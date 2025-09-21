from typing import Tuple, Dict, Any
import logging

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_label.data.places_api import PlacesAPI
from receipt_label.vector_store import VectorClient

from receipt_label.merchant_resolution.contexts import load_receipt_context
from receipt_label.merchant_resolution.places import places_find_candidates
from receipt_label.merchant_resolution.chroma import chroma_find_candidates
from receipt_label.merchant_resolution.decide import decide_best_candidate
from receipt_label.merchant_resolution.metadata import (
    metadata_from_places_candidate,
)

logger = logging.getLogger(__name__)


def resolve_receipt(
    key: Tuple[str, int],
    dynamo: DynamoClient,
    places_api: PlacesAPI,
    chroma_line_client: VectorClient,
    embed_fn,
    write_metadata: bool = False,
) -> Dict[str, Any]:
    """Resolve a single receipt to a merchant using Places + Chroma.

    Returns a dict with {context: {key}, decision: {candidates, best}}.
    If write_metadata is True and the best candidate is from Places with place_id,
    upserts a ReceiptMetadata to Dynamo.
    """
    ctx = load_receipt_context(dynamo, key)

    # Helper to fetch neighbor phones from Dynamo when missing in Chroma metadata
    def _get_neighbor_phones(neighbor_key: Tuple[str, int]) -> list[str]:
        try:
            words = dynamo.list_receipt_words_from_receipt(
                image_id=neighbor_key[0], receipt_id=neighbor_key[1]
            )
        except Exception:
            return []
        out: list[str] = []
        from receipt_label.merchant_validation.normalize import normalize_phone

        for w in words or []:
            ext = getattr(w, "extracted_data", None) or {}
            if (ext.get("type") or "").lower() == "phone":
                v = ext.get("value") or getattr(w, "text", "")
                p = normalize_phone(str(v))
                if p:
                    out.append(p)
        return out

    # Chroma-first, then Places on fallback based on threshold
    c = chroma_find_candidates(
        chroma_line_client, embed_fn, ctx, _get_neighbor_phones
    )
    chroma_best = max(c or [], key=lambda x: x.get("score", 0.0), default=None)
    threshold = 0.5
    try:
        logger.info(
            "chroma_phase",
            extra={
                "image_id": key[0],
                "receipt_id": key[1],
                "chroma_best_score": float(
                    (chroma_best or {}).get("score") or 0.0
                ),
                "chroma_best_reason": (chroma_best or {}).get("reason"),
                "threshold": threshold,
            },
        )
    except Exception:
        pass
    if not chroma_best or float(chroma_best.get("score", 0.0)) < threshold:
        logger.info(
            "calling_places_fallback",
            extra={"reason": "below_threshold_or_empty"},
        )
        p = places_find_candidates(places_api, ctx)
    else:
        logger.info("skipping_places", extra={"reason": "chroma_strong"})
        p = []
    decision = decide_best_candidate(ctx, c, p)
    best = decision.get("best")
    try:
        # Log summary of decision for quick visibility
        logger.info(
            "resolution_decision",
            extra={
                "image_id": key[0],
                "receipt_id": key[1],
                "best_source": (best or {}).get("source"),
                "best_reason": (best or {}).get("reason"),
                "best_score": (best or {}).get("score"),
                "best_neighbor_key": (best or {}).get("neighbor_key"),
                "best_place_id": (best or {}).get("place_id"),
                "best_name": (best or {}).get("name"),
                "best_phone": (best or {}).get("phone"),
                "best_address": (best or {}).get("address"),
            },
        )
    except Exception:
        pass

    wrote_metadata = False
    if (
        write_metadata
        and best
        and best.get("source") == "places"
        and best.get("place_id")
    ):
        meta = metadata_from_places_candidate(key, best)
        dynamo.add_receipt_metadatas([meta])
        wrote_metadata = True

    return {
        "context": {"key": key},
        "decision": decision,
        "wrote_metadata": wrote_metadata,
    }
