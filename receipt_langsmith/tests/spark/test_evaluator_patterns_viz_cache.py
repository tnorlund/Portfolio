"""Tests for evaluator_patterns_viz_cache against real trace data."""

from __future__ import annotations

import json
import os

import pytest

from receipt_langsmith.spark.evaluator_patterns_viz_cache import (
    _build_constellation_data,
    build_patterns_cache,
)

PARQUET_DIR = "/tmp/langsmith-traces/"
OUTPUT_DIR = "/tmp/viz-cache-output/patterns/"
BATCH_BUCKET = "label-evaluator-dev-batch-bucket-a82b944"
EXECUTION_ID = "d518a04d-4e79-45f7-bdfd-5f39d8971229"


@pytest.fixture(scope="module")
def patterns_cache() -> list[dict]:
    """Build the patterns cache once for all tests in this module."""
    if not os.path.isdir(PARQUET_DIR):
        pytest.skip(
            "Trace parquet data not available at /tmp/langsmith-traces/"
        )
    return build_patterns_cache(
        PARQUET_DIR,
        batch_bucket=BATCH_BUCKET,
        execution_id=EXECUTION_ID,
    )


@pytest.fixture(scope="module")
def constellation_data() -> dict:
    """Load constellation data from S3 pattern files."""
    return _build_constellation_data(BATCH_BUCKET, EXECUTION_ID)


class TestMerchantPatterns:
    """Verify pattern extraction from llm_pattern_discovery spans."""

    def test_finds_at_least_18_merchant_patterns(
        self, patterns_cache: list[dict]
    ):
        merchants_with_patterns = [
            e for e in patterns_cache if e.get("pattern") is not None
        ]
        assert (
            len(merchants_with_patterns) >= 18
        ), f"Expected >= 18 merchants with patterns, got {len(merchants_with_patterns)}"

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
                assert (
                    field in pattern
                ), f"Missing field '{field}' in pattern for {entry['merchant_name']}"

    def test_known_merchants_present(self, patterns_cache: list[dict]):
        merchant_names = {e["merchant_name"] for e in patterns_cache}
        expected = {
            "Urbane Cafe",
            "CVS",
            "Costco Wholesale",
            "In-N-Out Burger",
        }
        missing = expected - merchant_names
        assert not missing, f"Expected merchants not found: {missing}"

    def test_trace_ids_present(self, patterns_cache: list[dict]):
        for entry in patterns_cache:
            if entry.get("pattern") is not None:
                assert (
                    len(entry["trace_ids"]) >= 1
                ), f"No trace_ids for {entry['merchant_name']}"


class TestGeometricSummary:
    """Verify geometric anomaly aggregation from flag_geometric_anomalies spans."""

    def test_total_issues_match(self, patterns_cache: list[dict]):
        total_issues = sum(
            e["geometric_summary"]["total_issues"] for e in patterns_cache
        )
        assert (
            total_issues == 1608
        ), f"Expected 1608 total geometric issues, got {total_issues}"

    def test_issue_type_breakdown(self, patterns_cache: list[dict]):
        global_types: dict[str, int] = {}
        for entry in patterns_cache:
            for issue_type, count in entry["geometric_summary"][
                "issue_types"
            ].items():
                global_types[issue_type] = (
                    global_types.get(issue_type, 0) + count
                )

        assert (
            global_types.get("missing_label_cluster", 0) == 1073
        ), f"Expected 1073 missing_label_cluster, got {global_types.get('missing_label_cluster', 0)}"
        assert (
            global_types.get("missing_constellation_member", 0) == 346
        ), f"Expected 346 missing_constellation_member, got {global_types.get('missing_constellation_member', 0)}"
        assert (
            global_types.get("text_label_conflict", 0) == 189
        ), f"Expected 189 text_label_conflict, got {global_types.get('text_label_conflict', 0)}"

    def test_geometric_summary_always_present(
        self, patterns_cache: list[dict]
    ):
        for entry in patterns_cache:
            assert (
                "geometric_summary" in entry
            ), f"Missing geometric_summary for {entry['merchant_name']}"
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
        written = [f for f in os.listdir(OUTPUT_DIR) if f.endswith(".json")]
        assert (
            len(written) >= 19
        ), f"Expected >= 19 files written (18 merchants + index), got {len(written)}"

    def test_print_merchant_summary(self, patterns_cache: list[dict]):
        print("\n--- Merchant Pattern Summary ---")
        for entry in patterns_cache:
            name = entry["merchant_name"]
            has_pattern = entry.get("pattern") is not None
            geo_issues = entry["geometric_summary"]["total_issues"]
            receipt_type = ""
            if has_pattern:
                receipt_type = (
                    f" [{entry['pattern'].get('receipt_type', '?')}]"
                )
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
        print(
            f"\nTotal: {merchants_with_patterns} patterns, {total} geometric issues"
        )
        print(f"Merchants in cache: {len(patterns_cache)}")


class TestConstellationData:
    """Verify constellation and label position data from S3 pattern files."""

    def test_constellation_data_loaded(
        self, constellation_data: dict
    ):
        assert len(constellation_data) > 0, "No constellation data loaded"

    def test_label_positions_structure(
        self, constellation_data: dict
    ):
        for merchant, data in constellation_data.items():
            positions = data.get("label_positions", {})
            for label, stats in positions.items():
                assert "mean_y" in stats, (
                    f"Missing mean_y for {label} in {merchant}"
                )
                assert "std_y" in stats, (
                    f"Missing std_y for {label} in {merchant}"
                )
                assert "count" in stats, (
                    f"Missing count for {label} in {merchant}"
                )
                assert isinstance(stats["mean_y"], (int, float)), (
                    f"mean_y not numeric for {label} in {merchant}"
                )

    def test_constellation_relative_positions_structure(
        self, constellation_data: dict
    ):
        found_constellations = False
        for merchant, data in constellation_data.items():
            for c in data.get("constellations", []):
                found_constellations = True
                assert "labels" in c, f"Missing labels in {merchant}"
                assert "observation_count" in c
                assert "relative_positions" in c
                for label, pos in c["relative_positions"].items():
                    assert "mean_dx" in pos, (
                        f"Missing mean_dx for {label} in {merchant}"
                    )
                    assert "mean_dy" in pos
                    assert "std_dx" in pos
                    assert "std_dy" in pos
        assert found_constellations, "No constellations found in any merchant"

    def test_label_pairs_structure(
        self, constellation_data: dict
    ):
        for merchant, data in constellation_data.items():
            for p in data.get("label_pairs", []):
                assert "labels" in p
                assert len(p["labels"]) == 2
                assert "mean_dx" in p
                assert "mean_dy" in p
                assert "count" in p


class TestConstellationInCache:
    """Verify constellation data is merged into the full cache output."""

    def test_cache_entries_have_constellation_fields(
        self, patterns_cache: list[dict]
    ):
        for entry in patterns_cache:
            assert "receipt_count" in entry, (
                f"Missing receipt_count for {entry['merchant_name']}"
            )
            assert "label_positions" in entry, (
                f"Missing label_positions for {entry['merchant_name']}"
            )
            assert "constellations" in entry, (
                f"Missing constellations for {entry['merchant_name']}"
            )
            assert "label_pairs" in entry, (
                f"Missing label_pairs for {entry['merchant_name']}"
            )

    def test_some_merchants_have_constellations(
        self, patterns_cache: list[dict]
    ):
        with_constellations = [
            e for e in patterns_cache if len(e.get("constellations", [])) > 0
        ]
        assert len(with_constellations) > 0, (
            "Expected at least one merchant with constellations"
        )

    def test_some_merchants_have_label_positions(
        self, patterns_cache: list[dict]
    ):
        with_positions = [
            e for e in patterns_cache if len(e.get("label_positions", {})) > 0
        ]
        assert len(with_positions) > 0, (
            "Expected at least one merchant with label_positions"
        )
