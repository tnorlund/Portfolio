"""Unit tests for the Stage 3 dedup executor (dry-run by default)."""

import json

from receipt_upload.dedup.apply import (
    execute,
    plan_operations,
    rollback,
    summarize,
)

IMG_S = "11111111-1111-4111-8111-111111111111"  # survivor image
IMG_D = "22222222-2222-4222-8222-222222222222"  # dropped image


def _resolution(**kw):
    base = {
        "group_id": "g1",
        "scope": "cross_image",
        "survivor": f"{IMG_S}#2",
        "receipts_to_drop": [f"{IMG_D}#1"],
        "gap_fills": [
            {
                "locus": "4:3",
                "word_text": "$5.90",
                "label": "LINE_TOTAL",
                "from_member": f"{IMG_D}#1",
            },
        ],
    }
    base.update(kw)
    return base


def _it(image_id, sk, typ):
    return {
        "PK": {"S": f"IMAGE#{image_id}"},
        "SK": {"S": sk},
        "TYPE": {"S": typ},
    }


class FakeClient:
    """Fakes both the high-level helpers and the low-level boto3 client.

    ``subtree`` maps (image_id, receipt_id) -> list of raw items the query under
    that receipt's SK-prefix should return (may include a deliberately-wrong rid
    to exercise the precision filter).
    """

    table_name = "ReceiptsTable-test"

    def __init__(self, subtree=None, existing_labels=None, field_items=None):
        self.subtree = subtree or {}
        # existing_labels: {(pk, sk): raw_item} returned by get_item
        self.existing_labels = existing_labels or {}
        # field_items: {(image_id, rid): [items]} returned by the GSI1 query
        self.field_items = field_items or {}
        self.calls = []
        self.deleted_keys, self.put_items = [], []
        self.added_labels = []
        self._client = self

    def get_item(self, TableName, Key):
        self.calls.append("get_item")
        item = self.existing_labels.get((Key["PK"]["S"], Key["SK"]["S"]))
        return {"Item": item} if item else {}

    # high-level label writes
    def add_receipt_word_label(self, label):
        self.calls.append("add_receipt_word_label")
        self.added_labels.append(label)

    def update_receipt_word_label(self, label):
        self.calls.append("update_receipt_word_label")
        self.added_labels.append(label)

    # low-level boto3 surface
    def query(self, **kw):
        self.calls.append("query")
        vals = kw["ExpressionAttributeValues"]
        image_id = vals[":pk"]["S"].split("#", 1)[1]
        if kw.get("IndexName") == "GSI1":  # ReceiptField lookup
            rid = int(vals[":sk"]["S"].split("#")[1])
            return {
                "Items": self.field_items.get((image_id, rid), []),
                "LastEvaluatedKey": None,
            }
        # main table: broad RECEIPT# prefix -> all items for this image (all rids)
        items = [
            it
            for (img, _r), its in self.subtree.items()
            if img == image_id
            for it in its
        ]
        return {"Items": items, "LastEvaluatedKey": None}

    def delete_item(self, TableName, Key):
        self.calls.append("delete_item")
        self.deleted_keys.append(Key)

    def put_item(self, TableName, Item):
        self.calls.append("put_item")
        self.put_items.append(Item)


def _full_subtree(image_id, rid):
    """A realistic receipt subtree incl. the child types the old cascade missed."""
    p = f"RECEIPT#{rid:05d}"
    return [
        _it(image_id, p, "RECEIPT"),
        _it(image_id, f"{p}#LINE#00001", "RECEIPT_LINE"),
        _it(image_id, f"{p}#LINE#00001#WORD#00001", "RECEIPT_WORD"),
        _it(
            image_id,
            f"{p}#LINE#00001#WORD#00001#LETTER#00001",
            "RECEIPT_LETTER",
        ),
        _it(
            image_id,
            f"{p}#LINE#00001#WORD#00001#LABEL#TAX",
            "RECEIPT_WORD_LABEL",
        ),
        _it(image_id, f"{p}#PLACE", "RECEIPT_PLACE"),
        _it(image_id, f"{p}#SUMMARY", "RECEIPT_SUMMARY"),
        _it(
            image_id,
            f"{p}#VALIDATION_CATEGORY#x",
            "RECEIPT_VALIDATION_CATEGORY",
        ),
    ]


# --------------------------------------------------------------------------- #
# pure planning
# --------------------------------------------------------------------------- #
def test_plan_parses_survivor_locus_and_drops():
    plan = plan_operations([_resolution()])
    assert len(plan.label_adds) == 1 and len(plan.receipt_drops) == 1
    a = plan.label_adds[0]
    assert (
        a.image_id == IMG_S
        and a.receipt_id == 2
        and a.line_id == 4
        and a.word_id == 3
    )
    assert a.label == "LINE_TOTAL"
    d = plan.receipt_drops[0]
    assert d.image_id == IMG_D and d.receipt_id == 1


def test_summarize_counts():
    s = summarize(plan_operations([_resolution()]))
    assert s == {
        "labels_to_add": 1,
        "receipts_to_drop": 1,
        "images_touched": 2,
    }


# --------------------------------------------------------------------------- #
# dry-run
# --------------------------------------------------------------------------- #
def test_dry_run_performs_no_io():
    cli = FakeClient({(IMG_D, 1): _full_subtree(IMG_D, 1)})
    rep = execute(plan_operations([_resolution()]), cli, apply=False)
    assert rep["dry_run"] is True
    assert rep["labels_added"] == 1 and rep["receipts_deleted"] == 1
    assert cli.calls == []  # NOTHING called in dry-run


def test_dry_run_needs_no_client():
    rep = execute(plan_operations([_resolution()]), None, apply=False)
    assert rep["receipts_deleted"] == 1 and rep["labels_added"] == 1


# --------------------------------------------------------------------------- #
# apply: complete + precise subtree delete
# --------------------------------------------------------------------------- #
def test_apply_deletes_full_subtree_and_adds_label(tmp_path):
    cli = FakeClient({(IMG_D, 1): _full_subtree(IMG_D, 1)})
    bk = tmp_path / "restore.json"
    rep = execute(
        plan_operations([_resolution()]), cli, apply=True, backup_path=str(bk)
    )
    assert rep["receipts_deleted"] == 1
    # all 8 subtree items deleted; 7 counted as children (8 minus the RECEIPT)
    assert len(cli.deleted_keys) == 8 and rep["children_deleted"] == 7
    assert "add_receipt_word_label" in cli.calls
    # the child types the OLD cascade missed are now deleted
    deleted_sks = {k["SK"]["S"] for k in cli.deleted_keys}
    assert any("PLACE" in s for s in deleted_sks)
    assert any("SUMMARY" in s for s in deleted_sks)
    assert any("VALIDATION_CATEGORY" in s for s in deleted_sks)


def test_apply_requires_backup_path():
    import pytest

    cli = FakeClient({(IMG_D, 1): _full_subtree(IMG_D, 1)})
    with pytest.raises(ValueError):
        execute(
            plan_operations([_resolution()]), cli, apply=True, backup_path=None
        )
    assert cli.deleted_keys == []  # nothing deleted when backup is missing


def test_apply_never_deletes_parent_image(tmp_path):
    cli = FakeClient({(IMG_D, 1): _full_subtree(IMG_D, 1)})
    execute(
        plan_operations([_resolution()]),
        cli,
        apply=True,
        backup_path=str(tmp_path / "b.json"),
    )
    # nothing with SK == "IMAGE" (the parent image entity) is ever deleted
    assert all(k["SK"]["S"] != "IMAGE" for k in cli.deleted_keys)


def test_subtree_filter_excludes_sibling_receipt(tmp_path):
    # query returns a stray sibling item (rid 10 padded) — the rid filter drops it
    poisoned = _full_subtree(IMG_D, 1) + [
        _it(
            IMG_D, "RECEIPT#00010#PLACE", "RECEIPT_PLACE"
        ),  # different receipt!
    ]
    cli = FakeClient({(IMG_D, 1): poisoned})
    execute(
        plan_operations([_resolution()]),
        cli,
        apply=True,
        backup_path=str(tmp_path / "b.json"),
    )
    deleted_sks = {k["SK"]["S"] for k in cli.deleted_keys}
    assert "RECEIPT#00010#PLACE" not in deleted_sks  # sibling never touched
    assert len(cli.deleted_keys) == 8


def test_leftover_only_subtree_no_receipt_entity(tmp_path):
    # re-run case: RECEIPT already gone, only orphaned children remain
    leftovers = [
        _it(IMG_D, "RECEIPT#00001#PLACE", "RECEIPT_PLACE"),
        _it(IMG_D, "RECEIPT#00001#SUMMARY", "RECEIPT_SUMMARY"),
    ]
    cli = FakeClient({(IMG_D, 1): leftovers})
    rep = execute(
        plan_operations([_resolution(gap_fills=[])]),
        cli,
        apply=True,
        backup_path=str(tmp_path / "b.json"),
    )
    assert rep["receipts_deleted"] == 0  # no RECEIPT entity present
    assert rep["children_deleted"] == 2  # but the leftovers are swept
    assert len(cli.deleted_keys) == 2


def test_unpadded_sk_and_field_partition_are_swept(tmp_path):
    # ChatGPTValidation uses an UNPADDED rid SK; ReceiptField lives in the FIELD#
    # partition (found via GSI1). Both must be backed up + deleted.
    subtree = _full_subtree(IMG_D, 1) + [
        # unpadded validation record under IMAGE# (RECEIPT#1#... not RECEIPT#00001)
        _it(
            IMG_D,
            "RECEIPT#1#ANALYSIS#VALIDATION#CHATGPT#t",
            "RECEIPT_CHATGPT_VALIDATION",
        ),
    ]
    field = {
        "PK": {"S": "FIELD#MERCHANT_NAME"},
        "SK": {"S": "RECEIPT#00001#FIELD#MERCHANT_NAME"},
        "TYPE": {"S": "RECEIPT_FIELD"},
    }
    cli = FakeClient({(IMG_D, 1): subtree}, field_items={(IMG_D, 1): [field]})
    execute(
        plan_operations([_resolution()]),
        cli,
        apply=True,
        backup_path=str(tmp_path / "b.json"),
    )
    deleted_sks = {k["SK"]["S"] for k in cli.deleted_keys}
    assert (
        "RECEIPT#1#ANALYSIS#VALIDATION#CHATGPT#t" in deleted_sks
    )  # unpadded caught
    assert "RECEIPT#00001#FIELD#MERCHANT_NAME" in deleted_sks  # FIELD# caught
    deleted_pks = {k["PK"]["S"] for k in cli.deleted_keys}
    assert "FIELD#MERCHANT_NAME" in deleted_pks  # other partition


def test_gapfill_failure_skips_its_matching_drop(tmp_path):
    # if the VALID gap-fill from the dropped receipt fails to write, the drop is
    # SKIPPED (so the label isn't lost) and surfaced as an error.
    from receipt_dynamo.data.shared_exceptions import ReceiptDynamoError

    class FailLabelClient(FakeClient):
        def add_receipt_word_label(self, label):
            raise ReceiptDynamoError("write failed")

        def update_receipt_word_label(self, label):
            raise ReceiptDynamoError("update failed")

    cli = FailLabelClient({(IMG_D, 1): _full_subtree(IMG_D, 1)})
    rep = execute(
        plan_operations([_resolution()]),
        cli,
        apply=True,
        backup_path=str(tmp_path / "b.json"),
    )
    assert f"{IMG_D}#1" in rep["skipped_drops"]  # the source drop was skipped
    assert rep["receipts_deleted"] == 0  # nothing deleted
    assert cli.deleted_keys == []
    assert rep["errors"]  # surfaced


# --------------------------------------------------------------------------- #
# backup + rollback
# --------------------------------------------------------------------------- #
def test_backup_written_before_mutation(tmp_path):
    bk = tmp_path / "restore.json"
    cli = FakeClient({(IMG_D, 1): _full_subtree(IMG_D, 1)})
    rep = execute(
        plan_operations([_resolution()]), cli, apply=True, backup_path=str(bk)
    )
    assert rep["backup_path"] == str(bk)
    data = json.loads(bk.read_text())
    assert len(data["deleted_items"]) == 8  # full subtree captured
    assert len(data["added_label_keys"]) == 1
    assert data["table"] == "ReceiptsTable-test"


def test_overwritten_label_is_backed_up_and_restored(tmp_path):
    # a gap-fill key already holds a label (re-run / concurrent write): capture it
    # so rollback restores the original instead of deleting it.
    from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

    lbl = ReceiptWordLabel(
        image_id=IMG_S,
        receipt_id=2,
        line_id=4,
        word_id=3,
        label="LINE_TOTAL",
        reasoning="pre-existing",
        timestamp_added="2026-01-01T00:00:00+00:00",
    )
    key = lbl.key
    pre = {
        **key,
        "TYPE": {"S": "RECEIPT_WORD_LABEL"},
        "validation_status": {"S": "VALID"},
    }
    bk = tmp_path / "restore.json"
    cli = FakeClient(
        {(IMG_D, 1): _full_subtree(IMG_D, 1)},
        existing_labels={(key["PK"]["S"], key["SK"]["S"]): pre},
    )
    execute(
        plan_operations([_resolution()]), cli, apply=True, backup_path=str(bk)
    )
    data = json.loads(bk.read_text())
    assert len(data["overwritten_labels"]) == 1  # captured before overwrite

    restore = FakeClient()
    rep = rollback(str(bk), restore)
    assert rep["restored_labels"] == 1 and pre in restore.put_items


def test_rollback_reputs_items_and_removes_added_labels(tmp_path):
    bk = tmp_path / "restore.json"
    cli = FakeClient({(IMG_D, 1): _full_subtree(IMG_D, 1)})
    execute(
        plan_operations([_resolution()]), cli, apply=True, backup_path=str(bk)
    )

    restore = FakeClient()
    rep = rollback(str(bk), restore)
    assert rep["restored_items"] == 8 and rep["removed_labels"] == 1
    assert len(restore.put_items) == 8  # full subtree re-put
    assert len(restore.deleted_keys) == 1  # added label removed


def test_empty_plan_no_ops(tmp_path):
    plan = plan_operations(
        [
            {
                "group_id": "g",
                "scope": "cross_image",
                "survivor": f"{IMG_S}#1",
                "receipts_to_drop": [],
                "gap_fills": [],
            }
        ]
    )
    cli = FakeClient()
    rep = execute(plan, cli, apply=True, backup_path=str(tmp_path / "b.json"))
    assert rep["labels_added"] == 0 and rep["receipts_deleted"] == 0
    assert cli.deleted_keys == []
