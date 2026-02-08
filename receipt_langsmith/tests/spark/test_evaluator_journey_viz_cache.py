"""Tests for the decision journey viz-cache builder.

Runs against the real parquet exports in /tmp/langsmith-traces/ and
writes sample outputs to /tmp/viz-cache-output/journey/.
"""

from __future__ import annotations

import json
import os

import pytest

from receipt_langsmith.spark.evaluator_journey_viz_cache import (
    build_journey_cache,
)

PARQUET_DIR = "/tmp/langsmith-traces/"
OUTPUT_DIR = "/tmp/viz-cache-output/journey"
pytestmark = pytest.mark.skipif(
    not os.path.isdir(PARQUET_DIR),
    reason=f"Trace data not available at {PARQUET_DIR}",
)


@pytest.fixture(scope="module")
def journey_cache() -> list[dict]:
    """Build the journey cache once for all tests."""
    return build_journey_cache(PARQUET_DIR)


def test_receipt_count(journey_cache: list[dict]) -> None:
    """We should get data for 588 receipts (one per ReceiptEvaluation root)."""
    assert len(journey_cache) == 588, (
        f"Expected 588 receipts, got {len(journey_cache)}"
    )


def test_multi_phase_words(journey_cache: list[dict]) -> None:
    """At least 401 words should appear in 2+ phases."""
    multi_phase_total = 0
    for receipt in journey_cache:
        multi_phase_total += receipt["summary"]["multi_phase_words"]
    assert multi_phase_total >= 401, (
        f"Expected >= 401 multi-phase words, got {multi_phase_total}"
    )


def test_journeys_have_required_fields(journey_cache: list[dict]) -> None:
    """Every receipt must have the required top-level fields."""
    for receipt in journey_cache:
        assert "image_id" in receipt
        assert "receipt_id" in receipt
        assert "trace_id" in receipt
        assert "journeys" in receipt
        assert "summary" in receipt
        summary = receipt["summary"]
        assert "total_words_evaluated" in summary
        assert "multi_phase_words" in summary
        assert "words_with_conflicts" in summary


def test_conflict_detection(journey_cache: list[dict]) -> None:
    """Conflict detection should find words with diverging decisions."""
    total_conflicts = 0
    for receipt in journey_cache:
        total_conflicts += receipt["summary"]["words_with_conflicts"]

    print(f"\nTotal words with conflicts: {total_conflicts}")
    # We know from the data that 182 words have conflicting decisions
    assert total_conflicts > 0, "Expected at least some conflicts"


def test_write_sample_outputs(journey_cache: list[dict]) -> None:
    """Write 3 sample outputs to /tmp/viz-cache-output/journey/."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Pick interesting samples: one with conflicts, one without, one with many phases
    with_conflicts = [
        r for r in journey_cache if r["summary"]["words_with_conflicts"] > 0
    ]
    without_conflicts = [
        r for r in journey_cache
        if r["summary"]["words_with_conflicts"] == 0
        and r["summary"]["total_words_evaluated"] > 0
    ]
    most_multi_phase = sorted(
        journey_cache,
        key=lambda r: r["summary"]["multi_phase_words"],
        reverse=True,
    )

    samples = []
    if with_conflicts:
        samples.append(("sample_with_conflicts.json", with_conflicts[0]))
    if without_conflicts:
        samples.append(("sample_no_conflicts.json", without_conflicts[0]))
    if most_multi_phase:
        samples.append(("sample_most_multi_phase.json", most_multi_phase[0]))

    for filename, sample in samples:
        path = os.path.join(OUTPUT_DIR, filename)
        with open(path, "w") as f:
            json.dump(sample, f, indent=2, default=str)
        print(f"Wrote {path}")

    assert len(samples) == 3, f"Expected 3 samples, wrote {len(samples)}"


def test_print_conflict_statistics(journey_cache: list[dict]) -> None:
    """Print detailed conflict statistics."""
    total_words = 0
    total_multi_phase = 0
    total_conflicts = 0
    phase_pair_conflicts: dict[tuple[str, str], int] = {}
    label_conflicts: dict[str, int] = {}

    for receipt in journey_cache:
        total_words += receipt["summary"]["total_words_evaluated"]
        total_multi_phase += receipt["summary"]["multi_phase_words"]
        total_conflicts += receipt["summary"]["words_with_conflicts"]

        for journey in receipt["journeys"]:
            if journey["has_conflict"]:
                label = journey["current_label"]
                label_conflicts[label] = label_conflicts.get(label, 0) + 1

                phases = [p["phase"] for p in journey["phases"]]
                for i in range(len(phases)):
                    for j in range(i + 1, len(phases)):
                        pair = (phases[i], phases[j])
                        phase_pair_conflicts[pair] = (
                            phase_pair_conflicts.get(pair, 0) + 1
                        )

    print("\n=== Decision Journey Statistics ===")
    print(f"Total receipts: {len(journey_cache)}")
    print(f"Total words evaluated: {total_words}")
    print(f"Multi-phase words: {total_multi_phase}")
    print(f"Words with conflicts: {total_conflicts}")
    print("\nConflicts by label:")
    for label, count in sorted(
        label_conflicts.items(), key=lambda x: x[1], reverse=True
    ):
        print(f"  {label}: {count}")
    print("\nConflicts by phase pair:")
    for (p1, p2), count in sorted(
        phase_pair_conflicts.items(), key=lambda x: x[1], reverse=True
    ):
        print(f"  {p1} vs {p2}: {count}")

    # Phase-level decision distribution
    phase_decisions: dict[str, dict[str, int]] = {}
    for receipt in journey_cache:
        for journey in receipt["journeys"]:
            for phase in journey["phases"]:
                pname = phase["phase"]
                dec = phase["decision"] or "UNKNOWN"
                phase_decisions.setdefault(pname, {})
                phase_decisions[pname][dec] = (
                    phase_decisions[pname].get(dec, 0) + 1
                )

    print("\nDecisions by phase:")
    for pname in (
        "currency_evaluation",
        "metadata_evaluation",
        "financial_validation",
        "phase3_llm_review",
    ):
        decs = phase_decisions.get(pname, {})
        total = sum(decs.values())
        if total == 0:
            print(f"  {pname}: 0 decisions")
            continue
        parts = ", ".join(
            f"{d}: {c} ({c/total*100:.1f}%)"
            for d, c in sorted(decs.items(), key=lambda x: x[1], reverse=True)
        )
        print(f"  {pname}: {total} decisions ({parts})")
