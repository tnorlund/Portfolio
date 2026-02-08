"""Tests for the financial math overlay visualization cache builder.

Reads local parquet traces from /tmp/langsmith-traces/ and validates the
output structure and counts.
"""

from __future__ import annotations

import json
import os
import sys

import pytest


PARQUET_DIR = "/tmp/langsmith-traces/"
OUTPUT_DIR = "/tmp/viz-cache-output/financial-math/"

# Skip the entire module if trace data is not available
pytestmark = pytest.mark.skipif(
    not os.path.isdir(PARQUET_DIR),
    reason=f"Trace data not available at {PARQUET_DIR}",
)


@pytest.fixture(scope="module")
def cache_results():
    """Build the financial math cache once for all tests in this module."""
    from receipt_langsmith.spark.evaluator_financial_math_viz_cache import (
        build_financial_math_cache,
    )

    return build_financial_math_cache(PARQUET_DIR)


def test_expected_receipt_count(cache_results):
    """We expect 58 receipts with financial validation issues."""
    assert len(cache_results) == 58, (
        f"Expected 58 receipts with financial issues, got {len(cache_results)}"
    )


def test_each_receipt_has_at_least_one_equation(cache_results):
    """Every receipt in the cache should have at least one equation."""
    for receipt in cache_results:
        assert len(receipt["equations"]) >= 1, (
            f"Receipt {receipt['image_id']}_{receipt['receipt_id']} has no equations"
        )


def test_receipt_structure(cache_results):
    """Validate the top-level structure of each receipt dict."""
    required_keys = {
        "image_id",
        "receipt_id",
        "merchant_name",
        "trace_id",
        "equations",
        "summary",
    }
    for receipt in cache_results:
        assert required_keys.issubset(receipt.keys()), (
            f"Missing keys: {required_keys - receipt.keys()}"
        )


def test_equation_structure(cache_results):
    """Validate the structure of equation dicts."""
    equation_keys = {
        "issue_type",
        "description",
        "expected_value",
        "actual_value",
        "difference",
        "involved_words",
    }
    for receipt in cache_results:
        for eq in receipt["equations"]:
            assert equation_keys.issubset(eq.keys()), (
                f"Equation missing keys: {equation_keys - eq.keys()}"
            )
            assert len(eq["involved_words"]) >= 1


def test_involved_word_structure(cache_results):
    """Validate the structure of involved_word dicts."""
    word_keys = {
        "line_id",
        "word_id",
        "word_text",
        "current_label",
        "bbox",
        "decision",
        "confidence",
        "reasoning",
    }
    for receipt in cache_results:
        for eq in receipt["equations"]:
            for w in eq["involved_words"]:
                assert word_keys.issubset(w.keys()), (
                    f"Word missing keys: {word_keys - w.keys()}"
                )
                # bbox must have x, y, width, height
                bbox = w["bbox"]
                for field in ("x", "y", "width", "height"):
                    assert field in bbox, f"bbox missing {field}"


def test_summary_structure(cache_results):
    """Validate the summary dict in each receipt."""
    for receipt in cache_results:
        s = receipt["summary"]
        assert "total_equations" in s
        assert "has_invalid" in s
        assert "has_needs_review" in s
        assert isinstance(s["total_equations"], int)
        assert isinstance(s["has_invalid"], bool)
        assert isinstance(s["has_needs_review"], bool)
        assert s["total_equations"] == len(receipt["equations"])


def test_valid_issue_types(cache_results):
    """All issue_type values should be one of the known types."""
    known_types = {
        "GRAND_TOTAL_MISMATCH",
        "SUBTOTAL_MISMATCH",
        "LINE_ITEM_MISMATCH",
    }
    for receipt in cache_results:
        for eq in receipt["equations"]:
            assert eq["issue_type"] in known_types, (
                f"Unknown issue_type: {eq['issue_type']}"
            )


def test_valid_decisions(cache_results):
    """All decision values should be VALID, INVALID, or NEEDS_REVIEW."""
    valid_decisions = {"VALID", "INVALID", "NEEDS_REVIEW", None}
    for receipt in cache_results:
        for eq in receipt["equations"]:
            for w in eq["involved_words"]:
                assert w["decision"] in valid_decisions, (
                    f"Unknown decision: {w['decision']}"
                )


def test_write_sample_outputs(cache_results):
    """Write 3 sample outputs to disk for manual inspection."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Pick first 3
    samples = cache_results[:3]
    for i, receipt in enumerate(samples):
        path = os.path.join(
            OUTPUT_DIR,
            f"sample_{i}_{receipt['image_id'][:8]}.json",
        )
        with open(path, "w") as f:
            json.dump(receipt, f, indent=2)

    written = [
        f for f in os.listdir(OUTPUT_DIR) if f.endswith(".json")
    ]
    assert len(written) >= 3, f"Expected at least 3 sample files, got {len(written)}"


def test_print_summary_stats(cache_results, capsys):
    """Print summary statistics for inspection."""
    total_equations = sum(r["summary"]["total_equations"] for r in cache_results)
    total_words = sum(
        len(w)
        for r in cache_results
        for eq in r["equations"]
        for w in [eq["involved_words"]]
    )
    invalid_count = sum(
        1
        for r in cache_results
        if r["summary"]["has_invalid"]
    )
    needs_review_count = sum(
        1
        for r in cache_results
        if r["summary"]["has_needs_review"]
    )

    # Collect issue type distribution
    issue_types: dict[str, int] = {}
    for r in cache_results:
        for eq in r["equations"]:
            t = eq["issue_type"]
            issue_types[t] = issue_types.get(t, 0) + 1

    print("\n=== Financial Math Viz-Cache Summary ===")
    print(f"  Receipts with issues: {len(cache_results)}")
    print(f"  Total equations: {total_equations}")
    print(f"  Total involved words: {total_words}")
    print(f"  Receipts with INVALID: {invalid_count}")
    print(f"  Receipts with NEEDS_REVIEW: {needs_review_count}")
    print("  Issue type distribution:")
    for t, count in sorted(issue_types.items()):
        print(f"    {t}: {count}")
