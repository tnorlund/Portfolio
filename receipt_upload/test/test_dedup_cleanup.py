"""Unit tests for Stage 4 orphaned-image cleanup (dry-run + guard + rollback)."""

from types import SimpleNamespace

from receipt_upload.dedup.cleanup_images import (
    execute_cleanup,
    image_s3_targets,
    plan_image_cleanup,
    rollback_cleanup,
    summarize,
)


def _img(image_id, **kw):
    d = dict(image_id=image_id, raw_s3_bucket="rawb", raw_s3_key="raw/x.png",
             cdn_s3_bucket="cdnb")
    d.update(kw)
    return SimpleNamespace(**d)


def _item(t):
    return {"PK": {"S": "IMAGE#x"}, "SK": {"S": t}, "TYPE": {"S": t}}


class FakeDynamo:
    table_name = "T"

    def __init__(self, items_by_image):
        self.items_by_image = items_by_image
        self.deleted_images = []
        self.put_items = []
        self._client = self

    def query(self, **kw):
        iid = kw["ExpressionAttributeValues"][":pk"]["S"].split("#", 1)[1]
        return {"Items": self.items_by_image.get(iid, []), "LastEvaluatedKey": None}

    def delete_image_details(self, image_id):
        self.deleted_images.append(image_id)
        return {"IMAGE": 1, "LINE": 2}

    def put_item(self, TableName, Item):
        self.put_items.append(Item)


class FakeS3:
    def __init__(self, missing=()):
        self.deleted, self.uploaded, self.missing = [], [], set(missing)

    def download_file(self, bucket, key, local):
        if key in self.missing:
            raise Exception("404")
        with open(local, "w") as f:
            f.write("obj")

    def delete_object(self, Bucket, Key):
        self.deleted.append((Bucket, Key))

    def upload_file(self, local, bucket, key):
        self.uploaded.append((bucket, key))


def test_s3_targets_raw_plus_cdn_variants_deduped():
    img = _img("a", cdn_s3_key="assets/a.jpg", cdn_webp_s3_key="assets/a.webp",
               cdn_thumbnail_s3_key="assets/a_thumb.jpg")
    keys = {o.key for o in image_s3_targets(img)}
    assert keys == {"raw/x.png", "assets/a.jpg", "assets/a.webp", "assets/a_thumb.jpg"}


def test_plan_refuses_images_that_still_have_receipts():
    items = {"a": [_item("IMAGE"), _item("LINE")],          # orphaned
             "b": [_item("IMAGE"), _item("RECEIPT")]}        # NOT orphaned
    dyn = FakeDynamo(items)
    cleanups, refused = plan_image_cleanup(dyn, {"a": _img("a"), "b": _img("b")}, ["a", "b"])
    assert [c.image_id for c in cleanups] == ["a"]
    assert refused and refused[0]["image_id"] == "b" and refused[0]["receipt_count"] == 1


def test_dry_run_performs_no_io():
    items = {"a": [_item("IMAGE")]}
    dyn = FakeDynamo(items)
    cleanups, _ = plan_image_cleanup(dyn, {"a": _img("a", cdn_s3_key="assets/a.jpg")}, ["a"])
    rep = execute_cleanup(cleanups, dyn, FakeS3(), apply=False)
    assert rep["dry_run"] and rep["images_deleted"] == 1
    assert dyn.deleted_images == []  # nothing deleted in dry-run


def test_execute_backs_up_then_deletes(tmp_path):
    items = {"a": [_item("IMAGE"), _item("LINE")]}
    dyn = FakeDynamo(items)
    cleanups, _ = plan_image_cleanup(dyn, {"a": _img("a", cdn_s3_key="assets/a.jpg")}, ["a"])
    s3 = FakeS3()
    rep = execute_cleanup(cleanups, dyn, s3, apply=True, backup_dir=str(tmp_path))
    assert rep["images_deleted"] == 1 and rep["s3_deleted"] == 2  # raw + cdn
    assert dyn.deleted_images == ["a"]
    assert ("rawb", "raw/x.png") in s3.deleted and ("cdnb", "assets/a.jpg") in s3.deleted
    assert (tmp_path / "a.dynamo.json").exists() and (tmp_path / "a.s3.json").exists()


def test_execute_requires_backup_dir():
    import pytest
    with pytest.raises(ValueError):
        execute_cleanup([], FakeDynamo({}), FakeS3(), apply=True, backup_dir=None)


def test_missing_s3_object_is_counted_not_fatal(tmp_path):
    items = {"a": [_item("IMAGE")]}
    dyn = FakeDynamo(items)
    cleanups, _ = plan_image_cleanup(dyn, {"a": _img("a", cdn_s3_key="assets/gone.jpg")}, ["a"])
    s3 = FakeS3(missing={"assets/gone.jpg"})
    rep = execute_cleanup(cleanups, dyn, s3, apply=True, backup_dir=str(tmp_path))
    assert rep["s3_missing"] == 1 and rep["images_deleted"] == 1


def test_rollback_restores_dynamo_and_s3(tmp_path):
    items = {"a": [_item("IMAGE"), _item("LINE")]}
    dyn = FakeDynamo(items)
    cleanups, _ = plan_image_cleanup(dyn, {"a": _img("a", cdn_s3_key="assets/a.jpg")}, ["a"])
    s3 = FakeS3()
    execute_cleanup(cleanups, dyn, s3, apply=True, backup_dir=str(tmp_path))

    dyn2, s3_2 = FakeDynamo({}), FakeS3()
    rep = rollback_cleanup(str(tmp_path), dyn2, s3_2)
    assert rep["items_restored"] == 2          # IMAGE + LINE re-put
    assert rep["s3_restored"] == 2             # raw + cdn re-uploaded
    assert len(dyn2.put_items) == 2 and len(s3_2.uploaded) == 2
