#!/usr/bin/env python3.13
"""Score a vector-search backend against the captured Chroma reference."""

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))
sys.path.insert(0, str(REPOSITORY_ROOT / "receipt_embeddings"))

# With the repo root on sys.path, the OUTER receipt_embeddings/ directory
# can already be cached as an empty namespace package that shadows the
# real package one level down. Evict any such stub before importing.
_cached = sys.modules.get("receipt_embeddings")
if _cached is not None and getattr(_cached, "__file__", None) is None:
    for _name in [
        name
        for name in list(sys.modules)
        if name == "receipt_embeddings"
        or name.startswith("receipt_embeddings.")
    ]:
        del sys.modules[_name]

from receipt_embeddings import (  # noqa: E402
    FilterValue,
    ScoredItem,
    VectorSearchClient,
)
from receipt_embeddings.testing import FakeVectorIndex  # noqa: E402

from scripts.similarity_harness.common import DEFAULT_RECALL_K  # noqa: E402
from scripts.similarity_harness.common import (
    MERCHANT_FAMILY,
    SECTION_FAMILY,
    canonical_json_bytes,
    content_digest,
    corpus_items,
    derive_merchant,
    derive_section_vote,
    load_fixture,
    round_vector,
    scored_item_from_dict,
)

DEFAULT_FIXTURE = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "similarity" / "golden.json"
)
DEV_TABLE = "ReceiptsTable-dc5be22"


def _query_signature(
    vector: Sequence[float],
    index: str,
    top_k: int,
    filters: Mapping[str, FilterValue] | None,
) -> str:
    return json.dumps(
        {
            "filters": dict(filters or {}),
            "index": index,
            "top_k": top_k,
            "vector": round_vector(vector),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


class CapturedChromaReplay:
    """Replay captured Chroma answers through ``VectorSearchClient``.

    This is the offline ``--backend chroma`` self-parity sanity check. It
    validates fixture wiring without reopening Chroma Cloud and keeps
    evaluation pure given one fixture.
    """

    def __init__(self, fixture: Mapping[str, Any]) -> None:
        self._queries: dict[str, Mapping[str, Any]] = {}
        for query in fixture["queries"]:
            signature = _query_signature(
                query["vector"],
                str(query["index"]),
                int(query["top_k"]),
                query.get("filters"),
            )
            if signature in self._queries:
                raise ValueError(
                    "fixture contains ambiguous replay queries; vary vector, "
                    "index, top_k, or filters"
                )
            self._queries[signature] = query
        self._vectors = {
            str(item["key"]): [float(value) for value in item["vector"]]
            for item in fixture["corpus"]
        }
        self._metadata = {
            str(item["key"]): dict(item.get("metadata", {}))
            for item in fixture["corpus"]
        }
        self.last_latency_ms = 0.0
        self.last_request_units = 0.0

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Mapping[str, FilterValue] | None = None,
    ) -> list[ScoredItem]:
        signature = _query_signature(vector, index, top_k, filters)
        try:
            query = self._queries[signature]
        except KeyError as exc:
            raise KeyError(
                "query was not captured in the Chroma fixture"
            ) from exc
        observation = query["expected"].get("observation", {})
        self.last_latency_ms = float(observation.get("latency_ms", 0.0))
        self.last_request_units = float(observation.get("request_units", 0.0))
        return [
            scored_item_from_dict(item, self._metadata[str(item["key"])])
            for item in query["expected"]["neighbors"]
        ]

    def get_vector(self, key: str) -> list[float]:
        try:
            return list(self._vectors[key])
        except KeyError as exc:
            raise KeyError(f"unknown vector key: {key}") from exc


def _load_factory(path: str) -> Any:
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("backend factory must use module:callable syntax")
    return getattr(importlib.import_module(module_name), attribute)


def _dynamo_client(factory_path: str | None) -> VectorSearchClient:
    """Load the Round D implementation without coupling Round A to it."""

    configured = factory_path or os.environ.get("VECTOR_CLIENT_FACTORY")
    if configured:
        client = _load_factory(configured)()
    else:
        try:
            module = importlib.import_module(
                "receipt_embeddings.dynamo_client"
            )
        except ImportError as exc:
            raise RuntimeError(
                "the Dynamo vector backend is not implemented in Round A; "
                "set VECTOR_CLIENT_FACTORY=module:callable after Round D"
            ) from exc
        if hasattr(module, "create_client_from_env"):
            client = module.create_client_from_env()
        elif hasattr(module, "DynamoVectorSearchClient"):
            client_class = module.DynamoVectorSearchClient
            client = (
                client_class.from_env()
                if hasattr(client_class, "from_env")
                else client_class()
            )
        else:
            raise RuntimeError(
                "receipt_embeddings.dynamo_client must expose "
                "create_client_from_env or DynamoVectorSearchClient"
            )
    if not isinstance(client, VectorSearchClient):
        raise TypeError("Dynamo factory did not return a VectorSearchClient")
    return client


def build_backend(
    name: str,
    fixture: Mapping[str, Any],
    *,
    factory_path: str | None = None,
) -> VectorSearchClient:
    if name == "chroma":
        return CapturedChromaReplay(fixture)
    if name == "fake":
        return FakeVectorIndex(corpus_items(fixture))
    if name == "dynamo":
        return _dynamo_client(factory_path)
    raise ValueError(f"unsupported backend: {name}")


def _percentile(values: Sequence[float], percentile: float) -> float:
    """Linear percentile interpolation, matching NumPy's default definition."""

    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _recall(expected: Sequence[str], actual: Sequence[str], k: int) -> float:
    reference = list(expected[:k])
    if not reference:
        return 1.0
    return len(set(reference) & set(actual[:k])) / len(set(reference))


def _percentage(numerator: int, denominator: int) -> float:
    return 100.0 if denominator == 0 else (numerator / denominator) * 100.0


def _distribution(values: Sequence[str]) -> dict[str, float]:
    counts = Counter(values)
    total = len(values)
    return {
        key: round(_percentage(count, total), 6)
        for key, count in sorted(counts.items())
    }


def _backend_metrics(
    client: VectorSearchClient,
    *,
    wall_latency_ms: float,
    use_wall_latency: bool,
) -> tuple[float, float | None, int | None, float | None]:
    latency = getattr(client, "last_latency_ms", None)
    request_units = getattr(client, "last_request_units", None)
    request_bytes = getattr(client, "last_request_bytes", None)
    estimated_usd = None
    if hasattr(client, "get_last_search_metrics"):
        metrics = client.get_last_search_metrics()
        latency = metrics.get("latency_ms", latency)
        request_units = metrics.get("request_units", request_units)
        request_bytes = metrics.get("request_bytes", request_bytes)
        estimated_usd = metrics.get("estimated_usd")
    if use_wall_latency:
        latency = wall_latency_ms
    elif latency is None:
        latency = 0.0
    return (
        float(latency),
        float(request_units) if request_units is not None else None,
        int(request_bytes) if request_bytes is not None else None,
        float(estimated_usd) if estimated_usd is not None else None,
    )


def evaluate_fixture(
    fixture: Mapping[str, Any],
    client: VectorSearchClient,
    *,
    backend_name: str,
    recall_k: int = DEFAULT_RECALL_K,
    measure_wall_latency: bool = False,
) -> dict[str, object]:
    """Evaluate one backend. The result contains no clock-derived metadata."""

    recalls: list[float] = []
    recalls_by_family: dict[str, list[float]] = defaultdict(list)
    latencies: list[float] = []
    request_units: list[float] = []
    request_bytes: list[int] = []
    estimated_search_costs: list[float] = []
    missing_request_unit_samples = 0
    missing_request_byte_samples = 0
    merchant_matches = 0
    merchant_place_matches = 0
    merchant_total = 0
    # Known-truth merchant names (from the golden-set manifest), when the
    # fixture carries them. Cherry-picked from the grok entrant's
    # merchant_truth_agreement metric.
    merchant_truths = {
        str(receipt["key"]): str(receipt["merchant_truth"])
        for receipt in fixture["receipts"]
        if receipt.get("merchant_truth")
    }
    merchant_truth_matches = 0
    merchant_truth_total = 0
    tier_matches = 0
    expected_tiers: list[str] = []
    actual_tiers: list[str] = []
    section_matches = 0
    section_total = 0

    for query in fixture["queries"]:
        started = time.perf_counter()
        neighbors = client.search(
            query["vector"],
            str(query["index"]),
            int(query["top_k"]),
            query.get("filters") or None,
        )
        wall_latency_ms = (time.perf_counter() - started) * 1000.0
        latency, consumed, consumed_bytes, estimated_search_cost = (
            _backend_metrics(
                client,
                wall_latency_ms=wall_latency_ms,
                use_wall_latency=measure_wall_latency,
            )
        )
        latencies.append(latency)
        if consumed is None:
            missing_request_unit_samples += 1
        else:
            request_units.append(consumed)
        if consumed_bytes is None:
            missing_request_byte_samples += 1
        else:
            request_bytes.append(consumed_bytes)
        if estimated_search_cost is not None:
            estimated_search_costs.append(estimated_search_cost)

        expected_neighbors = [
            str(item["key"]) for item in query["expected"]["neighbors"]
        ]
        actual_neighbors = [item.key for item in neighbors]
        recall = _recall(expected_neighbors, actual_neighbors, recall_k)
        recalls.append(recall)
        family = str(query["family"])
        recalls_by_family[family].append(recall)

        if family == MERCHANT_FAMILY:
            inputs = query["inputs"]
            actual = derive_merchant(
                neighbors,
                image_id=str(inputs["image_id"]),
                receipt_id=int(inputs["receipt_id"]),
                tier=str(inputs["query_tier"]),
                max_distance=float(inputs["max_distance"]),
            )
            expected = query["expected"]["merchant"]
            merchant_total += 1
            actual_name = actual.get("merchant_name")
            expected_name = expected.get("merchant_name")
            names_agree = (actual_name is None and expected_name is None) or (
                isinstance(actual_name, str)
                and isinstance(expected_name, str)
                and actual_name.strip().casefold()
                == expected_name.strip().casefold()
            )
            if (
                actual.get("decision") == expected.get("decision")
                and names_agree
            ):
                merchant_matches += 1
            if all(
                actual.get(field) == expected.get(field)
                for field in ("decision", "merchant_name", "place_id")
            ):
                merchant_place_matches += 1
            expected_tier = str(expected["tier"])
            actual_tier = str(actual["tier"])
            expected_tiers.append(expected_tier)
            actual_tiers.append(actual_tier)
            tier_matches += expected_tier == actual_tier
            truth = merchant_truths.get(str(query["receipt_key"]))
            if truth is not None:
                merchant_truth_total += 1
                merchant_truth_matches += actual.get("merchant_name") == truth
        elif family == SECTION_FAMILY:
            inputs = query["inputs"]
            actual = derive_section_vote(
                neighbors,
                image_id=str(inputs["image_id"]),
                receipt_id=int(inputs["receipt_id"]),
                proposed_section_type=inputs.get("proposed_section_type"),
            )
            expected = query["expected"]["section"]
            section_total += 1
            if actual == expected:
                section_matches += 1

    expected_distribution = _distribution(expected_tiers)
    actual_distribution = _distribution(actual_tiers)
    tier_names = sorted(set(expected_distribution) | set(actual_distribution))
    distribution_delta = {
        tier: round(
            actual_distribution.get(tier, 0.0)
            - expected_distribution.get(tier, 0.0),
            6,
        )
        for tier in tier_names
    }
    max_tier_delta = max(
        (abs(value) for value in distribution_delta.values()), default=0.0
    )

    price = float(
        fixture.get("cost_model", {}).get(
            "read_request_usd_per_million", 0.125
        )
    )
    mean_request_units = (
        sum(request_units) / len(request_units) if request_units else None
    )
    mean_request_bytes = (
        sum(request_bytes) / len(request_bytes) if request_bytes else None
    )
    estimated_cost = (
        sum(estimated_search_costs) / len(estimated_search_costs)
        if estimated_search_costs
        else (
            mean_request_units * price / 1_000_000
            if mean_request_units is not None
            else None
        )
    )
    mean_recall = sum(recalls) / len(recalls) if recalls else 1.0
    merchant_agreement = _percentage(merchant_matches, merchant_total)
    merchant_place_agreement = _percentage(
        merchant_place_matches, merchant_total
    )
    tier_agreement = _percentage(tier_matches, merchant_total)
    section_agreement = _percentage(section_matches, section_total)
    p95 = _percentile(latencies, 0.95)

    return {
        "backend": backend_name,
        "fixture": {
            "content_sha256": content_digest(fixture),
            "query_count": len(fixture["queries"]),
            "receipt_count": len(fixture["receipts"]),
            "source": fixture["source"].get("backend"),
        },
        "gates": {
            "latency_p95_under_100ms": (
                p95 < 100.0 if backend_name == "dynamo" else None
            ),
            "merchant_agreement_at_least_98_percent": (
                merchant_agreement >= 98.0
            ),
            "neighbor_recall_at_least_0_85": mean_recall >= 0.85,
            "tier_distribution_within_5_percentage_points": (
                max_tier_delta <= 5.0
            ),
        },
        "metrics": {
            "estimated_usd_per_query": (
                round(estimated_cost, 12)
                if estimated_cost is not None
                else None
            ),
            "latency_ms": {
                "p50": round(_percentile(latencies, 0.50), 6),
                "p95": round(p95, 6),
                "sample_count": len(latencies),
                "source": (
                    "wall_clock"
                    if measure_wall_latency
                    else "backend_or_fixture"
                ),
            },
            "merchant_agreement_percent": round(merchant_agreement, 6),
            "merchant_place_agreement_percent": round(
                merchant_place_agreement, 6
            ),
            "merchant_tier_decision_agreement_percent": round(
                tier_agreement, 6
            ),
            "merchant_truth_agreement_percent": (
                round(
                    _percentage(merchant_truth_matches, merchant_truth_total),
                    6,
                )
                if merchant_truth_total
                else None
            ),
            "merchant_truth_sample_count": merchant_truth_total,
            "merchant_tier_distribution": {
                "actual_percent": actual_distribution,
                "delta_percentage_points": distribution_delta,
                "expected_percent": expected_distribution,
                "max_absolute_delta_percentage_points": round(
                    max_tier_delta, 6
                ),
            },
            "neighbor_recall_at_k": {
                "by_family": {
                    family: round(sum(values) / len(values), 8)
                    for family, values in sorted(recalls_by_family.items())
                },
                "k": recall_k,
                "overall": round(mean_recall, 8),
            },
            "read_request_units_per_query": (
                round(mean_request_units, 8)
                if mean_request_units is not None
                else None
            ),
            "request_unit_samples_missing": missing_request_unit_samples,
            "vector_search_request_bytes_per_query": (
                round(mean_request_bytes, 8)
                if mean_request_bytes is not None
                else None
            ),
            "vector_search_request_byte_samples_missing": (
                missing_request_byte_samples
            ),
            "section_vote_agreement_percent": round(section_agreement, 6),
        },
        "schema_version": 1,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend", required=True, choices=("fake", "dynamo", "chroma")
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--out", type=Path, default=Path("scorecard.json"))
    parser.add_argument("--recall-k", type=int, default=DEFAULT_RECALL_K)
    parser.add_argument(
        "--backend-factory",
        help="Dynamo client factory using module:callable syntax",
    )
    parser.add_argument(
        "--measure-wall-latency",
        action="store_true",
        help="measure calls locally; automatically enabled for Dynamo",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.recall_k <= 100:
        raise SystemExit("--recall-k must be between 1 and 100")
    fixture = load_fixture(args.fixture)
    if args.backend == "dynamo":
        table_name = os.environ.setdefault("DYNAMODB_TABLE_NAME", DEV_TABLE)
        if table_name != DEV_TABLE:
            raise SystemExit(
                f"refusing to query DynamoDB table {table_name!r}; "
                f"only {DEV_TABLE!r} is allowed"
            )
    try:
        client = build_backend(
            args.backend, fixture, factory_path=args.backend_factory
        )
    except (ImportError, RuntimeError, TypeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    scorecard = evaluate_fixture(
        fixture,
        client,
        backend_name=args.backend,
        recall_k=args.recall_k,
        measure_wall_latency=(
            args.measure_wall_latency or args.backend == "dynamo"
        ),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(canonical_json_bytes(scorecard))
    print(json.dumps(scorecard["metrics"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
