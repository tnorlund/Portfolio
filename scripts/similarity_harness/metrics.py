"""SPEC §8 / AGENT_PLAN scorecard metrics.

Definitions (pure given a golden fixture + a list of backend results):

* **neighbor recall@k** — for each query, ``|retrieved_ids ∩ golden_ids| /
  |golden_ids|`` using the first k ids of each list. Macro-average over
  queries. Reported per family (merchant / words / sections) and as
  ``recall@10`` (the Round D gate).
* **merchant agreement %** — share of receipts whose backend ``decision``
  equals the golden (Chroma) ``decision``. SPEC §8: ≥ 98%.
* **tier distribution** — counts of backend tiers, plus the max absolute
  percentage-point gap vs golden (E1: within ±5 pp).
* **tier agreement %** — per-receipt exact match of ``tier``.
* **section-vote agreement %** — per-row match of AGREED / DISAGREED /
  ABSTAINED (E2: ≥ 95%).
* **latency p50 / p95** — wall-clock ms of ``search()`` calls.
  SPEC §8: SearchVectors p95 < 100 ms from Lambda.
* **est. $/query** — mean USD per ``search()``. Fake and Chroma are $0
  incremental. Dynamo uses ConsumedCapacity RRUs when present, else
  ``1 + ceil(top_k * 512 / 4096)`` RRUs at $0.25 / million.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

from scripts.similarity_harness.common import (
    ESTIMATED_PROJECTED_BYTES,
    GATE_MERCHANT_AGREEMENT_PCT,
    GATE_NEIGHBOR_RECALL_AT_10,
    GATE_P95_LATENCY_MS,
    GATE_SECTION_VOTE_AGREEMENT_PCT,
    GATE_TIER_DISTRIBUTION_PP,
    ON_DEMAND_RRU_USD,
)


def recall_at_k(
    retrieved: Sequence[str],
    golden: Sequence[str],
    k: int,
) -> float:
    if k < 1:
        raise ValueError(f"k must be >= 1; got {k}")
    golden_ids = list(golden[:k])
    if not golden_ids:
        return 1.0
    retrieved_ids = set(retrieved[:k])
    hits = sum(1 for key in golden_ids if key in retrieved_ids)
    return hits / len(golden_ids)


def mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))


def percentile(values: Sequence[float], q: float) -> float:
    """Inclusive linear percentile. ``q`` in [0, 100]. Empty → 0.0."""
    if not values:
        return 0.0
    if q < 0 or q > 100:
        raise ValueError(f"percentile q must be in [0, 100]; got {q}")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (q / 100.0) * (len(ordered) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(ordered[low])
    weight = rank - low
    return float(ordered[low] * (1.0 - weight) + ordered[high] * weight)


def agreement_pct(pairs: Iterable[tuple[Any, Any]]) -> float:
    items = list(pairs)
    if not items:
        return 100.0
    hits = sum(1 for left, right in items if left == right)
    return 100.0 * hits / len(items)


def estimate_request_units(top_k: int, reported: float | None) -> float:
    if reported is not None:
        return float(reported)
    payload_rru = math.ceil(top_k * ESTIMATED_PROJECTED_BYTES / 4096)
    return float(1 + payload_rru)


def usd_from_request_units(request_units: float) -> float:
    return float(request_units) * ON_DEMAND_RRU_USD


def distribution(values: Iterable[str]) -> dict[str, int]:
    return dict(Counter(values))


def max_pp_gap(
    left: Mapping[str, int],
    right: Mapping[str, int],
) -> float:
    """Max absolute percentage-point gap between two count maps."""
    keys = set(left) | set(right)
    left_total = sum(left.values()) or 1
    right_total = sum(right.values()) or 1
    gaps = []
    for key in keys:
        left_pp = 100.0 * left.get(key, 0) / left_total
        right_pp = 100.0 * right.get(key, 0) / right_total
        gaps.append(abs(left_pp - right_pp))
    return max(gaps) if gaps else 0.0


def gate(
    *,
    name: str,
    value: float,
    threshold: float,
    comparator: str = "gte",
) -> dict[str, Any]:
    if comparator == "gte":
        passed = value >= threshold
    elif comparator == "lte":
        passed = value <= threshold
    else:
        raise ValueError(f"unknown comparator {comparator!r}")
    return {
        "name": name,
        "value": value,
        "threshold": threshold,
        "comparator": comparator,
        "pass": passed,
    }


def spec_gates(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    """SPEC §8 / AGENT_PLAN numeric gates (recorded, not enforced as exit)."""
    return [
        gate(
            name="merchant_agreement_pct",
            value=float(metrics["merchant_agreement_pct"]),
            threshold=GATE_MERCHANT_AGREEMENT_PCT,
        ),
        gate(
            name="neighbor_recall_at_10",
            value=float(metrics["neighbor_recall_at_k"]["recall@10"]),
            threshold=GATE_NEIGHBOR_RECALL_AT_10,
        ),
        gate(
            name="section_vote_agreement_pct",
            value=float(metrics["section_vote_agreement_pct"]),
            threshold=GATE_SECTION_VOTE_AGREEMENT_PCT,
        ),
        gate(
            name="tier_distribution_pp_gap",
            value=float(metrics["tier_distribution_pp_gap"]),
            threshold=GATE_TIER_DISTRIBUTION_PP,
            comparator="lte",
        ),
        gate(
            name="latency_p95_ms",
            value=float(metrics["latency_ms"]["p95"]),
            threshold=GATE_P95_LATENCY_MS,
            comparator="lte",
        ),
    ]
