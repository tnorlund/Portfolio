"""Metric definitions used by evaluate.py / SPEC §8."""

from __future__ import annotations

import pytest

from receipt_embeddings.cost import estimate_usd_per_query
from receipt_embeddings.metrics import (
    agreement_pct,
    latency_percentiles_ms,
    mean_recall_at_k,
    merchant_agreement_pct,
    recall_at_k,
    tier_distribution,
    tier_distribution_delta,
)

pytestmark = pytest.mark.unit


def test_recall_at_k_is_set_overlap() -> None:
    golden = ["a", "b", "c", "d"]
    retrieved = ["a", "x", "b", "y"]
    assert recall_at_k(retrieved, golden, k=2) == pytest.approx(0.5)
    assert recall_at_k(retrieved, golden, k=4) == pytest.approx(0.5)


def test_mean_recall_perfect() -> None:
    pairs = [(["a", "b"], ["a", "b"]), (["c"], ["c"])]
    assert mean_recall_at_k(pairs, 1) == pytest.approx(1.0)


def test_merchant_agreement_normalizes_case() -> None:
    pct = merchant_agreement_pct(
        ["Sprouts Farmers Market", "vons"],
        ["sprouts farmers market", "Vons"],
    )
    assert pct == pytest.approx(100.0)


def test_tier_distribution_delta_is_max_abs() -> None:
    golden = {"chroma_text": 0.5, "chroma_phone": 0.5}
    predicted = {"chroma_text": 0.55, "chroma_phone": 0.45}
    assert tier_distribution_delta(predicted, golden) == pytest.approx(0.05)


def test_tier_distribution_counts_unresolved() -> None:
    dist = tier_distribution(["chroma_text", None, "chroma_text"])
    assert dist["chroma_text"] == pytest.approx(2 / 3)
    assert dist["unresolved"] == pytest.approx(1 / 3)


def test_agreement_pct() -> None:
    assert (
        agreement_pct(["AGREED", "ABSTAINED"], ["AGREED", "ABSTAINED"])
        == 100.0
    )
    assert agreement_pct(["AGREED"], ["DISAGREED"]) == 0.0


def test_latency_percentiles() -> None:
    stats = latency_percentiles_ms([1.0, 2.0, 3.0, 4.0, 100.0])
    assert stats["n"] == 5.0
    assert stats["p50"] == pytest.approx(3.0)
    assert stats["p95"] >= stats["p50"]


def test_dynamo_cost_uses_1kb_minimum() -> None:
    usd = estimate_usd_per_query(100.0)
    # 1 KB billed at $0.002 / GB
    expected = 1024.0 / (1024.0**3) * 0.002
    assert usd == pytest.approx(expected)
    assert estimate_usd_per_query(None) == 0.0
    assert estimate_usd_per_query(0) == 0.0
