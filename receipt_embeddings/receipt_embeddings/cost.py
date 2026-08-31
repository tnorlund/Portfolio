"""Estimated $/query for scorecards (SPEC §8 / BAKEOFF rubric item 2).

DynamoDB vector search (on-demand, us-east-1 Standard, published 2026):
search is billed per byte processed at **$0.002 / GB**, with a **1 KB
minimum** per SearchVectors call. Consumed capacity is reported as
``VectorSearchRequestBytes``. Fake and Chroma Cloud have no per-query
byte meter — they report ``0.0`` with an explicit cost model string.
"""

from __future__ import annotations

# https://aws.amazon.com/dynamodb/pricing/  (vector search, Standard)
DYNAMO_SEARCH_USD_PER_GB = 0.002
DYNAMO_SEARCH_MIN_BILLABLE_BYTES = 1024.0
_BYTES_PER_GB = 1024.0**3


def estimate_usd_per_query(
    request_bytes: float | None,
    *,
    usd_per_gb: float = DYNAMO_SEARCH_USD_PER_GB,
    min_billable_bytes: float = DYNAMO_SEARCH_MIN_BILLABLE_BYTES,
) -> float:
    """Convert SearchVectors ``VectorSearchRequestBytes`` to USD.

    ``None`` or non-positive bytes means the backend did not meter a
    request (fake / Chroma subscription) → ``0.0``.
    """
    if request_bytes is None or request_bytes <= 0:
        return 0.0
    billed = max(float(request_bytes), min_billable_bytes)
    return billed / _BYTES_PER_GB * usd_per_gb


def mean_usd_per_query(per_query_usd: list[float]) -> float:
    """Average estimated dollars across timed searches."""
    if not per_query_usd:
        return 0.0
    return float(sum(per_query_usd) / len(per_query_usd))
