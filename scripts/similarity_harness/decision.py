"""Pure merchant-decision and section-vote logic shared by capture and eval.

``capture_golden.py`` (online, against live Chroma) and ``evaluate.py``
(offline, any backend) must score neighbors identically, so the scoring lives
here with **no imports beyond the stdlib**. The math mirrors
``receipt_upload/receipt_upload/merchant_resolution/resolver.py`` (Tier-2
similarity scoring) and ``receipt_chroma/receipt_chroma/section_propagation.py``
(``propagate_knn``); constants are copied verbatim rather than imported so this
module never drags in chromadb. ``tests/test_similarity_harness.py`` cross-
checks the copies against the source modules whenever those are importable, so
drift fails a test instead of silently skewing scores.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence

# --- constants mirrored from merchant_resolution/resolver.py ---------------
MIN_SIMILARITY_THRESHOLD = 0.70
HIGH_CONFIDENCE_THRESHOLD = 0.85
PHONE_MATCH_BOOST = 0.20
ADDRESS_MATCH_BOOST = 0.15
_MIN_TOKEN_LEN = 3

_GENERIC_MERCHANT_TOKENS = {
    "market",
    "supermarket",
    "store",
    "shop",
    "foods",
    "food",
    "grocery",
    "pharmacy",
    "drug",
    "gas",
    "station",
    "restaurant",
    "cafe",
    "coffee",
    "grill",
    "bar",
    "kitchen",
    "the",
    "and",
    "of",
    "for",
    "inc",
    "llc",
    "co",
    "company",
    "corp",
    "group",
    "center",
    "outlet",
}

# Tier order the resolver tries Chroma queries in (phone line, then address
# line, then merchant line). Acceptance bars per tier mirror _resolve_impl.
MERCHANT_TIERS = ("chroma_phone", "chroma_address", "chroma_text")
_TIER_MIN_CONFIDENCE = {
    "chroma_phone": MIN_SIMILARITY_THRESHOLD,
    "chroma_address": MIN_SIMILARITY_THRESHOLD,
    "chroma_text": HIGH_CONFIDENCE_THRESHOLD,
}

# Section verifier (mirrors section_verifier.KNN_NEIGHBORS / propagate_knn).
SECTION_KNN_NEIGHBORS = 15


def tokenize_text(text: str) -> set[str]:
    """Lowercase alphanumeric tokens of >= ``_MIN_TOKEN_LEN`` chars."""
    return {
        t
        for t in re.split(r"[^a-zA-Z0-9]+", text.lower())
        if len(t) >= _MIN_TOKEN_LEN
    }


def similarity_from_distance(distance: float) -> float:
    """Resolver's distance-to-similarity conversion (``1 - d/2``, floored)."""
    return max(0.0, 1.0 - (distance / 2.0))


def merchant_name_matches_receipt(
    merchant_name: Optional[str],
    line_texts: Sequence[str],
    allow_generic_overlap: bool = False,
) -> bool:
    """Token-overlap poison guard (mirrors resolver, on plain line texts)."""
    if not merchant_name:
        return True
    merchant_tokens = tokenize_text(merchant_name)
    if len(merchant_tokens) < 2:
        return True
    if not line_texts:
        return True
    receipt_tokens = tokenize_text(" ".join(t for t in line_texts if t))
    overlap = merchant_tokens & receipt_tokens
    distinctive = {
        t
        for t in overlap
        if len(t) >= _MIN_TOKEN_LEN and t not in _GENERIC_MERCHANT_TOKENS
    }
    if distinctive:
        return True
    if allow_generic_overlap or merchant_tokens <= _GENERIC_MERCHANT_TOKENS:
        return bool(overlap)
    return False


def addresses_similar(addr1: Optional[str], addr2: Optional[str]) -> bool:
    """Fuzzy address equality tolerant of OCR typos (mirrors resolver)."""
    if not addr1 or not addr2:
        return False
    a1 = addr1.upper().replace(" ", "")
    a2 = addr2.upper().replace(" ", "")
    if a1 == a2:
        return True
    if abs(len(a1) - len(a2)) > 5:
        return False
    shorter = min(len(a1), len(a2))
    if shorter == 0:
        return False
    matches = sum(1 for c1, c2 in zip(a1, a2) if c1 == c2)
    return matches / shorter >= 0.85


def _score_query(
    neighbors: Sequence[Mapping[str, Any]],
    *,
    image_id: str,
    receipt_id: int,
    expected_phone: Optional[str],
    expected_address: Optional[str],
    line_texts: Sequence[str],
) -> Optional[dict[str, Any]]:
    """Score one query's neighbors exactly like _similarity_search_impl.

    Each neighbor is ``{"key", "distance", "metadata"}`` where metadata holds
    the Chroma projection plus capture-time enrichment ``dynamo_place_id`` /
    ``dynamo_merchant_name`` (the resolver's per-candidate DynamoDB lookup,
    materialized so replay stays pure).
    """
    matches: list[dict[str, Any]] = []
    for neighbor in neighbors:
        meta = neighbor.get("metadata", {})
        if (
            meta.get("image_id") == image_id
            and int(meta.get("receipt_id") or 0) == receipt_id
        ):
            continue
        similarity = similarity_from_distance(float(neighbor["distance"]))
        if similarity < MIN_SIMILARITY_THRESHOLD:
            continue
        boost = 0.0
        result_phone = meta.get("normalized_phone_10")
        result_address = meta.get("normalized_full_address")
        if expected_phone and result_phone and expected_phone == result_phone:
            boost += PHONE_MATCH_BOOST
        if (
            expected_address
            and result_address
            and addresses_similar(expected_address, result_address)
        ):
            boost += ADDRESS_MATCH_BOOST
        matches.append(
            {
                "key": neighbor.get("key"),
                "image_id": meta.get("image_id"),
                "receipt_id": int(meta.get("receipt_id") or 0),
                "merchant_name": meta.get("merchant_name"),
                "confidence": min(1.0, similarity + boost),
                "place_id": meta.get("dynamo_place_id"),
                "dynamo_merchant_name": meta.get("dynamo_merchant_name"),
            }
        )
    if not matches:
        return None
    matches.sort(key=lambda m: (-m["confidence"], str(m["key"])))
    validated = [
        m
        for m in matches
        if merchant_name_matches_receipt(
            m["merchant_name"],
            line_texts,
            allow_generic_overlap=m["confidence"] >= HIGH_CONFIDENCE_THRESHOLD,
        )
    ]
    for match in validated[:5]:
        if match["place_id"]:
            return {
                "place_id": match["place_id"],
                "merchant_name": match["dynamo_merchant_name"]
                or match["merchant_name"],
                "confidence": round(match["confidence"], 6),
                "source_image_id": match["image_id"],
                "source_receipt_id": match["receipt_id"],
            }
    return None


def decide_merchant(
    queries: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
) -> Optional[dict[str, Any]]:
    """Run the Tier-2 similarity cascade over captured/replayed neighbors.

    ``queries``: ordered ``{"tier", "neighbors"}`` entries (resolver order:
    phone, address, text). ``context``: ``image_id``, ``receipt_id``,
    ``expected_phone``, ``expected_address``, ``line_texts``. Returns the
    accepted decision (with ``tier``) or ``None`` — the cascade stops at the
    first tier whose best match clears that tier's confidence bar, exactly as
    ``_resolve_impl`` does.
    """
    for query in queries:
        tier = query.get("tier")
        if tier not in _TIER_MIN_CONFIDENCE:
            continue
        result = _score_query(
            query.get("neighbors", []),
            image_id=str(context.get("image_id")),
            receipt_id=int(context.get("receipt_id") or 0),
            expected_phone=context.get("expected_phone"),
            expected_address=context.get("expected_address"),
            line_texts=context.get("line_texts", []),
        )
        if result and result["confidence"] >= _TIER_MIN_CONFIDENCE[tier]:
            return {"tier": tier, **result}
    return None


def section_vote(
    neighbors: Sequence[Mapping[str, Any]],
    neighbor_labels: Mapping[str, str],
    *,
    image_id: str,
    receipt_id: int,
    k: int = SECTION_KNN_NEIGHBORS,
) -> Optional[dict[str, Any]]:
    """Cosine-weighted KNN section vote (mirrors ``propagate_knn``).

    ``neighbor_labels`` maps neighbor key -> VALID section label (capture
    materializes the verifier's per-neighbor DynamoDB label lookup). Same-
    receipt neighbors and unlabeled neighbors are skipped, as in
    ``verify_receipt_sections``. Weight is non-negative cosine similarity
    ``max(1 - distance, 0)`` — for the L2-normalized vectors propagate_knn
    uses, cosine similarity is exactly ``1 - cosine_distance``.
    """
    votes: dict[str, float] = {}
    total = 0.0
    used = 0
    for neighbor in neighbors:
        if used >= k:
            break
        meta = neighbor.get("metadata", {})
        if (
            meta.get("image_id") == image_id
            and int(meta.get("receipt_id") or 0) == receipt_id
        ):
            continue
        label = neighbor_labels.get(str(neighbor.get("key")))
        if label is None:
            continue
        weight = max(1.0 - float(neighbor["distance"]), 0.0)
        votes[label] = votes.get(label, 0.0) + weight
        total += weight
        used += 1
    if not votes:
        return None
    winner, winning_weight = max(
        votes.items(), key=lambda item: (item[1], item[0])
    )
    confidence = winning_weight / (total + 1e-8)
    if confidence <= 0.0:
        return None
    return {"section_type": winner, "confidence": round(confidence, 6)}


__all__ = [
    "ADDRESS_MATCH_BOOST",
    "HIGH_CONFIDENCE_THRESHOLD",
    "MERCHANT_TIERS",
    "MIN_SIMILARITY_THRESHOLD",
    "PHONE_MATCH_BOOST",
    "SECTION_KNN_NEIGHBORS",
    "addresses_similar",
    "decide_merchant",
    "merchant_name_matches_receipt",
    "section_vote",
    "similarity_from_distance",
    "tokenize_text",
]
