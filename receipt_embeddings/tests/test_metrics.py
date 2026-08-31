"""SPEC §8 metric definitions used by evaluate.py."""

from __future__ import annotations

import pytest
from scripts.similarity_harness.metrics import (
    agreement_pct,
    estimate_request_units,
    gate,
    max_pp_gap,
    mean,
    percentile,
    recall_at_k,
    spec_gates,
    usd_from_request_units,
)


def test_recall_at_k_is_set_overlap_of_first_k() -> None:
    golden = ["a", "b", "c", "d"]
    retrieved = ["a", "x", "c", "y"]
    assert recall_at_k(retrieved, golden, k=3) == pytest.approx(2 / 3)
    assert recall_at_k(retrieved, golden, k=1) == pytest.approx(1.0)
    assert recall_at_k([], golden, k=2) == pytest.approx(0.0)
    assert recall_at_k(retrieved, [], k=5) == pytest.approx(1.0)


def test_percentile_and_mean() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    assert mean(values) == pytest.approx(2.5)
    assert percentile(values, 50) == pytest.approx(2.5)
    assert percentile(values, 0) == pytest.approx(1.0)
    assert percentile(values, 100) == pytest.approx(4.0)
    assert percentile([], 95) == pytest.approx(0.0)


def test_agreement_and_tier_gap() -> None:
    assert agreement_pct([("a", "a"), ("b", "c")]) == pytest.approx(50.0)
    assert agreement_pct([]) == pytest.approx(100.0)
    gap = max_pp_gap({"chroma_text": 8, "unresolved": 2}, {"chroma_text": 9})
    # backend 80/20 vs golden 100/0 → 20 pp
    assert gap == pytest.approx(20.0)


def test_cost_uses_reported_rru_else_estimate() -> None:
    assert estimate_request_units(20, 3.0) == pytest.approx(3.0)
    estimated = estimate_request_units(20, None)
    assert estimated >= 1.0
    assert usd_from_request_units(1_000_000) == pytest.approx(0.25)


def test_spec_gates_match_section_8_thresholds() -> None:
    metrics = {
        "merchant_agreement_pct": 98.0,
        "neighbor_recall_at_k": {"recall@10": 0.9},
        "section_vote_agreement_pct": 95.0,
        "tier_distribution_pp_gap": 5.0,
        "latency_ms": {"p95": 100.0},
    }
    gates = {item["name"]: item for item in spec_gates(metrics)}
    assert gates["merchant_agreement_pct"]["threshold"] == 98.0
    assert gates["neighbor_recall_at_10"]["threshold"] == 0.9
    assert gates["section_vote_agreement_pct"]["threshold"] == 95.0
    assert gates["tier_distribution_pp_gap"]["threshold"] == 5.0
    assert gates["latency_p95_ms"]["threshold"] == 100.0
    assert all(item["pass"] for item in gates.values())
    assert gate(name="x", value=99.0, threshold=100.0, comparator="lte")[
        "pass"
    ]
