"""Unit tests for the OCR->DynamoDB migration rehearsal harness.

Pure Python: no Docker, no AWS. Synthetic BEFORE/AFTER snapshots are built with
the same DynamoSQLiteWriter the live path uses, so the schema matches exactly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts import local_analytics_cache as cache
from scripts import ocr_migration_rehearsal as reh

# --------------------------------------------------------------------------- #
# Wire-item builders (DynamoDB typed JSON)                                     #
# --------------------------------------------------------------------------- #


def word_item(image_id: str, rid: int, lid: int, wid: int, text: str) -> dict[str, Any]:
    return {
        "PK": {"S": f"IMAGE#{image_id}"},
        "SK": {"S": f"RECEIPT#{rid:05d}#LINE#{lid:05d}#WORD#{wid:05d}"},
        "TYPE": {"S": "RECEIPT_WORD"},
        "text": {"S": text},
    }


def label_item(
    image_id: str,
    rid: int,
    lid: int,
    wid: int,
    label: str,
    status: str = "VALID",
) -> dict[str, Any]:
    return {
        "PK": {"S": f"IMAGE#{image_id}"},
        "SK": {"S": (f"RECEIPT#{rid:05d}#LINE#{lid:05d}#WORD#{wid:05d}#LABEL#{label}")},
        "TYPE": {"S": "RECEIPT_WORD_LABEL"},
        "label": {"S": label},
        "validation_status": {"S": status},
    }


def other_item(pk: str, sk: str, payload: str = "x") -> dict[str, Any]:
    return {
        "PK": {"S": pk},
        "SK": {"S": sk},
        "TYPE": {"S": "OTHER"},
        "payload": {"S": payload},
    }


def write_snapshot(path: Path, items: list[dict[str, Any]]) -> Path:
    writer = cache.DynamoSQLiteWriter(path)
    writer.add(items)
    writer.finalize({"test": True})
    writer.connection.close()
    return path


# --------------------------------------------------------------------------- #
# SK parsing                                                                   #
# --------------------------------------------------------------------------- #


def test_parse_word_sk():
    assert reh.parse_word_sk("RECEIPT#00001#LINE#00002#WORD#00003") == (1, 2, 3)
    # A label SK is NOT a word SK.
    assert reh.parse_word_sk("RECEIPT#00001#LINE#00002#WORD#00003#LABEL#TOTAL") is None
    assert reh.parse_word_sk("RECEIPT#00001#LINE#00002") is None


def test_parse_label_sk():
    assert reh.parse_label_sk(
        "RECEIPT#00001#LINE#00002#WORD#00003#LABEL#GRAND_TOTAL"
    ) == (1, 2, 3, "GRAND_TOTAL")
    assert reh.parse_label_sk("RECEIPT#00001#LINE#00002#WORD#00003") is None


# --------------------------------------------------------------------------- #
# Row diff + blast radius                                                      #
# --------------------------------------------------------------------------- #


def test_diff_rows_detects_add_delete_mutate(tmp_path: Path):
    before = write_snapshot(
        tmp_path / "before.sqlite3",
        [
            word_item("img1", 1, 1, 1, "COFFEE"),  # will mutate (text re-read)
            word_item("img1", 1, 1, 2, "MILK"),  # will be deleted
        ],
    )
    after = write_snapshot(
        tmp_path / "after.sqlite3",
        [
            word_item("img1", 1, 1, 1, "COFEE"),  # mutated text
            word_item("img1", 1, 1, 3, "SUGAR"),  # added
        ],
    )
    diff = reh.diff_rows(reh.load_rows(before), reh.load_rows(after))
    assert len(diff.added) == 1
    assert len(diff.deleted) == 1
    assert len(diff.mutated) == 1
    assert diff.changed_pks == {"IMAGE#img1"}


def test_blast_radius_flags_out_of_scope(tmp_path: Path):
    before = write_snapshot(
        tmp_path / "before.sqlite3",
        [word_item("img1", 1, 1, 1, "A"), other_item("IMAGE#img2", "META", "v1")],
    )
    after = write_snapshot(
        tmp_path / "after.sqlite3",
        [word_item("img1", 1, 1, 1, "B"), other_item("IMAGE#img2", "META", "v2")],
    )
    diff = reh.diff_rows(reh.load_rows(before), reh.load_rows(after))
    # Only img1 is a migration target; img2 changed too -> violation.
    violations = reh.blast_radius_violations(diff.changed_pks, ["img1"])
    assert violations == ["IMAGE#img2"]
    # With both in scope, no violation.
    assert reh.blast_radius_violations(diff.changed_pks, ["img1", "img2"]) == []


# --------------------------------------------------------------------------- #
# Label preservation                                                           #
# --------------------------------------------------------------------------- #


def test_label_preservation_detects_dropped_label(tmp_path: Path):
    before = write_snapshot(
        tmp_path / "before.sqlite3",
        [
            word_item("img1", 1, 1, 1, "TOTAL"),
            label_item("img1", 1, 1, 1, "GRAND_TOTAL"),
        ],
    )
    # Re-OCR regenerated the word but did NOT re-apply the label.
    after = write_snapshot(
        tmp_path / "after.sqlite3",
        [word_item("img1", 1, 2, 5, "TOTAL")],
    )
    report = reh.check_label_preservation(
        reh.load_rows(before), reh.load_rows(after), ["img1"]
    )
    assert report.has_real_loss
    assert "img1" in report.lost_labels
    assert report.labels_before == 1
    assert report.labels_after == 0


def test_text_reread_is_churn_not_loss(tmp_path: Path):
    # Label preserved but on a word re-read to different text and new line/word id.
    before = write_snapshot(
        tmp_path / "before.sqlite3",
        [
            word_item("img1", 1, 1, 1, "$2.68"),
            label_item("img1", 1, 1, 1, "LINE_TOTAL"),
        ],
    )
    after = write_snapshot(
        tmp_path / "after.sqlite3",
        [
            word_item("img1", 1, 7, 9, "$2.66"),  # re-read text, new ids
            label_item("img1", 1, 7, 9, "LINE_TOTAL"),  # label re-applied
        ],
    )
    report = reh.check_label_preservation(
        reh.load_rows(before), reh.load_rows(after), ["img1"]
    )
    # No REAL loss (label+status count preserved) ...
    assert not report.has_real_loss
    # ... but the word-text-sensitive tuple changed -> reported as churn.
    assert "img1" in report.churn_only


def test_reassigned_ids_same_text_preserved(tmp_path: Path):
    # Same label + same word text, only line/word ids changed -> fully preserved.
    before = write_snapshot(
        tmp_path / "before.sqlite3",
        [
            word_item("img1", 1, 1, 1, "VISA"),
            label_item("img1", 1, 1, 1, "PAYMENT_METHOD"),
        ],
    )
    after = write_snapshot(
        tmp_path / "after.sqlite3",
        [
            word_item("img1", 1, 4, 2, "VISA"),
            label_item("img1", 1, 4, 2, "PAYMENT_METHOD"),
        ],
    )
    report = reh.check_label_preservation(
        reh.load_rows(before), reh.load_rows(after), ["img1"]
    )
    assert not report.has_real_loss
    assert not report.churn_only


def test_run_diff_verdict_fails_on_loss(tmp_path: Path):
    before = write_snapshot(
        tmp_path / "before.sqlite3",
        [
            word_item("img1", 1, 1, 1, "TOTAL"),
            label_item("img1", 1, 1, 1, "GRAND_TOTAL"),
        ],
    )
    after = write_snapshot(
        tmp_path / "after.sqlite3",
        [word_item("img1", 1, 1, 1, "TOTAL")],  # label dropped
    )
    report = reh.run_diff(before, after, ["img1"])
    assert report["ok"] is False
    assert report["labels"]["images_with_lost_labels"] == 1


def test_run_diff_verdict_passes_on_clean_reocr(tmp_path: Path):
    before = write_snapshot(
        tmp_path / "before.sqlite3",
        [
            word_item("img1", 1, 1, 1, "TOTAL"),
            label_item("img1", 1, 1, 1, "GRAND_TOTAL"),
        ],
    )
    after = write_snapshot(
        tmp_path / "after.sqlite3",
        [
            word_item("img1", 1, 3, 8, "TOTAL"),
            label_item("img1", 1, 3, 8, "GRAND_TOTAL"),
        ],
    )
    report = reh.run_diff(before, after, ["img1"])
    assert report["ok"] is True
    assert report["blast_radius"]["violations"] == []
    assert report["invariants"]["unchanged_targets"] == []


# --------------------------------------------------------------------------- #
# Fable review must-fixes                                                       #
# --------------------------------------------------------------------------- #


def test_churn_reported_even_with_other_real_loss(tmp_path: Path):
    # Label A is truly dropped; label B is re-applied on a re-read word. The
    # per-item churn filter must still report B (the old whole-receipt `if not
    # lost` test suppressed all churn whenever any loss existed).
    before = write_snapshot(
        tmp_path / "before.sqlite3",
        [
            word_item("img1", 1, 1, 1, "X"),
            label_item("img1", 1, 1, 1, "A"),
            word_item("img1", 1, 2, 2, "$1"),
            label_item("img1", 1, 2, 2, "B"),
        ],
    )
    after = write_snapshot(
        tmp_path / "after.sqlite3",
        [
            word_item("img1", 1, 5, 5, "$2"),  # B's word re-read, new ids
            label_item("img1", 1, 5, 5, "B"),
        ],
    )
    report = reh.check_label_preservation(
        reh.load_rows(before), reh.load_rows(after), ["img1"]
    )
    assert report.has_real_loss  # A
    churn_labels = {t[0][0] for t in report.churn_only["img1"]}
    assert "B" in churn_labels  # churn still surfaced despite A's loss
    assert "A" not in churn_labels  # A is real loss, not churn


def test_orphan_label_detected(tmp_path: Path):
    after = write_snapshot(
        tmp_path / "after.sqlite3",
        [
            word_item("img1", 1, 1, 1, "TOTAL"),  # a real word (not wiped)
            label_item("img1", 1, 9, 9, "GRAND_TOTAL"),  # points at absent word
        ],
    )
    orphans = reh.find_orphan_label_keys(reh.load_rows(after), ["img1"])
    assert ("img1", 1, 9, 9, "GRAND_TOTAL") in orphans


def test_run_diff_fails_on_orphan_label(tmp_path: Path):
    # Migration regenerated the word under new ids but left the OLD label row.
    before = write_snapshot(
        tmp_path / "before.sqlite3",
        [word_item("img1", 1, 1, 1, "T"), label_item("img1", 1, 1, 1, "GT")],
    )
    after = write_snapshot(
        tmp_path / "after.sqlite3",
        [
            word_item("img1", 1, 2, 2, "T"),  # new word
            label_item("img1", 1, 2, 2, "GT"),  # re-applied
            label_item("img1", 1, 1, 1, "GT"),  # stale orphan (word 1,1,1 gone)
        ],
    )
    report = reh.run_diff(before, after, ["img1"])
    assert report["ok"] is False
    assert report["invariants"]["orphan_labels"]


def test_wiped_receipt_detected(tmp_path: Path):
    # Receipt with words but NO labels, all words nuked -> label check is blind,
    # words-survive invariant catches it.
    before = write_snapshot(
        tmp_path / "before.sqlite3",
        [word_item("img1", 1, 1, 1, "A"), word_item("img1", 1, 1, 2, "B")],
    )
    after = write_snapshot(
        tmp_path / "after.sqlite3",
        [other_item("IMAGE#img1", "RECEIPT#00001", "meta-only")],  # words gone
    )
    assert "img1" in reh.wiped_images(
        reh.load_rows(before), reh.load_rows(after), ["img1"]
    )
    report = reh.run_diff(before, after, ["img1"])
    assert report["ok"] is False


def test_unchanged_target_flags_noop(tmp_path: Path):
    # Identical BEFORE/AFTER -> migration silently did nothing (or bypassed the
    # local endpoint and wrote to real dev). Empty diff must NOT pass.
    items = [word_item("img1", 1, 1, 1, "A"), label_item("img1", 1, 1, 1, "GT")]
    before = write_snapshot(tmp_path / "before.sqlite3", items)
    after = write_snapshot(tmp_path / "after.sqlite3", items)
    report = reh.run_diff(before, after, ["img1"])
    assert report["ok"] is False
    assert report["invariants"]["unchanged_targets"] == ["img1"]


def test_set_member_reorder_not_mutated(tmp_path: Path):
    def word_with_tags(order):
        return {
            "PK": {"S": "IMAGE#img1"},
            "SK": {"S": "RECEIPT#00001#LINE#00001#WORD#00001"},
            "TYPE": {"S": "RECEIPT_WORD"},
            "text": {"S": "A"},
            "tags": {"SS": order},
        }

    before = write_snapshot(tmp_path / "before.sqlite3", [word_with_tags(["y", "x"])])
    after = write_snapshot(tmp_path / "after.sqlite3", [word_with_tags(["x", "y"])])
    diff = reh.diff_rows(reh.load_rows(before), reh.load_rows(after))
    assert len(diff.mutated) == 0


def test_surplus_label_detected_id_collision(tmp_path: Path):
    # forgot-to-delete: the OLD label survives on a re-read word (so NO orphan),
    # and the label is ALSO re-applied to a new word -> 2 copies where 1 existed.
    # The orphan check is blind to this; the surplus check must catch it.
    before = write_snapshot(
        tmp_path / "before.sqlite3",
        [word_item("img1", 1, 1, 1, "A"), label_item("img1", 1, 1, 1, "GT")],
    )
    after = write_snapshot(
        tmp_path / "after.sqlite3",
        [
            word_item("img1", 1, 1, 1, "A2"),  # re-read; stale label still valid
            label_item("img1", 1, 1, 1, "GT"),  # stale label NOT deleted
            word_item("img1", 1, 2, 2, "B"),  # regenerated word
            label_item("img1", 1, 2, 2, "GT"),  # re-applied here too
        ],
    )
    report = reh.check_label_preservation(
        reh.load_rows(before), reh.load_rows(after), ["img1"]
    )
    assert report.has_surplus
    assert not report.has_real_loss
    assert reh.find_orphan_label_keys(reh.load_rows(after), ["img1"]) == []
    diff = reh.run_diff(before, after, ["img1"])
    assert diff["ok"] is False
    assert diff["labels"]["images_with_surplus_labels"] == 1


def test_targets_without_word_changes_flags_mixed_endpoint(tmp_path: Path):
    # Only a benign receipt-metadata row changed locally; the word/label writes
    # went to real dev (endpoint bypass). unchanged_targets is satisfied (a row
    # changed) but the stronger word-level check must flag the bypass.
    before = write_snapshot(
        tmp_path / "before.sqlite3",
        [
            word_item("img1", 1, 1, 1, "A"),
            label_item("img1", 1, 1, 1, "GT"),
            other_item("IMAGE#img1", "RECEIPT#00001", "v1"),
        ],
    )
    after = write_snapshot(
        tmp_path / "after.sqlite3",
        [
            word_item("img1", 1, 1, 1, "A"),
            label_item("img1", 1, 1, 1, "GT"),
            other_item("IMAGE#img1", "RECEIPT#00001", "v2"),  # only meta changed
        ],
    )
    diff = reh.diff_rows(reh.load_rows(before), reh.load_rows(after))
    assert reh.unchanged_targets(diff.changed_pks, ["img1"]) == []  # a row changed
    assert reh.targets_without_word_changes(diff, ["img1"]) == ["img1"]
    report = reh.run_diff(before, after, ["img1"])
    assert report["ok"] is False
    assert report["invariants"]["targets_without_word_changes"] == ["img1"]


# --------------------------------------------------------------------------- #
# Per-image aggregation (re-segmentation robustness)                           #
# --------------------------------------------------------------------------- #


def test_resegmentation_label_moved_between_receipts_preserved(tmp_path: Path):
    # Re-OCR re-segments the image: a label that was on receipt 1 is now on
    # receipt 2 (same word text). A per-receipt check would see loss-in-1 +
    # surplus-in-2; per-image pooling must read it as fully preserved.
    before = write_snapshot(
        tmp_path / "before.sqlite3",
        [
            word_item("img1", 1, 1, 1, "VISA"),
            label_item("img1", 1, 1, 1, "PAYMENT_METHOD"),
        ],
    )
    after = write_snapshot(
        tmp_path / "after.sqlite3",
        [
            word_item("img1", 2, 1, 1, "VISA"),  # now under receipt 2
            label_item("img1", 2, 1, 1, "PAYMENT_METHOD"),
        ],
    )
    report = reh.check_label_preservation(
        reh.load_rows(before), reh.load_rows(after), ["img1"]
    )
    assert not report.has_real_loss
    assert not report.has_surplus
    assert reh.run_diff(before, after, ["img1"])["ok"] is True


# --------------------------------------------------------------------------- #
# Backup / restore (rollback)                                                  #
# --------------------------------------------------------------------------- #


def test_compute_restore_plan(tmp_path: Path):
    backup = write_snapshot(
        tmp_path / "backup.sqlite3",
        [
            word_item("img1", 1, 1, 1, "OLD"),
            label_item("img1", 1, 1, 1, "GT"),
            word_item("img2", 1, 1, 1, "KEEP"),  # not a restore target
        ],
    )
    current = write_snapshot(
        tmp_path / "current.sqlite3",
        [
            word_item("img1", 1, 2, 2, "NEW"),  # migration-added -> delete
            word_item("img1", 1, 1, 1, "MUTATED"),  # same key -> overwritten by put
        ],
    )
    puts, deletes = reh.compute_restore_plan(
        reh.load_rows(backup), reh.load_rows(current), ["img1"]
    )
    put_keys = {(r.pk, r.sk) for r in puts}
    # every img1 backup row is re-put; img2 is out of scope
    assert ("IMAGE#img1", "RECEIPT#00001#LINE#00001#WORD#00001") in put_keys
    assert all(pk == "IMAGE#img1" for pk, _ in put_keys)
    # the migration-added row is deleted; the mutated same-key row is not
    assert deletes == [("IMAGE#img1", "RECEIPT#00001#LINE#00002#WORD#00002")]


def test_local_endpoint_guardrail():
    assert reh._is_local_endpoint("http://127.0.0.1:8000")
    assert reh._is_local_endpoint("http://localhost:8000")
    assert not reh._is_local_endpoint(None)
    assert not reh._is_local_endpoint("https://dynamodb.us-east-1.amazonaws.com")
    import pytest

    with pytest.raises(SystemExit):
        reh._make_client("https://dynamodb.us-east-1.amazonaws.com", None, False)
    with pytest.raises(SystemExit):
        reh._make_client(None, None, False)


# --------------------------------------------------------------------------- #
# Parked reconciliation + orphan delta                                         #
# --------------------------------------------------------------------------- #


def test_parked_reconciliation_clears_verdict(tmp_path: Path):
    # One label parked: (GT, VALID) -> (GT, NEEDS_REVIEW) on a re-read word.
    before = write_snapshot(
        tmp_path / "b.sqlite3",
        [
            word_item("img1", 1, 1, 1, "TOTAL"),
            label_item("img1", 1, 1, 1, "GT", status="VALID"),
        ],
    )
    after = write_snapshot(
        tmp_path / "a.sqlite3",
        [
            word_item("img1", 1, 2, 2, "TOTAI"),  # re-read word
            label_item("img1", 1, 2, 2, "GT", status="NEEDS_REVIEW"),  # parked
        ],
    )
    # Without reconciliation: loss + surplus -> FAIL.
    assert reh.run_diff(before, after, ["img1"])["ok"] is False
    # With the apply report vouching for 1 parked label -> PASS.
    report = reh.run_diff(before, after, ["img1"], expect_parked={"img1": 1})
    assert report["ok"] is True
    assert report["labels"]["reconciled_parked"] == 1


def test_parked_reconciliation_never_hides_real_loss(tmp_path: Path):
    # Two labels: one parked, one genuinely dropped. Reconciliation may cancel
    # only the parked pair; the drop must still FAIL.
    before = write_snapshot(
        tmp_path / "b.sqlite3",
        [
            word_item("img1", 1, 1, 1, "TOTAL"),
            label_item("img1", 1, 1, 1, "GT", status="VALID"),
            word_item("img1", 1, 2, 1, "VISA"),
            label_item("img1", 1, 2, 1, "PAYMENT", status="VALID"),  # dropped
        ],
    )
    after = write_snapshot(
        tmp_path / "a.sqlite3",
        [
            word_item("img1", 1, 3, 3, "TOTAI"),
            label_item("img1", 1, 3, 3, "GT", status="NEEDS_REVIEW"),  # parked
            word_item("img1", 1, 4, 1, "VISA"),  # word survives, label gone
        ],
    )
    report = reh.run_diff(before, after, ["img1"], expect_parked={"img1": 1})
    assert report["ok"] is False
    lost = report["labels"]["lost"]["img1"]
    assert any(lab == "PAYMENT" for ((lab, _st), _n) in lost)


def test_orphan_delta_ignores_pre_existing(tmp_path: Path):
    # img1 already has a dangling label BEFORE the migration. If the migration
    # neither fixes nor worsens it, the orphan invariant must not fail.
    pre_orphan = label_item("img1", 1, 9, 9, "GT")  # word (1,9,9) never exists
    before = write_snapshot(
        tmp_path / "b.sqlite3",
        [word_item("img1", 1, 1, 1, "A"), pre_orphan],
    )
    after = write_snapshot(
        tmp_path / "a.sqlite3",
        [word_item("img1", 1, 2, 2, "A"), pre_orphan],  # word re-keyed; orphan kept
    )
    report = reh.run_diff(before, after, ["img1"])
    assert report["invariants"]["orphan_labels"] == []
    assert report["invariants"]["pre_existing_orphans"] == 1
