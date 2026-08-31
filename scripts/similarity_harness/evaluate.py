#!/usr/bin/env python3
"""Grade a vector-search backend against the golden similarity fixtures.

    python scripts/similarity_harness/evaluate.py \
        --backend {fake,chroma,dynamo} --out scorecard.json

Replays every fixture query through the chosen backend's
``VectorSearchClient`` and scores it per SPEC §8:

- ``neighbor_recall_at_10`` (and @30 for words): overlap of the backend's
  top-k keys with the fixture's top-k, per query family.
- ``merchant_agreement_pct``: the pure Tier-2 decision (``decision.py``) run
  on the backend's neighbors vs. the fixture decision.
- ``tier_distribution``: fixture vs. backend counts per resolution tier, with
  deltas.
- ``section_vote_agreement_pct``: cosine-weighted KNN votes vs. fixture votes.
- ``latency_ms``: p50/p95 wall time per ``search()`` call.
- ``est_cost_per_query_usd``: per-backend cost model (documented constants).

Purity (rubric item 3): given the same fixtures, everything except the
``latency_ms`` block is a deterministic function of the fixture files and the
backend's answers — no AWS, no network for ``--backend fake``.

Backends:
- ``fake``   — ``FakeVectorIndex`` seeded from the ``vectors.json.gz``
  sidecar (exact NN over the captured vectors; fully offline).
- ``chroma`` — live Chroma Cloud, for the self-parity sanity gate (≈1.0).
- ``dynamo`` — interface ready, ``NotImplementedError`` until Round C/D
  lands the SearchVectors backend.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
for _pkg in ("receipt_embeddings", "receipt_chroma", "receipt_agent"):
    _path = _REPO_ROOT / _pkg
    if _path.is_dir():
        sys.path.insert(0, str(_path))
sys.path.insert(0, str(_REPO_ROOT))

from scripts.similarity_harness import decision, fixtures_io  # noqa: E402

from receipt_embeddings.vector_client import (  # noqa: E402
    LINES_INDEX,
    WORDS_INDEX,
    ScoredItem,
    VectorSearchClient,
)

RECALL_K = 10

# Cost-per-query estimates (USD). These are documented planning constants,
# not measured billing: chroma from the Aug-2026 Chroma Cloud bill divided by
# query volume (order-of-magnitude); dynamo to be replaced by consumed
# request units once the Round C/D backend reports them.
EST_COST_PER_QUERY = {
    "fake": 0.0,
    "chroma": 0.0025,
    "dynamo": None,  # filled from live request units when implemented
}


class ChromaBackend:
    """Adapter: live Chroma Cloud behind VectorSearchClient."""

    name = "chroma"

    def __init__(self) -> None:
        from receipt_agent.clients.factory import (  # noqa: PLC0415
            create_chroma_client,
        )

        self._client = create_chroma_client(mode="read")

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Optional[Mapping[str, Any]] = None,
    ) -> list[ScoredItem]:
        kwargs: dict[str, Any] = {}
        if filters:
            clauses = [{k: {"$eq": v}} for k, v in sorted(filters.items())]
            kwargs["where"] = (
                clauses[0] if len(clauses) == 1 else {"$and": clauses}
            )
        result = self._client.query(
            collection_name=index,
            query_embeddings=[list(vector)],
            n_results=top_k,
            include=["metadatas", "distances"],
            **kwargs,
        )
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        return [
            ScoredItem(
                key=key, distance=float(dist), metadata=dict(meta or {})
            )
            for key, dist, meta in zip(ids, distances, metadatas)
        ]

    def get_vector(
        self, key: str, index: str = LINES_INDEX
    ) -> Optional[Sequence[float]]:
        result = self._client.get(
            collection_name=index, ids=[key], include=["embeddings"]
        )
        embeddings = result.get("embeddings") or []
        ids = result.get("ids") or []
        for found_key, emb in zip(ids, embeddings):
            if found_key == key and emb is not None:
                return [float(v) for v in emb]
        return None


class DynamoBackend:
    """SearchVectors backend placeholder — interface ready, lands Round C/D.

    Will wrap DynamoDB ``SearchVectors`` on the ``lines-vectors`` /
    ``words-vectors`` indexes; cosine distance passes through unchanged, so
    this evaluate flow needs no edits when it lands.
    """

    name = "dynamo"

    def search(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
        filters: Optional[Mapping[str, Any]] = None,
    ) -> list[ScoredItem]:
        raise NotImplementedError(
            "DynamoDB SearchVectors backend arrives in Round C/D "
            "(dev vector indexes are judge-created; see BAKEOFF.md)."
        )

    def get_vector(
        self, key: str, index: str = LINES_INDEX
    ) -> Optional[Sequence[float]]:
        raise NotImplementedError(
            "DynamoDB SearchVectors backend arrives in Round C/D."
        )


def build_fake_backend(fixtures: Mapping[str, Any]) -> VectorSearchClient:
    """Seed a FakeVectorIndex from the vectors sidecar + fixture metadata."""
    from receipt_embeddings.testing import FakeVectorIndex  # noqa: PLC0415

    vectors = fixtures.get("vectors")
    if not vectors:
        raise SystemExit(
            "--backend fake needs the vectors.json.gz sidecar next to the "
            "fixtures (written by capture_golden.py)."
        )
    index = FakeVectorIndex()
    metadata_by_key: dict[str, dict[str, dict[str, Any]]] = {
        LINES_INDEX: {},
        WORDS_INDEX: {},
    }
    for entry in fixtures["merchant"]:
        for query in entry["queries"]:
            for neighbor in query["neighbors"]:
                metadata_by_key[LINES_INDEX].setdefault(
                    neighbor["key"], neighbor["metadata"]
                )
    for entry in fixtures["sections"]:
        for row in entry["rows"]:
            for neighbor in row["neighbors"]:
                metadata_by_key[LINES_INDEX].setdefault(
                    neighbor["key"], neighbor["metadata"]
                )
    for entry in fixtures["words"]:
        for query in entry["queries"]:
            for neighbor in query["neighbors"]:
                metadata_by_key[WORDS_INDEX].setdefault(
                    neighbor["key"], neighbor["metadata"]
                )
    for index_name, store in vectors.items():
        for key, vector in store.items():
            index.add(
                index_name,
                key,
                vector,
                metadata_by_key.get(index_name, {}).get(key, {}),
            )
    setattr(index, "name", "fake")
    return index


def _recall(
    fixture_neighbors: Sequence[Mapping[str, Any]],
    backend_items: Sequence[ScoredItem],
    k: int,
) -> Optional[float]:
    expected = [n["key"] for n in fixture_neighbors[:k]]
    if not expected:
        return None
    got = {item.key for item in backend_items[:k]}
    return len(got.intersection(expected)) / len(expected)


def _percentiles(samples: list[float]) -> dict[str, Optional[float]]:
    if not samples:
        return {"p50": None, "p95": None, "count": 0}
    ordered = sorted(samples)
    quantiles = (
        statistics.quantiles(ordered, n=100, method="inclusive")
        if (len(ordered) > 1)
        else [ordered[0]] * 99
    )
    return {
        "p50": round(quantiles[49], 3),
        "p95": round(quantiles[94], 3),
        "count": len(ordered),
    }


class _TimedSearch:
    """Wraps backend.search to collect wall-time samples."""

    def __init__(self, backend: VectorSearchClient) -> None:
        self.backend = backend
        self.samples_ms: list[float] = []

    def __call__(
        self,
        vector: Sequence[float],
        index: str,
        top_k: int,
    ) -> list[ScoredItem]:
        start = time.perf_counter()
        result = self.backend.search(vector, index, top_k)
        self.samples_ms.append((time.perf_counter() - start) * 1000.0)
        return result


def _items_as_neighbors(items: Sequence[ScoredItem]) -> list[dict[str, Any]]:
    return [
        {
            "key": item.key,
            "distance": item.distance,
            "metadata": dict(item.metadata),
        }
        for item in items
    ]


def evaluate(
    fixtures: Mapping[str, Any],
    backend: VectorSearchClient,
    backend_name: str,
) -> dict[str, Any]:
    """Compute the scorecard for ``backend`` against ``fixtures``."""
    search = _TimedSearch(backend)
    missing_query_vectors = 0

    # --- merchant family --------------------------------------------------
    merchant_recalls: list[float] = []
    agreements = 0
    merchant_total = 0
    fixture_tiers: dict[str, int] = {}
    backend_tiers: dict[str, int] = {}

    def _tier_of(decided: Optional[Mapping[str, Any]]) -> str:
        return decided["tier"] if decided else "unresolved"

    for entry in fixtures["merchant"]:
        replayed_queries = []
        for query in entry["queries"]:
            vector = backend.get_vector(query["query_key"], LINES_INDEX)
            if vector is None:
                missing_query_vectors += 1
                continue
            items = search(vector, LINES_INDEX, len(query["neighbors"]) or 1)
            recall = _recall(query["neighbors"], items, RECALL_K)
            if recall is not None:
                merchant_recalls.append(recall)
            replayed_queries.append(
                {
                    "tier": query["tier"],
                    "neighbors": _items_as_neighbors(items),
                }
            )
        merchant_total += 1
        fixture_decision = entry.get("decision")
        backend_decision = decision.decide_merchant(
            replayed_queries, entry["context"]
        )
        fixture_tiers[_tier_of(fixture_decision)] = (
            fixture_tiers.get(_tier_of(fixture_decision), 0) + 1
        )
        backend_tiers[_tier_of(backend_decision)] = (
            backend_tiers.get(_tier_of(backend_decision), 0) + 1
        )
        if fixture_decision is None and backend_decision is None:
            agreements += 1
        elif fixture_decision and backend_decision:
            if fixture_decision.get("merchant_name") == backend_decision.get(
                "merchant_name"
            ) and fixture_decision.get("place_id") == backend_decision.get(
                "place_id"
            ):
                agreements += 1

    tier_deltas = {
        tier: backend_tiers.get(tier, 0) - fixture_tiers.get(tier, 0)
        for tier in sorted(set(fixture_tiers) | set(backend_tiers))
    }

    # --- word family ------------------------------------------------------
    word_recalls_10: list[float] = []
    word_recalls_30: list[float] = []
    for entry in fixtures["words"]:
        for query in entry["queries"]:
            vector = backend.get_vector(query["query_key"], WORDS_INDEX)
            if vector is None:
                missing_query_vectors += 1
                continue
            items = search(vector, WORDS_INDEX, len(query["neighbors"]) or 1)
            recall10 = _recall(query["neighbors"], items, RECALL_K)
            recall30 = _recall(query["neighbors"], items, 30)
            if recall10 is not None:
                word_recalls_10.append(recall10)
            if recall30 is not None:
                word_recalls_30.append(recall30)

    # --- section family ---------------------------------------------------
    vote_matches = 0
    vote_total = 0
    for entry in fixtures["sections"]:
        fixture_votes = {
            vote["row_id"]: vote["section_type"] for vote in entry["votes"]
        }
        for row in entry["rows"]:
            vector = backend.get_vector(row["query_key"], LINES_INDEX)
            if vector is None:
                missing_query_vectors += 1
                continue
            items = search(vector, LINES_INDEX, decision.SECTION_KNN_NEIGHBORS)
            predicted = decision.section_vote(
                _items_as_neighbors(items),
                row["neighbor_labels"],
                image_id=entry["image_id"],
                receipt_id=entry["receipt_id"],
            )
            expected = fixture_votes.get(row["row_id"])
            got = predicted["section_type"] if predicted else None
            if expected is not None or got is not None:
                vote_total += 1
                if expected == got:
                    vote_matches += 1

    def _mean(values: list[float]) -> Optional[float]:
        return round(statistics.fmean(values), 4) if values else None

    return {
        "backend": backend_name,
        "fixtures": {
            "receipts": fixtures["manifest"]["counts"]["receipts"],
            "captured_at": fixtures["manifest"].get("captured_at"),
        },
        "neighbor_recall": {
            "merchant_lines_at_10": _mean(merchant_recalls),
            "words_at_10": _mean(word_recalls_10),
            "words_at_30": _mean(word_recalls_30),
            "merchant_query_count": len(merchant_recalls),
            "word_query_count": len(word_recalls_10),
        },
        "merchant": {
            "agreement_pct": (
                round(100.0 * agreements / merchant_total, 2)
                if merchant_total
                else None
            ),
            "receipts": merchant_total,
            "tier_distribution_fixture": fixture_tiers,
            "tier_distribution_backend": backend_tiers,
            "tier_distribution_delta": tier_deltas,
        },
        "sections": {
            "vote_agreement_pct": (
                round(100.0 * vote_matches / vote_total, 2)
                if vote_total
                else None
            ),
            "votes": vote_total,
        },
        "latency_ms": _percentiles(search.samples_ms),
        "est_cost_per_query_usd": EST_COST_PER_QUERY.get(backend_name),
        "missing_query_vectors": missing_query_vectors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--backend", choices=("fake", "chroma", "dynamo"), required=True
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=_REPO_ROOT / "tests" / "fixtures" / "similarity",
    )
    parser.add_argument("--out", type=Path, default=Path("scorecard.json"))
    args = parser.parse_args()

    fixtures = fixtures_io.load_fixtures(args.fixtures)
    if args.backend == "fake":
        backend: VectorSearchClient = build_fake_backend(fixtures)
    elif args.backend == "chroma":
        backend = ChromaBackend()
    else:
        backend = DynamoBackend()

    scorecard = evaluate(fixtures, backend, args.backend)
    args.out.write_text(
        json.dumps(scorecard, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(scorecard, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
