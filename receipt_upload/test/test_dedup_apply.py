"""Unit tests for the Stage 3 dedup executor (dry-run by default)."""

from types import SimpleNamespace

import json

from receipt_upload.dedup.apply import (
    ExecutionPlan,
    LabelAdd,
    execute,
    plan_operations,
    rollback,
    summarize,
)


def _resolution(**kw):
    base = {
        "group_id": "g1",
        "scope": "cross_image",
        "survivor": "11111111-1111-4111-8111-111111111111#2",
        "receipts_to_drop": ["22222222-2222-4222-8222-222222222222#1"],
        "gap_fills": [
            {"locus": "4:3", "word_text": "$5.90", "label": "LINE_TOTAL",
             "from_member": "22222222-2222-4222-8222-222222222222#1"},
        ],
    }
    base.update(kw)
    return base


def test_plan_parses_survivor_locus_and_drops():
    plan = plan_operations([_resolution()])
    assert len(plan.label_adds) == 1 and len(plan.receipt_drops) == 1
    a = plan.label_adds[0]
    assert a.image_id == "11111111-1111-4111-8111-111111111111"
    assert a.receipt_id == 2 and a.line_id == 4 and a.word_id == 3
    assert a.label == "LINE_TOTAL"
    d = plan.receipt_drops[0]
    assert d.image_id == "22222222-2222-4222-8222-222222222222" and d.receipt_id == 1


def test_summarize_counts():
    s = summarize(plan_operations([_resolution()]))
    assert s == {"labels_to_add": 1, "receipts_to_drop": 1, "images_touched": 2}


class _SpyClient:
    """Records every method call so we can assert dry-run touches nothing."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append(name)
            if name == "get_receipt_details":
                return SimpleNamespace(receipt=object(), labels=[], words=[], lines=[], letters=[])
            if name == "list_receipt_letters":
                return ([], None)
            return None
        return rec


def test_dry_run_performs_no_io():
    spy = _SpyClient()
    rep = execute(plan_operations([_resolution()]), spy, apply=False)
    assert rep["dry_run"] is True
    assert rep["labels_added"] == 1 and rep["receipts_deleted"] == 1
    assert spy.calls == []  # NOTHING called in dry-run


def test_dry_run_needs_no_client():
    rep = execute(plan_operations([_resolution()]), None, apply=False)
    assert rep["receipts_deleted"] == 1 and rep["labels_added"] == 1


def test_apply_writes_label_and_deletes_receipt_not_image():
    spy = _SpyClient()
    rep = execute(plan_operations([_resolution()]), spy, apply=True)
    assert rep["dry_run"] is False
    assert "add_receipt_word_label" in spy.calls
    assert "delete_receipt" in spy.calls
    assert "delete_image" not in spy.calls  # parent image must never be deleted
    assert rep["labels_added"] == 1 and rep["receipts_deleted"] == 1


class _Ent:
    """Fake entity with to_item()/key, mimicking a receipt_dynamo entity."""

    def __init__(self, pk, sk):
        self._pk, self._sk = pk, sk

    def to_item(self):
        return {"PK": {"S": self._pk}, "SK": {"S": self._sk}, "TYPE": {"S": "X"}}

    @property
    def key(self):
        return {"PK": {"S": self._pk}, "SK": {"S": self._sk}}


class _BackupClient:
    """Fake client that returns real entities and records raw put/delete."""

    table_name = "ReceiptsTable-test"

    def __init__(self):
        self.calls = []
        self.put_items, self.deleted_keys = [], []
        self._client = self  # so dynamo._client.put_item works

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append(name)
            if name == "get_receipt_details":
                img = a[0]
                return SimpleNamespace(
                    receipt=_Ent(f"IMAGE#{img}", "RECEIPT#1"),
                    labels=[_Ent(f"IMAGE#{img}", "RECEIPT#1#LABEL#A")],
                    words=[_Ent(f"IMAGE#{img}", "RECEIPT#1#WORD#1")],
                    lines=[], letters=[],
                )
            if name == "list_receipt_letters":
                return ([], None)
            if name == "get_receipt_metadata":
                return None
            return None
        return rec

    # explicit low-level handles (so they aren't shadowed by __getattr__)
    def put_item(self, TableName, Item):
        self.put_items.append(Item)

    def delete_item(self, TableName, Key):
        self.deleted_keys.append(Key)


def test_backup_written_before_mutation(tmp_path):
    bk = tmp_path / "restore.json"
    cli = _BackupClient()
    rep = execute(plan_operations([_resolution()]), cli, apply=True, backup_path=str(bk))
    assert rep["backup_path"] == str(bk)
    data = json.loads(bk.read_text())
    # 1 receipt + 1 label + 1 word = 3 deleted items captured
    assert len(data["deleted_items"]) == 3
    assert len(data["added_label_keys"]) == 1  # the gap-fill label key
    assert data["table"] == "ReceiptsTable-test"


def test_rollback_reputs_items_and_removes_added_labels(tmp_path):
    bk = tmp_path / "restore.json"
    cli = _BackupClient()
    execute(plan_operations([_resolution()]), cli, apply=True, backup_path=str(bk))

    restore_cli = _BackupClient()
    rep = rollback(str(bk), restore_cli)
    assert rep["restored_items"] == 3 and rep["removed_labels"] == 1
    assert len(restore_cli.put_items) == 3      # deleted items re-put
    assert len(restore_cli.deleted_keys) == 1   # added label removed


def test_empty_plan_no_ops():
    plan = plan_operations([{"group_id": "g", "scope": "cross_image",
                             "survivor": "aaaa#1", "receipts_to_drop": [], "gap_fills": []}])
    spy = _SpyClient()
    rep = execute(plan, spy, apply=True)
    assert rep["labels_added"] == 0 and rep["receipts_deleted"] == 0
    assert "delete_receipt" not in spy.calls
