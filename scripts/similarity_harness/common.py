"""Shared constants, ids, and fixture IO for the similarity harness."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from receipt_embeddings.vector_client import DISTANCE_ATOL, ScoredItem

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "similarity"
LINE_ITEM_GOLDEN = (
    REPO_ROOT
    / "receipt_upload"
    / "tests"
    / "fixtures"
    / "line_items_golden.json"
)

# Retrieval depths matching live consumers.
MERCHANT_TOP_K = 20  # resolver.py _similarity_search_impl n_results=20
WORD_TOP_K = 30  # chroma_helpers._compute_top_k_word_consensus n_query=30
SECTION_TOP_K = 15  # section_verifier.KNN_NEIGHBORS

# Resolver thresholds (receipt_upload.merchant_resolution.resolver).
MIN_SIMILARITY_THRESHOLD = 0.70
HIGH_CONFIDENCE_THRESHOLD = 0.85

WORDS_PER_RECEIPT = 2
ROWS_PER_RECEIPT = 3
MAY26_BATCH_SIZE = 43
SYNTHETIC_DIM = 8
SYNTHETIC_SEED = 0

SCHEMA_VERSION = 1

SECTION_TYPES = ("HEADER", "ITEMS", "TOTALS", "PAYMENT", "FOOTER")
QUERY_KINDS = ("phone", "address", "text")

# SPEC §8 / AGENT_PLAN gates used by evaluate.py.
GATE_MERCHANT_AGREEMENT_PCT = 98.0
GATE_NEIGHBOR_RECALL_AT_10 = 0.9
GATE_SECTION_VOTE_AGREEMENT_PCT = 95.0
GATE_TIER_DISTRIBUTION_PP = 5.0
GATE_P95_LATENCY_MS = 100.0

# On-demand DynamoDB RRU list price, us-east-1, as of the 2026-08 research
# note: $0.25 per million read request units. SearchVectors reports
# ConsumedCapacity.ReadRequestUnits when ReturnConsumedCapacity=TOTAL;
# otherwise we estimate 1 RRU + ceil(top_k * projected_bytes / 4 KiB).
ON_DEMAND_RRU_USD = 0.25 / 1_000_000
ESTIMATED_PROJECTED_BYTES = 512

CHROMA_ENV_KEYS = (
    "CHROMA_CLOUD_API_KEY",
    "CHROMA_CLOUD_TENANT",
    "CHROMA_CLOUD_DATABASE",
)


def chroma_cloud_configured() -> bool:
    """True when all three Chroma Cloud env vars are non-empty."""
    return all(os.environ.get(key, "").strip() for key in CHROMA_ENV_KEYS)


def line_key(image_id: str, receipt_id: int, line_id: int) -> str:
    return (
        f"IMAGE#{image_id}" f"#RECEIPT#{receipt_id:05d}" f"#LINE#{line_id:05d}"
    )


def word_key(
    image_id: str, receipt_id: int, line_id: int, word_id: int
) -> str:
    return f"{line_key(image_id, receipt_id, line_id)}" f"#WORD#{word_id:05d}"


def may26_image_id(index: int) -> str:
    """Stable synthetic UUID for the May-26 batch placeholder slots."""
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"https://tylernorlund.com/chroma-removal/may26/{index:02d}",
        )
    )


def round_vector(vector: Sequence[float], ndigits: int = 8) -> list[float]:
    return [round(float(x), ndigits) for x in vector]


def round_distance(distance: float) -> float:
    return round(float(distance), 8)


def distance_to_similarity(distance: float) -> float:
    """Resolver conversion: L2-on-unit-sphere scaling of cosine distance."""
    return max(0.0, 1.0 - (float(distance) / 2.0))


def scored_to_neighbor(item: ScoredItem) -> dict[str, Any]:
    return {
        "key": item.key,
        "distance": round_distance(item.score),
        "metadata": dict(item.metadata),
    }


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_line_item_golden_receipts() -> list[dict[str, Any]]:
    payload = load_json(LINE_ITEM_GOLDEN)
    receipts = []
    for row in payload["receipts"]:
        receipts.append(
            {
                "image_id": row["image_id"],
                "receipt_id": int(row["receipt_id"]),
                "merchant_truth": row["merchant"],
                "source_set": "line_items_golden",
                "local_only": bool(row.get("local_only", False)),
            }
        )
    return receipts


def may26_placeholder_receipts() -> list[dict[str, Any]]:
    """43-image May-26 batch slots.

    Live capture fills these from Chroma Cloud. Offline synthetic capture
    uses deterministic UUIDs so the committed fixture set stays ≥40 and
    includes the batch the AGENT_PLAN names. The winner recapture replaces
    these ids with the real May-26 image ids.
    """
    merchants = [
        "Sprouts Farmers Market",
        "Costco Wholesale",
        "Trader Joe's",
        "Whole Foods Market",
        "Target",
        "Vons",
        "Amazon Fresh",
        "The Home Depot",
        "In-N-Out Burger",
        "Smith's",
    ]
    receipts = []
    for index in range(MAY26_BATCH_SIZE):
        receipts.append(
            {
                "image_id": may26_image_id(index),
                "receipt_id": 1,
                "merchant_truth": merchants[index % len(merchants)],
                "source_set": "may26",
                "local_only": False,
            }
        )
    return receipts


def golden_receipt_set() -> list[dict[str, Any]]:
    """Line-item golden receipts plus the May-26 batch (≥40)."""
    return load_line_item_golden_receipts() + may26_placeholder_receipts()


def merchant_decision_from_neighbors(
    neighbors: Sequence[Mapping[str, Any]],
    *,
    image_id: str,
    receipt_id: int,
    query_kind: str,
) -> dict[str, Any]:
    """Apply resolver chroma-tier rules to retrieved neighbors.

    Same-receipt hits are skipped. Remaining neighbors convert cosine
    distance to the resolver's similarity, drop those below
    ``MIN_SIMILARITY_THRESHOLD``, and accept the best. ``chroma_text``
    additionally requires ``HIGH_CONFIDENCE_THRESHOLD``.
    """
    ranked: list[tuple[float, Mapping[str, Any]]] = []
    for neighbor in neighbors:
        metadata = neighbor.get("metadata") or {}
        if (
            metadata.get("image_id") == image_id
            and int(metadata.get("receipt_id", 0) or 0) == receipt_id
        ):
            continue
        similarity = distance_to_similarity(float(neighbor["distance"]))
        if similarity < MIN_SIMILARITY_THRESHOLD:
            continue
        ranked.append((similarity, neighbor))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["key"]))
    if not ranked:
        return {
            "tier": "unresolved",
            "decision": None,
            "place_id": None,
            "confidence": 0.0,
        }
    similarity, neighbor = ranked[0]
    metadata = neighbor.get("metadata") or {}
    if query_kind == "phone":
        tier = "chroma_phone"
        accept = similarity >= MIN_SIMILARITY_THRESHOLD
    elif query_kind == "address":
        tier = "chroma_address"
        accept = similarity >= MIN_SIMILARITY_THRESHOLD
    else:
        tier = "chroma_text"
        accept = similarity >= HIGH_CONFIDENCE_THRESHOLD
    if not accept:
        return {
            "tier": "unresolved",
            "decision": None,
            "place_id": None,
            "confidence": similarity,
        }
    return {
        "tier": tier,
        "decision": metadata.get("merchant_name"),
        "place_id": metadata.get("place_id"),
        "confidence": similarity,
    }


def section_vote_from_neighbors(
    neighbors: Sequence[Mapping[str, Any]],
    *,
    image_id: str,
    receipt_id: int,
    proposed_section_type: str,
) -> dict[str, Any]:
    """Majority ``section_type`` vote, excluding the query receipt.

    This is the retrieval-level snapshot of section-verifier behaviour
    (agree / disagree / abstain). Live ``verify_receipt_sections`` also
    writes DynamoDB; capture never calls it.
    """
    votes: dict[str, int] = {}
    confidences: list[float] = []
    for neighbor in neighbors:
        metadata = neighbor.get("metadata") or {}
        if (
            metadata.get("image_id") == image_id
            and int(metadata.get("receipt_id", 0) or 0) == receipt_id
        ):
            continue
        label = metadata.get("section_type")
        if not label:
            continue
        votes[str(label)] = votes.get(str(label), 0) + 1
        confidences.append(distance_to_similarity(float(neighbor["distance"])))
    if not votes:
        return {
            "vote": "ABSTAINED",
            "predicted_section_type": None,
            "confidence": None,
        }
    # Majority; a tie abstains (matches verifier's _candidate_label).
    ranked = sorted(votes.items(), key=lambda item: (-item[1], item[0]))
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return {
            "vote": "ABSTAINED",
            "predicted_section_type": None,
            "confidence": (
                sum(confidences) / len(confidences) if confidences else None
            ),
        }
    predicted = ranked[0][0]
    vote = "AGREED" if predicted == proposed_section_type else "DISAGREED"
    return {
        "vote": vote,
        "predicted_section_type": predicted,
        "confidence": (
            sum(confidences) / len(confidences) if confidences else None
        ),
    }


def fixture_meta(
    *,
    source: str,
    embedding_dim: int,
    n_receipts: int,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "distance_metric": "cosine",
        "distance_range": [0.0, 2.0],
        "distance_definition": "1 - cosine_similarity",
        "embedding_dim": embedding_dim,
        "n_receipts": n_receipts,
        "k": {
            "merchant": MERCHANT_TOP_K,
            "words": WORD_TOP_K,
            "sections": SECTION_TOP_K,
        },
        "tolerance": {
            "distance_atol": DISTANCE_ATOL,
            "neighbor_set": "exact",
            "note": (
                "Two capture runs minutes apart must produce identical "
                "neighbor ids and distances within distance_atol. "
                "FakeVectorIndex is exact and sorts (distance, key). "
                "Live ANN backends may permute near-tie neighbors; the "
                "winner recapture is the canonical committed set."
            ),
        },
        "resolver_thresholds": {
            "min_similarity": MIN_SIMILARITY_THRESHOLD,
            "high_confidence": HIGH_CONFIDENCE_THRESHOLD,
            "similarity_from_distance": "max(0, 1 - distance / 2)",
        },
    }
