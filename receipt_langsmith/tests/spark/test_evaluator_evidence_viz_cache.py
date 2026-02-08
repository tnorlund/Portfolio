"""Tests for evaluator_evidence_viz_cache helper."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from receipt_langsmith.spark.evaluator_evidence_viz_cache import (
    build_evidence_cache,
    _build_issue_entry,
    _build_summary,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

EVIDENCE_ITEMS = [
    {
        "word_text": "5.99",
        "similarity_score": 0.92,
        "label_valid": True,
        "evidence_source": "merchant_history",
        "is_same_merchant": True,
    },
    {
        "word_text": "6.49",
        "similarity_score": 0.87,
        "label_valid": True,
        "evidence_source": "global",
        "is_same_merchant": False,
    },
]

REVIEW_DECISION: dict[str, Any] = {
    "issue": {
        "line_id": 3,
        "word_id": 1,
        "word_text": "5.99",
        "current_label": "LINE_TOTAL",
        "type": "low_confidence",
        "suggested_label": "LINE_TOTAL",
    },
    "llm_review": {
        "decision": "VALID",
        "reasoning": "Price is consistent with merchant history",
        "suggested_label": "LINE_TOTAL",
        "confidence": "HIGH",
    },
    "similar_word_count": 8,
    "evidence": EVIDENCE_ITEMS,
    "consensus_score": 0.85,
}


def _make_trace_row(
    *,
    row_id: str = "run-1",
    trace_id: str = "trace-1",
    name: str = "ReceiptEvaluation",
    parent_run_id: str | None = None,
    extra: dict | str | None = None,
    outputs: dict | str | None = None,
) -> dict[str, Any]:
    """Build a minimal trace row dict."""
    row: dict[str, Any] = {
        "id": row_id,
        "trace_id": trace_id,
        "name": name,
        "parent_run_id": parent_run_id,
        "run_type": "chain",
        "status": "success",
        "start_time": "2025-01-01T00:00:00",
        "end_time": "2025-01-01T00:00:05",
    }
    if extra is not None:
        row["extra"] = json.dumps(extra) if isinstance(extra, dict) else extra
    else:
        row["extra"] = None
    if outputs is not None:
        row["outputs"] = (
            json.dumps(outputs) if isinstance(outputs, dict) else outputs
        )
    else:
        row["outputs"] = None
    return row


def _write_parquet(rows: list[dict[str, Any]], directory: str) -> str:
    """Write rows as a parquet file and return the directory path."""
    table = pa.table({k: [r.get(k) for r in rows] for k in rows[0]})
    pq.write_table(table, os.path.join(directory, "traces.parquet"))
    return directory


def _sample_rows() -> list[dict[str, Any]]:
    """Return a standard set of root + child rows for testing."""
    root = _make_trace_row(
        row_id="root-1",
        trace_id="trace-1",
        name="ReceiptEvaluation",
        parent_run_id=None,
        extra={
            "metadata": {
                "image_id": "img-abc",
                "receipt_id": 1,
                "merchant_name": "Test Cafe",
            }
        },
    )
    child = _make_trace_row(
        row_id="child-1",
        trace_id="trace-1",
        name="phase3_llm_review",
        parent_run_id="root-1",
        outputs={"review_all_decisions": [REVIEW_DECISION]},
    )
    return [root, child]


# ---------------------------------------------------------------------------
# Test: receipt structure has required fields
# ---------------------------------------------------------------------------


class TestReceiptStructure:
    """Receipts returned by build_evidence_cache have the required fields."""

    def test_required_top_level_fields(self, tmp_path: Path):
        rows = _sample_rows()
        _write_parquet(rows, str(tmp_path))

        results = build_evidence_cache(str(tmp_path))
        assert len(results) == 1

        receipt = results[0]
        assert "image_id" in receipt
        assert "receipt_id" in receipt
        assert "merchant_name" in receipt
        assert "trace_id" in receipt
        assert "issues_with_evidence" in receipt
        assert "summary" in receipt

    def test_metadata_values(self, tmp_path: Path):
        rows = _sample_rows()
        _write_parquet(rows, str(tmp_path))

        receipt = build_evidence_cache(str(tmp_path))[0]
        assert receipt["image_id"] == "img-abc"
        assert receipt["receipt_id"] == 1
        assert receipt["merchant_name"] == "Test Cafe"
        assert receipt["trace_id"] == "trace-1"


# ---------------------------------------------------------------------------
# Test: evidence items have required fields
# ---------------------------------------------------------------------------


class TestEvidenceItems:
    """Evidence entries have the expected shape."""

    def test_evidence_required_fields(self, tmp_path: Path):
        rows = _sample_rows()
        _write_parquet(rows, str(tmp_path))

        receipt = build_evidence_cache(str(tmp_path))[0]
        evidence = receipt["issues_with_evidence"][0]["evidence"]
        assert len(evidence) == 2

        for item in evidence:
            assert "word_text" in item
            assert "similarity_score" in item
            assert "label_valid" in item
            assert "evidence_source" in item
            assert "is_same_merchant" in item

    def test_evidence_values(self, tmp_path: Path):
        rows = _sample_rows()
        _write_parquet(rows, str(tmp_path))

        receipt = build_evidence_cache(str(tmp_path))[0]
        first_ev = receipt["issues_with_evidence"][0]["evidence"][0]
        assert first_ev["word_text"] == "5.99"
        assert first_ev["similarity_score"] == 0.92
        assert first_ev["label_valid"] is True
        assert first_ev["evidence_source"] == "merchant_history"
        assert first_ev["is_same_merchant"] is True


# ---------------------------------------------------------------------------
# Test: issue entries have required fields
# ---------------------------------------------------------------------------


class TestIssueEntries:
    """Each issue entry has the expected keys from the task spec."""

    def test_issue_required_fields(self, tmp_path: Path):
        rows = _sample_rows()
        _write_parquet(rows, str(tmp_path))

        receipt = build_evidence_cache(str(tmp_path))[0]
        issue = receipt["issues_with_evidence"][0]

        required_keys = {
            "line_id",
            "word_id",
            "word_text",
            "current_label",
            "issue_type",
            "suggested_label",
            "decision",
            "confidence",
            "reasoning",
            "consensus_score",
            "similar_word_count",
            "evidence",
        }
        assert required_keys.issubset(issue.keys())

    def test_issue_values(self, tmp_path: Path):
        rows = _sample_rows()
        _write_parquet(rows, str(tmp_path))

        issue = build_evidence_cache(str(tmp_path))[0]["issues_with_evidence"][0]
        assert issue["line_id"] == 3
        assert issue["word_id"] == 1
        assert issue["word_text"] == "5.99"
        assert issue["current_label"] == "LINE_TOTAL"
        assert issue["issue_type"] == "low_confidence"
        assert issue["decision"] == "VALID"
        assert issue["confidence"] == "HIGH"
        assert issue["consensus_score"] == 0.85
        assert issue["similar_word_count"] == 8


# ---------------------------------------------------------------------------
# Test: summary has correct keys
# ---------------------------------------------------------------------------


class TestSummary:
    """The summary dict has the correct shape and values."""

    def test_summary_keys(self, tmp_path: Path):
        rows = _sample_rows()
        _write_parquet(rows, str(tmp_path))

        summary = build_evidence_cache(str(tmp_path))[0]["summary"]
        assert "total_issues_reviewed" in summary
        assert "issues_with_evidence" in summary
        assert "avg_consensus_score" in summary
        assert "avg_similarity" in summary
        assert "decisions" in summary

    def test_summary_values(self, tmp_path: Path):
        rows = _sample_rows()
        _write_parquet(rows, str(tmp_path))

        summary = build_evidence_cache(str(tmp_path))[0]["summary"]
        assert summary["total_issues_reviewed"] == 1
        assert summary["issues_with_evidence"] == 1  # similar_word_count=8 > 0
        assert summary["avg_consensus_score"] == 0.85
        assert summary["decisions"]["VALID"] == 1
        assert summary["decisions"]["INVALID"] == 0
        assert summary["decisions"]["NEEDS_REVIEW"] == 0

    def test_avg_similarity(self, tmp_path: Path):
        rows = _sample_rows()
        _write_parquet(rows, str(tmp_path))

        summary = build_evidence_cache(str(tmp_path))[0]["summary"]
        expected = round((0.92 + 0.87) / 2, 4)
        assert summary["avg_similarity"] == expected


# ---------------------------------------------------------------------------
# Test: write sample outputs to /tmp/viz-cache-output/evidence/
# ---------------------------------------------------------------------------


class TestWriteSampleOutput:
    """Write sample output to the expected cache directory for integration."""

    def test_write_sample(self, tmp_path: Path):
        rows = _sample_rows()
        _write_parquet(rows, str(tmp_path))

        results = build_evidence_cache(str(tmp_path))

        output_dir = tmp_path / "viz-cache-output" / "evidence"
        output_dir.mkdir(parents=True, exist_ok=True)

        for receipt in results:
            image_id = receipt["image_id"]
            receipt_id = receipt["receipt_id"]
            filename = f"evidence-{image_id}-{receipt_id}.json"
            output_path = output_dir / filename
            output_path.write_text(json.dumps(receipt, indent=2))

        assert (output_dir / "evidence-img-abc-1.json").exists()
        content = json.loads(
            (output_dir / "evidence-img-abc-1.json").read_text()
        )
        assert content["image_id"] == "img-abc"
        assert len(content["issues_with_evidence"]) == 1


# ---------------------------------------------------------------------------
# Test: edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases that should be handled gracefully."""

    def test_empty_directory(self, tmp_path: Path):
        results = build_evidence_cache(str(tmp_path))
        assert results == []

    def test_root_without_child(self, tmp_path: Path):
        """Root span with no phase3_llm_review child is skipped."""
        root = _make_trace_row(
            row_id="root-1",
            trace_id="trace-1",
            name="ReceiptEvaluation",
            extra={"metadata": {"image_id": "img-1", "receipt_id": 1}},
        )
        _write_parquet([root], str(tmp_path))

        results = build_evidence_cache(str(tmp_path))
        assert results == []

    def test_child_with_empty_decisions(self, tmp_path: Path):
        """Child span with empty review_all_decisions is skipped."""
        root = _make_trace_row(
            row_id="root-1",
            trace_id="trace-1",
            name="ReceiptEvaluation",
            extra={"metadata": {"image_id": "img-1", "receipt_id": 1}},
        )
        child = _make_trace_row(
            row_id="child-1",
            trace_id="trace-1",
            name="phase3_llm_review",
            parent_run_id="root-1",
            outputs={"review_all_decisions": []},
        )
        _write_parquet([root, child], str(tmp_path))

        results = build_evidence_cache(str(tmp_path))
        assert results == []

    def test_multiple_receipts(self, tmp_path: Path):
        """Multiple ReceiptEvaluation roots produce multiple entries."""
        rows = _sample_rows()  # trace-1

        root2 = _make_trace_row(
            row_id="root-2",
            trace_id="trace-2",
            name="ReceiptEvaluation",
            extra={
                "metadata": {
                    "image_id": "img-def",
                    "receipt_id": 2,
                    "merchant_name": "Another Store",
                }
            },
        )
        child2 = _make_trace_row(
            row_id="child-2",
            trace_id="trace-2",
            name="phase3_llm_review",
            parent_run_id="root-2",
            outputs={
                "review_all_decisions": [
                    {
                        "issue": {
                            "line_id": 1,
                            "word_id": 0,
                            "word_text": "TAX",
                            "current_label": "TAX",
                            "type": "mismatch",
                            "suggested_label": "O",
                        },
                        "llm_review": {
                            "decision": "INVALID",
                            "reasoning": "Not a tax value",
                            "suggested_label": "O",
                            "confidence": "MEDIUM",
                        },
                        "similar_word_count": 0,
                        "evidence": [],
                        "consensus_score": -0.5,
                    }
                ]
            },
        )
        rows.extend([root2, child2])
        _write_parquet(rows, str(tmp_path))

        results = build_evidence_cache(str(tmp_path))
        assert len(results) == 2

        ids = {(r["image_id"], r["receipt_id"]) for r in results}
        assert ("img-abc", 1) in ids
        assert ("img-def", 2) in ids

    def test_evidence_capped_at_10(self):
        """_build_issue_entry caps evidence to 10 items."""
        many_evidence = [
            {
                "word_text": f"item-{i}",
                "similarity_score": 0.5,
                "label_valid": True,
                "evidence_source": "global",
                "is_same_merchant": False,
            }
            for i in range(15)
        ]
        decision = {
            "issue": {"line_id": 1, "word_id": 0, "word_text": "x"},
            "llm_review": {"decision": "VALID"},
            "similar_word_count": 15,
            "evidence": many_evidence,
            "consensus_score": 0.0,
        }
        entry = _build_issue_entry(decision)
        assert len(entry["evidence"]) == 10


# ---------------------------------------------------------------------------
# Test: _build_summary unit tests
# ---------------------------------------------------------------------------


class TestBuildSummaryUnit:
    """Direct unit tests for _build_summary."""

    def test_no_issues(self):
        summary = _build_summary([])
        assert summary["total_issues_reviewed"] == 0
        assert summary["issues_with_evidence"] == 0
        assert summary["avg_consensus_score"] == 0.0
        assert summary["avg_similarity"] == 0.0
        assert summary["decisions"] == {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}

    def test_mixed_decisions(self):
        issues = [
            {
                "decision": "VALID",
                "consensus_score": 0.8,
                "similar_word_count": 5,
                "evidence": [{"similarity_score": 0.9}],
            },
            {
                "decision": "INVALID",
                "consensus_score": -0.6,
                "similar_word_count": 0,
                "evidence": [],
            },
            {
                "decision": "NEEDS_REVIEW",
                "consensus_score": 0.1,
                "similar_word_count": 2,
                "evidence": [{"similarity_score": 0.7}, {"similarity_score": 0.5}],
            },
        ]
        summary = _build_summary(issues)
        assert summary["total_issues_reviewed"] == 3
        assert summary["issues_with_evidence"] == 2
        assert summary["decisions"]["VALID"] == 1
        assert summary["decisions"]["INVALID"] == 1
        assert summary["decisions"]["NEEDS_REVIEW"] == 1
        assert summary["avg_consensus_score"] == round((0.8 - 0.6 + 0.1) / 3, 4)
        assert summary["avg_similarity"] == round((0.9 + 0.7 + 0.5) / 3, 4)
