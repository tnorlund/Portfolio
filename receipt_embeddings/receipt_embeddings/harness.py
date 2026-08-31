"""Orchestration for capture + evaluate. CLIs in scripts/ wrap this."""

from __future__ import annotations

import os
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from receipt_embeddings.cost import estimate_usd_per_query
from receipt_embeddings.dynamo_adapter import DynamoVectorSearchClient
from receipt_embeddings.fixtures import (
    SCHEMA_VERSION,
    round_distance,
)
from receipt_embeddings.metrics import (
    agreement_pct,
    build_scorecard,
    mean_recall_at_k,
    merchant_agreement_pct,
    tier_distribution,
)
from receipt_embeddings.testing.fake_index import FakeVectorIndex
from receipt_embeddings.vector_client import (
    LINE_EMBEDDINGS_INDEX,
    WORD_EMBEDDINGS_INDEX,
    ScoredItem,
    VectorSearchClient,
)

MERCHANT_TOP_K = 20
WORD_TOP_K = 30
SECTION_TOP_K = 15

COST_MODELS = {
    "fake": ("fake exact-NN: $0 (no SearchVectors / Chroma Cloud request)"),
    "chroma": (
        "chroma cloud: $0 per query in this scorecard (subscription); "
        "live capture is the reference, not a billed SearchVectors call"
    ),
    "dynamo": (
        "dynamodb vector search: $0.002/GB processed, 1 KB minimum "
        "(us-east-1 Standard); uses VectorSearchRequestBytes when present"
    ),
}


def chroma_cloud_credentials() -> dict[str, str] | None:
    """Return Cloud creds when all three ``CHROMA_CLOUD_*`` vars are set."""
    api_key = os.environ.get("CHROMA_CLOUD_API_KEY", "").strip()
    tenant = os.environ.get("CHROMA_CLOUD_TENANT", "").strip()
    database = os.environ.get("CHROMA_CLOUD_DATABASE", "").strip()
    if api_key and tenant and database:
        return {
            "api_key": api_key,
            "tenant": tenant,
            "database": database,
        }
    return None


def neighbors_excluding_self(
    client: VectorSearchClient,
    vector: Sequence[float],
    index: str,
    top_k: int,
    query_key: str,
    filters: Mapping[str, Any] | None = None,
) -> list[ScoredItem]:
    """``search(top_k+1)`` then drop the query key, matching ingest callers."""
    hits = client.search(vector, index, top_k=top_k + 1, filters=filters)
    return [item for item in hits if item.key != query_key][:top_k]


def _neighbor_payload(item: ScoredItem) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "key": item.key,
        "distance": round_distance(item.distance),
    }
    for field in (
        "merchant_name",
        "place_id",
        "image_id",
        "receipt_id",
        "section_type",
        "label_status",
        "text",
    ):
        if field in item.metadata and item.metadata[field] is not None:
            payload[field] = item.metadata[field]
    return payload


def retrieval_tier(
    query_meta: Mapping[str, Any], neighbors: Sequence[ScoredItem]
) -> str:
    """Retrieval-layer tier from neighbor metadata (no Places / Dynamo).

    Mirrors resolver corroboration: matching phone → ``chroma_phone``,
    matching address → ``chroma_address``, otherwise ``chroma_text`` if
    any neighbor remains, else ``unresolved``. Full ingest ladder (Places
    first) is out of scope for the harness — E1 ports that behind
    ``VECTOR_BACKEND``.
    """
    if not neighbors:
        return "unresolved"
    phone = query_meta.get("normalized_phone_10")
    address = query_meta.get("normalized_full_address")
    for item in neighbors:
        if phone and item.metadata.get("normalized_phone_10") == phone:
            return "chroma_phone"
    for item in neighbors:
        if address and item.metadata.get("normalized_full_address") == address:
            return "chroma_address"
    return "chroma_text"


def retrieval_decision(
    neighbors: Sequence[ScoredItem],
) -> dict[str, Any]:
    """Top neighbor's merchant / place as the retrieval decision."""
    if not neighbors:
        return {"merchant_name": None, "place_id": None}
    top = neighbors[0]
    return {
        "merchant_name": top.metadata.get("merchant_name"),
        "place_id": top.metadata.get("place_id"),
    }


def section_vote(
    query_section: str | None, neighbor_sections: Sequence[str | None]
) -> str:
    """AGREED / DISAGREED / ABSTAINED from neighbor ``section_type`` votes."""
    labels = [label for label in neighbor_sections if label]
    if not labels:
        return "ABSTAINED"
    counts = Counter(labels)
    winner = min(counts, key=lambda lab: (-counts[lab], lab))
    if query_section and winner == query_section:
        return "AGREED"
    return "DISAGREED"


def capture_from_client(
    client: VectorSearchClient,
    receipts: Sequence[Mapping[str, Any]],
    *,
    line_keys: Mapping[str, list[str]],
    word_keys: Mapping[str, list[str]],
) -> dict[str, Any]:
    """Run the three query families against ``client``; return a bundle.

    ``receipts`` entries need ``image_id``, ``receipt_id``, ``merchant``.
    ``line_keys`` / ``word_keys`` map ``image_id`` → stored item keys.
    Query vectors come from :meth:`VectorSearchClient.get_vector`.
    """
    merchant_queries: list[dict[str, Any]] = []
    word_queries: list[dict[str, Any]] = []
    section_queries: list[dict[str, Any]] = []

    for receipt in receipts:
        image_id = str(receipt["image_id"])
        receipt_id = int(receipt["receipt_id"])
        keys = line_keys.get(image_id, [])
        if not keys:
            continue
        query_key = keys[0]
        vector = client.get_vector(query_key)
        neighbors = neighbors_excluding_self(
            client,
            vector,
            LINE_EMBEDDINGS_INDEX,
            MERCHANT_TOP_K,
            query_key,
        )
        # Query-row metadata lives on the stored item; FakeVectorIndex
        # search does not return the query itself after exclusion, so
        # pull metadata from a self-search of 1 when possible.
        query_hits = client.search(vector, LINE_EMBEDDINGS_INDEX, top_k=1)
        query_meta = dict(query_hits[0].metadata) if query_hits else {}
        query_meta.setdefault("merchant_name", receipt.get("merchant"))
        tier = retrieval_tier(query_meta, neighbors)
        decision = retrieval_decision(neighbors)
        if decision["merchant_name"] is None:
            decision["merchant_name"] = receipt.get("merchant")
        merchant_queries.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "query_key": query_key,
                "neighbors": [_neighbor_payload(item) for item in neighbors],
                "tier": tier,
                "decision": decision,
            }
        )

        for word_key in word_keys.get(image_id, [])[:3]:
            word_vec = client.get_vector(word_key)
            word_neighbors = neighbors_excluding_self(
                client,
                word_vec,
                WORD_EMBEDDINGS_INDEX,
                WORD_TOP_K,
                word_key,
            )
            word_queries.append(
                {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "query_key": word_key,
                    "neighbors": [
                        _neighbor_payload(item) for item in word_neighbors
                    ],
                }
            )

        row_votes: list[dict[str, Any]] = []
        for line_key in keys[:5]:
            row_vec = client.get_vector(line_key)
            row_neighbors = neighbors_excluding_self(
                client,
                row_vec,
                LINE_EMBEDDINGS_INDEX,
                SECTION_TOP_K,
                line_key,
            )
            query_section = None
            self_hits = client.search(row_vec, LINE_EMBEDDINGS_INDEX, top_k=1)
            if self_hits:
                query_section = self_hits[0].metadata.get("section_type")
            vote = section_vote(
                query_section,
                [item.metadata.get("section_type") for item in row_neighbors],
            )
            row_votes.append(
                {
                    "query_key": line_key,
                    "section_type": query_section,
                    "vote": vote,
                    "neighbors": [
                        _neighbor_payload(item) for item in row_neighbors
                    ],
                }
            )
        section_queries.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "votes": row_votes,
            }
        )

    return {
        "merchant_resolution": {
            "schema_version": SCHEMA_VERSION,
            "top_k": MERCHANT_TOP_K,
            "queries": merchant_queries,
        },
        "word_neighbors": {
            "schema_version": SCHEMA_VERSION,
            "top_k": WORD_TOP_K,
            "queries": word_queries,
        },
        "section_verifier": {
            "schema_version": SCHEMA_VERSION,
            "top_k": SECTION_TOP_K,
            "queries": section_queries,
        },
    }


def _keys(seq: Sequence[Any]) -> list[str]:
    keys: list[str] = []
    for item in seq:
        if isinstance(item, ScoredItem):
            keys.append(item.key)
        else:
            keys.append(str(item["key"]))
    return keys


def evaluate_backend(
    client: VectorSearchClient,
    bundle: Mapping[str, Any],
    *,
    backend: str,
    request_bytes_per_search: float | None = None,
) -> dict[str, object]:
    """Score ``client`` against a golden fixture bundle. Pure given fixtures."""
    latencies_ms: list[float] = []
    usd: list[float] = []
    merchant_pairs: list[tuple[list[str], list[str]]] = []
    word_pairs: list[tuple[list[str], list[str]]] = []
    pred_merchants: list[str | None] = []
    gold_merchants: list[str | None] = []
    pred_tiers: list[str | None] = []
    gold_tiers: list[str | None] = []
    pred_votes: list[str | None] = []
    gold_votes: list[str | None] = []

    def _timed_neighbors(
        query_key: str, index: str, top_k: int
    ) -> list[ScoredItem]:
        vector = client.get_vector(query_key)
        started = time.perf_counter()
        hits = neighbors_excluding_self(
            client, vector, index, top_k, query_key
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        latencies_ms.append(elapsed_ms)
        usd.append(estimate_usd_per_query(request_bytes_per_search))
        return hits

    for query in bundle["merchant_resolution"]["queries"]:
        hits = _timed_neighbors(
            query["query_key"],
            LINE_EMBEDDINGS_INDEX,
            MERCHANT_TOP_K,
        )
        merchant_pairs.append((_keys(hits), _keys(query["neighbors"])))
        decision = retrieval_decision(hits)
        pred_merchants.append(decision.get("merchant_name"))
        gold_merchants.append(query["decision"].get("merchant_name"))
        query_hits = client.search(
            client.get_vector(query["query_key"]),
            LINE_EMBEDDINGS_INDEX,
            top_k=1,
        )
        query_meta = dict(query_hits[0].metadata) if query_hits else {}
        pred_tiers.append(retrieval_tier(query_meta, hits))
        gold_tiers.append(query.get("tier"))

    for query in bundle["word_neighbors"]["queries"]:
        hits = _timed_neighbors(
            query["query_key"],
            WORD_EMBEDDINGS_INDEX,
            WORD_TOP_K,
        )
        word_pairs.append((_keys(hits), _keys(query["neighbors"])))

    for receipt in bundle["section_verifier"]["queries"]:
        for vote in receipt["votes"]:
            hits = _timed_neighbors(
                vote["query_key"],
                LINE_EMBEDDINGS_INDEX,
                SECTION_TOP_K,
            )
            predicted = section_vote(
                vote.get("section_type"),
                [item.metadata.get("section_type") for item in hits],
            )
            pred_votes.append(predicted)
            gold_votes.append(vote.get("vote"))

    neighbor_recall = {
        "merchant_recall@1": mean_recall_at_k(merchant_pairs, 1),
        "merchant_recall@5": mean_recall_at_k(merchant_pairs, 5),
        "merchant_recall@10": mean_recall_at_k(merchant_pairs, 10),
        "merchant_recall@20": mean_recall_at_k(merchant_pairs, 20),
        "words_recall@10": mean_recall_at_k(word_pairs, 10),
        "words_recall@30": mean_recall_at_k(word_pairs, 30),
        "recall@10": mean_recall_at_k(merchant_pairs + word_pairs, 10),
    }
    n_receipts = len(bundle["merchant_resolution"]["queries"])
    return build_scorecard(
        backend=backend,
        neighbor_recall=neighbor_recall,
        merchant_agreement=merchant_agreement_pct(
            pred_merchants, gold_merchants
        ),
        tier_dist_predicted=tier_distribution(pred_tiers),
        tier_dist_golden=tier_distribution(gold_tiers),
        tier_decision_agreement=agreement_pct(pred_tiers, gold_tiers),
        section_vote_agreement=agreement_pct(pred_votes, gold_votes),
        latencies_ms=latencies_ms,
        usd_per_query=usd,
        n_receipts=n_receipts,
        cost_model=COST_MODELS[backend],
    )


def client_for_backend(
    backend: str, bundle: Mapping[str, Any]
) -> VectorSearchClient:
    """Construct the requested backend. ``chroma`` needs Cloud creds."""
    if backend == "fake":
        return FakeVectorIndex.from_fixture_items(bundle["vectors"]["items"])
    if backend == "dynamo":
        return DynamoVectorSearchClient()
    if backend == "chroma":
        raise RuntimeError(
            "evaluate --backend chroma requires a Chroma query client; "
            "use scripts/similarity_harness/evaluate.py which constructs "
            "it from CHROMA_CLOUD_* (package stays chromadb-free)"
        )
    raise ValueError(
        f"Unknown backend {backend!r}; expected fake, dynamo, or chroma"
    )
