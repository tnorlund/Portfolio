"""Search-then-join word-label evidence (spec §3.7).

``similar_labeled_words`` answers, in both polarities: "what similar
words have this label, or have been proven NOT to have it — and why?"
No new item type is involved: the target word's stored vector seeds a
validated-neighbor search on the word index, and each close neighbor's
existing ``ReceiptWordLabel`` rows carry the verdicts with their
original ``reasoning`` and provenance. The function is fully graceful —
it returns a structured answer for a missing vector, a throttled
search, or a failed label join rather than raising.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any, Optional

from receipt_embeddings.keys import word_vector_key
from receipt_embeddings.service_limits import MAX_SEARCH_RESULTS, WORD_INDEX
from receipt_embeddings.vector_client import VectorSearchClient

from receipt_dynamo.constants import CORE_LABEL_NAMES

# The retired validate_word_similarity tool's thresholds, kept so the
# evidence is judged the way the old validator intended (spec §3.7).
# Similarity itself is computed on the seam's cosine-distance contract
# (1 - distance), NOT the old tool's raw-L2 halving (review P2-C).
# MIN_SIMILARITY is therefore recalibrated to the corrected scale: the
# old tool's 0.80 cut on its halved scale admitted neighbors up to
# cosine distance 0.40, which is similarity 0.60 here. Keeping 0.80 on
# the corrected scale silently demanded distance <= 0.20 — on the live
# dev corpus real same-kind neighbors sit at 0.63-0.80 similarity, so
# every probe returned zero evidence (2026-09-01 live re-probe).
MIN_SIMILARITY = 0.60
MIN_MATCHES = 3
CONSENSUS_THRESHOLD = 0.80
SAME_MERCHANT_BOOST = 0.10
DEFAULT_TOP_K = 25

#: ``load_label_rows`` receives (image_id, receipt_id, line_id, word_id,
#: label) tuples and returns the ``ReceiptWordLabel`` rows that exist
#: (missing keys silently omitted — DynamoClient.get_receipt_word_labels
#: is the production implementation).
LabelRowLoader = Callable[[list[tuple[str, int, int, int, str]]], list[Any]]


def _distance_to_similarity(distance: float) -> float:
    """Cosine distance (the VectorSearchClient contract) to similarity.

    ``ScoredItem.distance`` is already cosine distance (1 - cosine
    similarity, range 0-2) on every backend, so similarity is simply
    ``1 - distance``. The retired validator's ``1 - d/2`` halving
    belonged to its raw-L2 assumption and inflated weak matches — a
    0.60-similarity neighbor scored 0.80 and passed the threshold
    (E3 review P2-C).
    """

    return max(0.0, 1.0 - distance)


def _neighbor_identity(
    metadata: Mapping[str, Any],
) -> Optional[tuple[str, int, int, int]]:
    try:
        return (
            str(metadata["image_id"]),
            int(metadata["receipt_id"]),
            int(metadata["line_id"]),
            int(metadata["word_id"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _row_record(row: Any) -> dict[str, Any]:
    """Normalize an adapter-hydrated dict or ReceiptWordLabel-like row."""

    if isinstance(row, Mapping):
        return {
            "label": row.get("label"),
            "validation_status": row.get("validation_status"),
            "reasoning": row.get("reasoning"),
            "label_proposed_by": row.get("label_proposed_by"),
            "timestamp_added": row.get("timestamp_added"),
        }
    return {
        "label": getattr(row, "label", None),
        "validation_status": getattr(row, "validation_status", None),
        "reasoning": getattr(row, "reasoning", None),
        "label_proposed_by": getattr(row, "label_proposed_by", None),
        "timestamp_added": getattr(row, "timestamp_added", None),
    }


def _evidence_entry(
    identity: tuple[str, int, int, int],
    metadata: Mapping[str, Any],
    similarity: float,
    same_merchant: Optional[bool],
    row: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "image_id": identity[0],
        "receipt_id": identity[1],
        "line_id": identity[2],
        "word_id": identity[3],
        "text": str(metadata.get("text", "")),
        "similarity": round(similarity, 3),
        "merchant": str(metadata.get("merchant_name", "")),
        "same_merchant": same_merchant,
        "validation_status": row.get("validation_status"),
        "reasoning": row.get("reasoning"),
        "proposed_by": row.get("label_proposed_by"),
        "timestamp_added": str(row.get("timestamp_added") or ""),
    }


def _degraded(
    word: dict[str, Any],
    label: str,
    *,
    reason: str,
    error_type: Optional[str] = None,
    found_vector: bool = True,
) -> dict[str, Any]:
    answer: dict[str, Any] = {
        "word": word,
        "label": label,
        "found_vector": found_vector,
        "recommended_status": "PENDING",
        "confidence": 0.0,
        "reason": reason,
        "evidence_for": [],
        "evidence_against": [],
        "alternative_labels": [],
    }
    if error_type is not None:
        answer["error_type"] = error_type
    return answer


# pylint: disable-next=too-many-statements
def similar_labeled_words(
    vector_client: VectorSearchClient,
    load_label_rows: LabelRowLoader,
    *,
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    label: str,
    top_k: int = DEFAULT_TOP_K,
    min_similarity: float = MIN_SIMILARITY,
    target_merchant: Optional[str] = None,
) -> dict[str, Any]:
    """Collect similarity evidence for/against a candidate word label.

    1. Read the target word's stored vector (no OpenAI call).
    2. Search validated word embeddings for nearest neighbors.
    3. Similarity-cut at ``min_similarity`` (old validator threshold).
    4. Join each survivor's ``ReceiptWordLabel`` rows by exact key.
    5. Aggregate: VALID rows for ``label`` are evidence FOR, INVALID
       rows are evidence AGAINST — each entry carrying the neighbor's
       original ``reasoning`` and provenance — plus alternative-label
       candidates and a weighted consensus recommendation.

    ``target_merchant`` enables the old same-merchant vote boost; when
    unknown the boost is skipped and ``same_merchant`` is ``None``.
    """

    word = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "line_id": line_id,
        "word_id": word_id,
    }
    target_key = word_vector_key(image_id, receipt_id, line_id, word_id)

    try:
        vector = vector_client.get_vector(target_key)
    except KeyError:
        return _degraded(
            word,
            label,
            found_vector=False,
            reason=(
                f"No stored vector for {target_key}; the word may "
                "predate the embedding backfill. Re-embed the receipt "
                "to enable similarity evidence."
            ),
        )
    # pylint: disable-next=broad-exception-caught
    except Exception as exc:  # noqa: BLE001 - degrade, never raise
        return _degraded(
            word,
            label,
            error_type="vector_search_failed",
            reason=f"Reading the stored vector failed: {exc}",
        )

    try:
        neighbors = vector_client.search(
            vector,
            index=WORD_INDEX,
            top_k=max(1, min(top_k, MAX_SEARCH_RESULTS)),
            filters={"label_status": "validated"},
        )
    # pylint: disable-next=broad-exception-caught
    except Exception as exc:  # noqa: BLE001 - degrade, never raise
        return _degraded(
            word,
            label,
            error_type="vector_search_failed",
            reason=f"Neighbor search failed: {exc}",
        )

    survivors: list[tuple[tuple[str, int, int, int], Any, float]] = []
    for neighbor in neighbors:
        if neighbor.key == target_key:
            continue
        similarity = _distance_to_similarity(neighbor.distance)
        if similarity < min_similarity:
            continue
        identity = _neighbor_identity(neighbor.metadata)
        if identity is None or identity == (
            image_id,
            receipt_id,
            line_id,
            word_id,
        ):
            continue
        survivors.append((identity, neighbor, similarity))

    base = {
        "word": word,
        "label": label,
        "found_vector": True,
        "neighbors_considered": len(neighbors),
        "neighbors_after_cut": len(survivors),
        "min_similarity": min_similarity,
    }

    if not survivors:
        return {
            **base,
            "recommended_status": "PENDING",
            "confidence": 0.0,
            "reason": (
                "No validated neighbors at or above the similarity "
                "threshold."
            ),
            "evidence_for": [],
            "evidence_against": [],
            "alternative_labels": [],
        }

    label_names = list(CORE_LABEL_NAMES)
    if label not in label_names:
        label_names.append(label)

    # Single-join reuse (E3 review P2-4): the Dynamo adapter's
    # validated-filter hydration already fetched each neighbor's
    # core-label rows and attached them as ``label_rows``. Only
    # neighbors without that metadata (the Chroma backend, or a
    # degraded join) and non-core candidate labels reach the loader.
    rows_by_word: dict[tuple[str, int, int, int], list[dict[str, Any]]] = {}
    loader_keys: list[tuple[str, int, int, int, str]] = []
    for identity, neighbor, _similarity in survivors:
        hydrated = neighbor.metadata.get("label_rows")
        if isinstance(hydrated, list):
            rows_by_word[identity] = [
                _row_record(row)
                for row in hydrated
                if isinstance(row, Mapping)
            ]
            if label not in CORE_LABEL_NAMES:
                loader_keys.append((*identity, label))
        else:
            loader_keys.extend((*identity, name) for name in label_names)
    try:
        loaded = load_label_rows(loader_keys) if loader_keys else []
    # pylint: disable-next=broad-exception-caught
    except Exception as exc:  # noqa: BLE001 - degrade, never raise
        return {
            **base,
            **_degraded(
                word,
                label,
                error_type="label_join_failed",
                reason=f"Label-row join failed: {exc}",
            ),
        }
    for row in loaded:
        try:
            row_key = (
                str(row.image_id),
                int(row.receipt_id),
                int(row.line_id),
                int(row.word_id),
            )
        except (AttributeError, TypeError, ValueError):
            continue
        rows_by_word.setdefault(row_key, []).append(_row_record(row))

    evidence_for: list[dict[str, Any]] = []
    evidence_against: list[dict[str, Any]] = []
    alternatives: Counter[str] = Counter()
    votes_for = 0.0
    votes_against = 0.0

    for identity, neighbor, similarity in survivors:
        metadata = neighbor.metadata
        same_merchant: Optional[bool] = None
        weight = similarity
        if target_merchant:
            same_merchant = (
                str(metadata.get("merchant_name", "")) == target_merchant
            )
            if same_merchant:
                weight = min(1.0, weight + SAME_MERCHANT_BOOST)
        for row in rows_by_word.get(identity, []):
            status = row.get("validation_status")
            row_label = row.get("label")
            if row_label == label:
                if status == "VALID":
                    evidence_for.append(
                        _evidence_entry(
                            identity, metadata, similarity, same_merchant, row
                        )
                    )
                    votes_for += weight
                elif status == "INVALID":
                    evidence_against.append(
                        _evidence_entry(
                            identity, metadata, similarity, same_merchant, row
                        )
                    )
                    votes_against += weight
            elif status == "VALID" and row_label:
                alternatives[row_label] += 1

    for entries in (evidence_for, evidence_against):
        entries.sort(key=lambda e: (-e["similarity"], e["image_id"]))

    total_matches = len(evidence_for) + len(evidence_against)
    total_votes = votes_for + votes_against
    confidence = votes_for / total_votes if total_votes > 0 else 0.0

    if total_matches == 0:
        recommended_status = "PENDING"
        reason = (
            f"No similar validated words carry a {label} verdict; "
            "see alternative_labels for what neighbors ARE labeled."
        )
    elif total_matches < MIN_MATCHES:
        recommended_status = "PENDING"
        reason = f"Only {total_matches} matches (need {MIN_MATCHES})"
    elif confidence >= CONSENSUS_THRESHOLD:
        recommended_status = "VALID"
        reason = f"{confidence:.0%} of similar words validated as {label}"
    elif confidence <= (1.0 - CONSENSUS_THRESHOLD):
        recommended_status = "INVALID"
        reason = f"{1.0 - confidence:.0%} of similar words rejected {label}"
    else:
        recommended_status = "NEEDS_REVIEW"
        reason = (
            f"Mixed evidence: {confidence:.0%} for, "
            f"{1.0 - confidence:.0%} against"
        )

    return {
        **base,
        "recommended_status": recommended_status,
        "confidence": round(confidence, 3),
        "reason": reason,
        "votes_for": round(votes_for, 3),
        "votes_against": round(votes_against, 3),
        "evidence_for": evidence_for,
        "evidence_against": evidence_against,
        "alternative_labels": [
            {"label": name, "neighbor_count": count}
            for name, count in alternatives.most_common(5)
        ],
    }


__all__ = [
    "CONSENSUS_THRESHOLD",
    "DEFAULT_TOP_K",
    "LabelRowLoader",
    "MIN_MATCHES",
    "MIN_SIMILARITY",
    "SAME_MERCHANT_BOOST",
    "similar_labeled_words",
    "word_vector_key",
]
