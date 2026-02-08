"""Tests for evaluator_dedup_viz_cache."""

from __future__ import annotations

import json
import os
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from receipt_langsmith.spark.evaluator_dedup_viz_cache import (
    _build_summary,
    build_dedup_cache,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REQUIRED_RECEIPT_FIELDS = {
    "image_id",
    "receipt_id",
    "merchant_name",
    "trace_id",
    "dedup_stats",
    "resolutions",
    "summary",
}

REQUIRED_RESOLUTION_FIELDS = {
    "line_id",
    "word_id",
    "word_text",
    "current_label",
    "currency_decision",
    "currency_confidence",
    "metadata_decision",
    "metadata_confidence",
    "winner",
    "resolution_reason",
    "applied_label",
}


def _make_resolution(
    *,
    line_id: int = 0,
    word_id: int = 0,
    word_text: str = "10.99",
    current_label: str = "LINE_TOTAL",
    winner: str = "currency",
    resolution_reason: str = "higher_confidence",
) -> dict:
    return {
        "line_id": line_id,
        "word_id": word_id,
        "word_text": word_text,
        "current_label": current_label,
        "currency_decision": "VALID",
        "currency_confidence": "HIGH",
        "metadata_decision": "INVALID",
        "metadata_confidence": "LOW",
        "winner": winner,
        "resolution_reason": resolution_reason,
        "applied_label": current_label,
    }


def _write_parquet(rows: list[dict], directory: str) -> None:
    """Write rows to a parquet file in *directory*."""
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, os.path.join(directory, "traces.parquet"))


def _root_row(
    *,
    trace_id: str = "trace-1",
    image_id: str = "img-abc",
    receipt_id: int = 0,
    merchant_name: str = "Test Store",
) -> dict:
    return {
        "id": trace_id,
        "trace_id": trace_id,
        "parent_run_id": None,
        "name": "ReceiptEvaluation",
        "run_type": "chain",
        "status": "success",
        "is_root": True,
        "extra": json.dumps({
            "metadata": {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant_name": merchant_name,
            }
        }),
        "inputs": "{}",
        "outputs": "{}",
    }


def _phase1_row(
    *,
    trace_id: str = "trace-1",
    inputs: dict | None = None,
    outputs: dict | None = None,
) -> dict:
    if inputs is None:
        inputs = {
            "currency_invalid_count": 2,
            "metadata_invalid_count": 1,
            "overlapping_words": 3,
            "conflicting_words": 2,
        }
    if outputs is None:
        outputs = {
            "resolutions": [
                _make_resolution(
                    line_id=1,
                    word_id=0,
                    winner="currency",
                    resolution_reason="higher_confidence",
                ),
                _make_resolution(
                    line_id=2,
                    word_id=1,
                    current_label="TAX",
                    winner="metadata",
                    resolution_reason="financial_label_priority",
                ),
            ],
            "total_corrections_applied": 2,
            "dedup_removed": 1,
            "resolution_strategy": "confidence_priority",
        }
    return {
        "id": "span-phase1",
        "trace_id": trace_id,
        "parent_run_id": trace_id,
        "name": "apply_phase1_corrections",
        "run_type": "chain",
        "status": "success",
        "is_root": False,
        "extra": "{}",
        "inputs": json.dumps(inputs),
        "outputs": json.dumps(outputs),
    }


# ---------------------------------------------------------------------------
# Test: receipt structure has required fields
# ---------------------------------------------------------------------------


class TestReceiptStructure:
    """build_dedup_cache produces receipts with all required fields."""

    def test_receipt_has_required_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_parquet(
                [_root_row(), _phase1_row()],
                tmpdir,
            )
            results = build_dedup_cache(tmpdir)

        assert len(results) == 1
        assert REQUIRED_RECEIPT_FIELDS <= set(results[0].keys())

    def test_no_phase1_span_has_null_dedup_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_parquet([_root_row()], tmpdir)
            results = build_dedup_cache(tmpdir)

        assert len(results) == 1
        assert results[0]["dedup_stats"] is None
        assert results[0]["resolutions"] == []

    def test_metadata_extracted_correctly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_parquet(
                [
                    _root_row(
                        image_id="img-xyz",
                        receipt_id=42,
                        merchant_name="Coffee Shop",
                    ),
                    _phase1_row(),
                ],
                tmpdir,
            )
            results = build_dedup_cache(tmpdir)

        r = results[0]
        assert r["image_id"] == "img-xyz"
        assert r["receipt_id"] == 42
        assert r["merchant_name"] == "Coffee Shop"


# ---------------------------------------------------------------------------
# Test: resolutions have required fields when conflicts exist
# ---------------------------------------------------------------------------


class TestResolutionFields:
    """Resolutions include all required fields when conflicts are present."""

    def test_resolutions_have_required_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_parquet(
                [_root_row(), _phase1_row()],
                tmpdir,
            )
            results = build_dedup_cache(tmpdir)

        resolutions = results[0]["resolutions"]
        assert len(resolutions) == 2
        for res in resolutions:
            assert REQUIRED_RESOLUTION_FIELDS <= set(res.keys())

    def test_no_conflicts_empty_resolutions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_parquet(
                [
                    _root_row(),
                    _phase1_row(
                        inputs={
                            "currency_invalid_count": 0,
                            "metadata_invalid_count": 0,
                            "overlapping_words": 0,
                            "conflicting_words": 0,
                        },
                        outputs={
                            "resolutions": [],
                            "total_corrections_applied": 0,
                            "dedup_removed": 0,
                            "resolution_strategy": "confidence_priority",
                        },
                    ),
                ],
                tmpdir,
            )
            results = build_dedup_cache(tmpdir)

        assert results[0]["resolutions"] == []
        assert results[0]["summary"]["has_conflicts"] is False


# ---------------------------------------------------------------------------
# Test: summary breakdown counts match resolution list
# ---------------------------------------------------------------------------


class TestSummaryBreakdown:
    """Summary breakdown counters agree with the resolution list."""

    def test_resolution_breakdown_matches(self):
        resolutions = [
            _make_resolution(resolution_reason="higher_confidence"),
            _make_resolution(resolution_reason="higher_confidence"),
            _make_resolution(resolution_reason="financial_label_priority"),
        ]
        summary = _build_summary(resolutions)

        assert summary["resolution_breakdown"]["higher_confidence"] == 2
        assert summary["resolution_breakdown"]["financial_label_priority"] == 1
        assert summary["resolution_breakdown"]["currency_priority_default"] == 0

    def test_winner_breakdown_matches(self):
        resolutions = [
            _make_resolution(winner="currency"),
            _make_resolution(winner="currency"),
            _make_resolution(winner="metadata"),
        ]
        summary = _build_summary(resolutions)

        assert summary["winner_breakdown"]["currency"] == 2
        assert summary["winner_breakdown"]["metadata"] == 1

    def test_labels_affected(self):
        resolutions = [
            _make_resolution(current_label="LINE_TOTAL"),
            _make_resolution(current_label="TAX"),
            _make_resolution(current_label="LINE_TOTAL"),
        ]
        summary = _build_summary(resolutions)
        assert sorted(summary["labels_affected"]) == ["LINE_TOTAL", "TAX"]

    def test_empty_resolutions_summary(self):
        summary = _build_summary([])
        assert summary["has_conflicts"] is False
        assert summary["resolution_breakdown"]["higher_confidence"] == 0
        assert summary["winner_breakdown"]["currency"] == 0
        assert summary["labels_affected"] == []


# ---------------------------------------------------------------------------
# Test: integration - full round trip
# ---------------------------------------------------------------------------


class TestFullRoundTrip:
    """End-to-end test with multiple receipts."""

    def test_multiple_receipts(self):
        rows = [
            _root_row(trace_id="t1", image_id="img-1", receipt_id=0),
            _phase1_row(trace_id="t1"),
            _root_row(
                trace_id="t2",
                image_id="img-2",
                receipt_id=1,
                merchant_name="Other",
            ),
            # t2 has no phase1 span
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_parquet(rows, tmpdir)
            results = build_dedup_cache(tmpdir)

        assert len(results) == 2

        r1 = next(r for r in results if r["trace_id"] == "t1")
        r2 = next(r for r in results if r["trace_id"] == "t2")

        assert r1["dedup_stats"] is not None
        assert len(r1["resolutions"]) == 2
        assert r1["summary"]["has_conflicts"] is True

        assert r2["dedup_stats"] is None
        assert r2["resolutions"] == []
        assert r2["summary"]["has_conflicts"] is False

    def test_dedup_stats_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_parquet(
                [_root_row(), _phase1_row()],
                tmpdir,
            )
            results = build_dedup_cache(tmpdir)

        stats = results[0]["dedup_stats"]
        assert stats["currency_invalid_count"] == 2
        assert stats["metadata_invalid_count"] == 1
        assert stats["overlapping_words"] == 3
        assert stats["conflicting_words"] == 2
        assert stats["dedup_removed"] == 1
        assert stats["total_corrections_applied"] == 2
        assert stats["resolution_strategy"] == "confidence_priority"


# ---------------------------------------------------------------------------
# Test: write sample outputs (smoke test for JSON serialisation)
# ---------------------------------------------------------------------------


class TestWriteSampleOutputs:
    """Verify that outputs are JSON-serialisable."""

    def test_json_serialisable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_parquet(
                [_root_row(), _phase1_row()],
                tmpdir,
            )
            results = build_dedup_cache(tmpdir)

        serialised = json.dumps(results, default=str)
        parsed = json.loads(serialised)
        assert len(parsed) == 1
        assert parsed[0]["image_id"] == "img-abc"

    def test_write_to_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_parquet(
                [_root_row(), _phase1_row()],
                tmpdir,
            )
            results = build_dedup_cache(tmpdir)

            out_path = os.path.join(tmpdir, "sample_output.json")
            with open(out_path, "w") as f:
                json.dump(results, f, default=str, indent=2)

            with open(out_path) as f:
                loaded = json.load(f)

        assert len(loaded) == 1
        assert loaded[0]["summary"]["has_conflicts"] is True
