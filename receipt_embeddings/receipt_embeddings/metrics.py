"""Scorecard metrics. Definitions match SPEC §8 + BAKEOFF Round A.

* **neighbor recall@k** — fraction of the golden top-k neighbor keys that
  appear in the candidate top-k (set overlap / k). Later rounds gate
  ``recall@10 ≥ 0.9``.
* **merchant agreement %** — fraction of receipts whose resolved merchant
  name matches the fixture decision (casefold, stripped). SPEC §8 gate
  is ≥ 98% vs Chroma.
* **tier distribution** — share of receipts in each resolution tier;
  later rounds require the candidate distribution within ±5% of the
  fixture. Also reported: per-receipt **tier-decision agreement**.
* **latency percentiles** — p50 / p95 of ``search()`` wall time in ms.
* **est. $/query** — see :mod:`receipt_embeddings.cost`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

import numpy as np

from receipt_embeddings.cost import mean_usd_per_query


def recall_at_k(
    retrieved_keys: Sequence[str],
    golden_keys: Sequence[str],
    k: int,
) -> float:
    """Set-overlap recall at ``k``. Empty golden → 1.0 if k==0 else 0.0."""
    if k <= 0:
        return 1.0
    golden = list(golden_keys)[:k]
    if not golden:
        return 1.0
    retrieved = set(list(retrieved_keys)[:k])
    hits = sum(1 for key in golden if key in retrieved)
    return hits / float(len(golden))


def mean_recall_at_k(
    pairs: Sequence[tuple[Sequence[str], Sequence[str]]],
    k: int,
) -> float:
    """Mean :func:`recall_at_k` over ``(retrieved, golden)`` pairs."""
    if not pairs:
        return 1.0
    return float(
        sum(recall_at_k(retrieved, golden, k) for retrieved, golden in pairs)
        / len(pairs)
    )


def normalize_merchant(name: str | None) -> str:
    """Casefold + collapse whitespace for merchant agreement."""
    if name is None:
        return ""
    return " ".join(str(name).casefold().split())


def merchant_agreement_pct(
    predicted: Sequence[str | None],
    golden: Sequence[str | None],
) -> float:
    """Percent of receipts whose normalized merchant names match."""
    if len(predicted) != len(golden):
        raise ValueError("predicted and golden must be the same length")
    if not predicted:
        return 100.0
    matches = sum(
        1
        for pred, gold in zip(predicted, golden, strict=True)
        if normalize_merchant(pred) == normalize_merchant(gold)
    )
    return 100.0 * matches / len(predicted)


def agreement_pct(
    predicted: Sequence[str | None], golden: Sequence[str | None]
) -> float:
    """Percent of pairwise equal string decisions (tiers, votes)."""
    if len(predicted) != len(golden):
        raise ValueError("predicted and golden must be the same length")
    if not predicted:
        return 100.0
    matches = sum(
        1
        for pred, gold in zip(predicted, golden, strict=True)
        if (pred or "") == (gold or "")
    )
    return 100.0 * matches / len(predicted)


def tier_distribution(tiers: Iterable[str | None]) -> dict[str, float]:
    """Share of receipts per tier (missing → ``unresolved``)."""
    counts: Counter[str] = Counter()
    n = 0
    for tier in tiers:
        counts[str(tier or "unresolved")] += 1
        n += 1
    if n == 0:
        return {}
    return {tier: count / n for tier, count in sorted(counts.items())}


def tier_distribution_delta(
    predicted: dict[str, float], golden: dict[str, float]
) -> float:
    """Max absolute difference in tier share (the ±5% later-round gate)."""
    keys = set(predicted) | set(golden)
    if not keys:
        return 0.0
    return max(abs(predicted.get(k, 0.0) - golden.get(k, 0.0)) for k in keys)


def latency_percentiles_ms(
    latencies_ms: Sequence[float],
) -> dict[str, float]:
    """p50 / p95 (and n) of search latencies in milliseconds."""
    if not latencies_ms:
        return {"p50": 0.0, "p95": 0.0, "n": 0.0}
    arr = np.asarray(list(latencies_ms), dtype=np.float64)
    return {
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
        "n": float(arr.size),
    }


def build_scorecard(
    *,
    backend: str,
    neighbor_recall: dict[str, float],
    merchant_agreement: float,
    tier_dist_predicted: dict[str, float],
    tier_dist_golden: dict[str, float],
    tier_decision_agreement: float,
    section_vote_agreement: float,
    latencies_ms: Sequence[float],
    usd_per_query: Sequence[float],
    n_receipts: int,
    cost_model: str,
) -> dict[str, object]:
    """Assemble the JSON object ``evaluate.py`` writes."""
    latency = latency_percentiles_ms(latencies_ms)
    return {
        "backend": backend,
        "n_receipts": n_receipts,
        "neighbor_recall": dict(neighbor_recall),
        "merchant_agreement_pct": merchant_agreement,
        "tier_distribution": {
            "predicted": tier_dist_predicted,
            "golden": tier_dist_golden,
            "max_abs_delta": tier_distribution_delta(
                tier_dist_predicted, golden=tier_dist_golden
            ),
        },
        "tier_decision_agreement_pct": tier_decision_agreement,
        "section_vote_agreement_pct": section_vote_agreement,
        "latency_ms": latency,
        "est_usd_per_query": mean_usd_per_query(list(usd_per_query)),
        "cost_model": cost_model,
    }
