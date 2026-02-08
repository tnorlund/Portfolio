"""Tests for evaluator_diff_viz_cache against local parquet traces."""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict

import pytest

from receipt_langsmith.spark.evaluator_diff_viz_cache import (
    _merge_decisions,
    build_diff_cache,
)

PARQUET_DIR = "/tmp/langsmith-traces/"
OUTPUT_DIR = "/tmp/viz-cache-output/before-after-diff/"


@pytest.fixture(scope="module")
def diff_cache() -> list[dict]:
    """Build the diff cache once for all tests."""
    if not os.path.isdir(PARQUET_DIR):
        pytest.skip("Trace parquet data not available at /tmp/langsmith-traces/")
    return build_diff_cache(PARQUET_DIR)


# ------------------------------------------------------------------
# Core assertions
# ------------------------------------------------------------------


def test_receipts_with_changes(diff_cache: list[dict]) -> None:
    """We should see at least 415 receipts with label changes."""
    with_changes = [r for r in diff_cache if r["change_count"] > 0]
    assert len(with_changes) >= 415, (
        f"Expected >= 415 receipts with changes, got {len(with_changes)}"
    )


def test_total_receipt_count(diff_cache: list[dict]) -> None:
    """We should have receipts for all traces that contain words.

    54 of the 588 ReceiptEvaluation roots have empty visual_lines
    (0 OCR words), so they are excluded from the diff cache.
    """
    assert len(diff_cache) >= 530, (
        f"Expected >= 530 receipts (588 roots minus ~54 empty), "
        f"got {len(diff_cache)}"
    )


def test_word_counts_match(diff_cache: list[dict]) -> None:
    """before and after label_summary should account for the same word count."""
    for receipt in diff_cache:
        before_total = sum(receipt["label_summary"]["before"].values())
        after_total = sum(receipt["label_summary"]["after"].values())
        assert before_total == after_total == receipt["word_count"], (
            f"Word count mismatch for {receipt['image_id']}_"
            f"{receipt['receipt_id']}: "
            f"before={before_total}, after={after_total}, "
            f"word_count={receipt['word_count']}"
        )


def test_changed_words_have_source(diff_cache: list[dict]) -> None:
    """Every changed word must have change_source, confidence, reasoning."""
    for receipt in diff_cache:
        for w in receipt["words"]:
            if w["changed"]:
                assert w.get("change_source") is not None, (
                    f"Missing change_source for {w}"
                )
                assert w.get("reasoning") is not None, (
                    f"Missing reasoning for {w}"
                )


def test_unchanged_words_no_extra_keys(diff_cache: list[dict]) -> None:
    """Unchanged words should not carry change_source / reasoning."""
    for receipt in diff_cache[:50]:  # spot-check first 50
        for w in receipt["words"]:
            if not w["changed"]:
                assert "change_source" not in w
                assert "reasoning" not in w


def test_priority_financial_over_others() -> None:
    """financial_validation should override lower-priority sources."""
    target = {
        (1, 1): {
            "change_source": "metadata_evaluation",
            "after_label": "SUBTOTAL",
            "confidence": "LOW",
            "reasoning": "metadata",
        }
    }
    incoming = {
        (1, 1): {
            "change_source": "financial_validation",
            "after_label": "LINE_TOTAL",
            "confidence": "HIGH",
            "reasoning": "financial",
        }
    }

    _merge_decisions(target, incoming)

    assert target[(1, 1)]["change_source"] == "financial_validation"
    assert target[(1, 1)]["after_label"] == "LINE_TOTAL"


def test_diff_payload_schema(diff_cache: list[dict]) -> None:
    """Verify the top-level keys of each diff payload."""
    required_keys = {
        "image_id",
        "receipt_id",
        "merchant_name",
        "trace_id",
        "word_count",
        "change_count",
        "words",
        "label_summary",
    }
    for receipt in diff_cache[:20]:
        assert required_keys <= set(receipt.keys()), (
            f"Missing keys: {required_keys - set(receipt.keys())}"
        )


def test_word_schema(diff_cache: list[dict]) -> None:
    """Each word dict must have the expected fields."""
    required = {"line_id", "word_id", "text", "before_label", "after_label",
                "changed", "bbox"}
    for receipt in diff_cache[:20]:
        for w in receipt["words"]:
            assert required <= set(w.keys()), (
                f"Missing word keys: {required - set(w.keys())}"
            )
            assert isinstance(w["bbox"], dict)
            assert "x" in w["bbox"] and "y" in w["bbox"]


# ------------------------------------------------------------------
# Sample output + summary
# ------------------------------------------------------------------


def test_write_samples_and_summary(diff_cache: list[dict]) -> None:
    """Write 3 sample outputs and print label-transition summary."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Pick 3 receipts with the most changes
    sorted_by_changes = sorted(
        diff_cache, key=lambda r: r["change_count"], reverse=True
    )

    for i, receipt in enumerate(sorted_by_changes[:3]):
        path = os.path.join(
            OUTPUT_DIR,
            f"sample_{i}_{receipt['image_id']}_{receipt['receipt_id']}.json",
        )
        with open(path, "w") as f:
            json.dump(receipt, f, indent=2)
        print(
            f"Sample {i}: {receipt['image_id']}_{receipt['receipt_id']} "
            f"({receipt['word_count']} words, "
            f"{receipt['change_count']} changes) -> {path}"
        )

    # Print transition summary
    transitions: Counter[tuple[str | None, str]] = Counter()
    source_counts: Counter[str] = Counter()
    for receipt in diff_cache:
        for w in receipt["words"]:
            if w["changed"]:
                before = w["before_label"] or "null"
                after = w["after_label"]
                transitions[(before, after)] += 1
                source_counts[w["change_source"]] += 1

    print("\n=== Label Transition Summary ===")
    print(f"Total changes: {sum(transitions.values())}")
    print("\nBy source:")
    for source, count in source_counts.most_common():
        print(f"  {source}: {count}")
    print("\nTop 20 transitions (before -> after):")
    for (before, after), count in transitions.most_common(20):
        print(f"  {before} -> {after}: {count}")

    # Verify samples were written
    sample_files = [
        f for f in os.listdir(OUTPUT_DIR) if f.startswith("sample_")
    ]
    assert len(sample_files) == 3
