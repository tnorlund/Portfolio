"""Unit tests for the Stage 3 dedup executor (dry-run by default)."""

from types import SimpleNamespace

from receipt_upload.dedup.apply import (
    ExecutionPlan,
    LabelAdd,
    execute,
    plan_operations,
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


def test_empty_plan_no_ops():
    plan = plan_operations([{"group_id": "g", "scope": "cross_image",
                             "survivor": "aaaa#1", "receipts_to_drop": [], "gap_fills": []}])
    spy = _SpyClient()
    rep = execute(plan, spy, apply=True)
    assert rep["labels_added"] == 0 and rep["receipts_deleted"] == 0
    assert "delete_receipt" not in spy.calls
