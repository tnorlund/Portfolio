"""Tests for evaluator_patterns_viz_cache against real trace data."""

from __future__ import annotations

import json
import os

import pytest

from receipt_langsmith.spark.evaluator_patterns_viz_cache import (
    build_patterns_cache,
)

PARQUET_DIR = "/tmp/langsmith-traces/"
OUTPUT_DIR = "/tmp/viz-cache-output/patterns/"


@pytest.fixture(scope="module")
def patterns_cache() -> list[dict]:
    """Build the patterns cache once for all tests in this module."""
    if not os.path.isdir(PARQUET_DIR):
        pytest.skip("Trace parquet data not available at /tmp/langsmith-traces/")
    return build_patterns_cache(PARQUET_DIR)


class TestMerchantPatterns:
    """Verify pattern extraction from llm_pattern_discovery spans."""

    def test_finds_at_least_18_merchant_patterns(self, patterns_cache: list[dict]):
        merchants_with_patterns = [
            e for e in patterns_cache if e.get("pattern") is not None
        ]
        assert len(merchants_with_patterns) >= 18, (
            f"Expected >= 18 merchants with patterns, got {len(merchants_with_patterns)}"
        )

    def test_pattern_has_required_fields(self, patterns_cache: list[dict]):
        merchants_with_patterns = [
            e for e in patterns_cache if e.get("pattern") is not None
        ]
        required_fields = {
            "receipt_type",
            "label_positions",
            "grouping_rule",
        }
        for entry in merchants_with_patterns:
            pattern = entry["pattern"]
            for field in required_fields:
                assert field in pattern, (
                    f"Missing field '{field}' in pattern for {entry['merchant_name']}"
                )

    def test_known_merchants_present(self, patterns_cache: list[dict]):
        merchant_names = {e["merchant_name"] for e in patterns_cache}
        expected = {"Urbane Cafe", "CVS", "Costco Wholesale", "In-N-Out Burger"}
        missing = expected - merchant_names
        assert not missing, f"Expected merchants not found: {missing}"

    def test_trace_ids_present(self, patterns_cache: list[dict]):
        for entry in patterns_cache:
            if entry.get("pattern") is not None:
                assert len(entry["trace_ids"]) >= 1, (
                    f"No trace_ids for {entry['merchant_name']}"
                )


class TestGeometricSummary:
    """Verify geometric anomaly aggregation from flag_geometric_anomalies spans."""

    def test_total_issues_match(self, patterns_cache: list[dict]):
        total_issues = sum(
            e["geometric_summary"]["total_issues"] for e in patterns_cache
        )
        assert total_issues == 1608, (
            f"Expected 1608 total geometric issues, got {total_issues}"
        )

    def test_issue_type_breakdown(self, patterns_cache: list[dict]):
        global_types: dict[str, int] = {}
        for entry in patterns_cache:
            for issue_type, count in entry["geometric_summary"]["issue_types"].items():
                global_types[issue_type] = global_types.get(issue_type, 0) + count

        assert global_types.get("missing_label_cluster", 0) == 1073, (
            f"Expected 1073 missing_label_cluster, got {global_types.get('missing_label_cluster', 0)}"
        )
        assert global_types.get("missing_constellation_member", 0) == 346, (
            f"Expected 346 missing_constellation_member, got {global_types.get('missing_constellation_member', 0)}"
        )
        assert global_types.get("text_label_conflict", 0) == 189, (
            f"Expected 189 text_label_conflict, got {global_types.get('text_label_conflict', 0)}"
        )

    def test_geometric_summary_always_present(self, patterns_cache: list[dict]):
        for entry in patterns_cache:
            assert "geometric_summary" in entry, (
                f"Missing geometric_summary for {entry['merchant_name']}"
            )
            assert "total_issues" in entry["geometric_summary"]
            assert "issue_types" in entry["geometric_summary"]


class TestOutputWriting:
    """Write the cache to disk and verify it is valid JSON."""

    def test_write_patterns_to_output(self, patterns_cache: list[dict]):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        for entry in patterns_cache:
            merchant_slug = (
                entry["merchant_name"]
                .lower()
                .replace(" ", "_")
                .replace("'", "")
                .replace("&", "and")
                .replace("/", "-")
            )
            path = os.path.join(OUTPUT_DIR, f"{merchant_slug}.json")
            with open(path, "w") as f:
                json.dump(entry, f, indent=2, default=str)

        # Write index file
        index_path = os.path.join(OUTPUT_DIR, "_index.json")
        index = {
            "merchant_count": len(patterns_cache),
            "merchants_with_patterns": len(
                [e for e in patterns_cache if e.get("pattern") is not None]
            ),
            "total_geometric_issues": sum(
                e["geometric_summary"]["total_issues"] for e in patterns_cache
            ),
            "merchants": [
                {
                    "merchant_name": e["merchant_name"],
                    "has_pattern": e.get("pattern") is not None,
                    "geometric_issues": e["geometric_summary"]["total_issues"],
                }
                for e in patterns_cache
            ],
        }
        with open(index_path, "w") as f:
            json.dump(index, f, indent=2)

        # Verify files were written
        written = [
            f for f in os.listdir(OUTPUT_DIR) if f.endswith(".json")
        ]
        assert len(written) >= 19, (
            f"Expected >= 19 files written (18 merchants + index), got {len(written)}"
        )

    def test_print_merchant_summary(self, patterns_cache: list[dict]):
        print("\n--- Merchant Pattern Summary ---")
        for entry in patterns_cache:
            name = entry["merchant_name"]
            has_pattern = entry.get("pattern") is not None
            geo_issues = entry["geometric_summary"]["total_issues"]
            receipt_type = ""
            if has_pattern:
                receipt_type = f" [{entry['pattern'].get('receipt_type', '?')}]"
            if has_pattern or geo_issues > 0:
                print(
                    f"  {name}: pattern={'yes' if has_pattern else 'no'}"
                    f"{receipt_type}, geo_issues={geo_issues}"
                )
        total = sum(
            e["geometric_summary"]["total_issues"] for e in patterns_cache
        )
        merchants_with_patterns = len(
            [e for e in patterns_cache if e.get("pattern") is not None]
        )
        print(f"\nTotal: {merchants_with_patterns} patterns, {total} geometric issues")
        print(f"Merchants in cache: {len(patterns_cache)}")
