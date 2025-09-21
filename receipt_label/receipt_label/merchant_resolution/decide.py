from typing import Dict, Any, List
import logging

from receipt_label.merchant_validation.normalize import (
    normalize_address,
    normalize_phone,
)

logger = logging.getLogger(__name__)


def decide_best_candidate(
    ctx: Dict[str, Any],
    chroma_cands: List[Dict[str, Any]],
    places_cands: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Score candidates and pick best. Returns {candidates, best}."""
    cands: List[Dict[str, Any]] = []

    # Score places candidates
    for c in places_cands:
        score = 0.0
        cand_phone_digits = normalize_phone(c.get("phone") or "")
        ctx_phones = [normalize_phone(p) for p in (ctx.get("phones") or [])]
        phone_match = bool(
            cand_phone_digits and cand_phone_digits in ctx_phones
        )
        if phone_match:
            score += 0.5
        ca = ctx.get("address")
        if ca and c.get("address"):
            a = normalize_address(ca)
            b = normalize_address(c["address"])
            score += 0.4 if a.split()[:2] == b.split()[:2] else 0.2
        if c.get("types") == ["route"]:
            score -= 0.6
        c["score"] = max(0.0, min(1.0, score))
        c["_debug_phone_match"] = phone_match
        c["_debug_cand_phone_digits"] = cand_phone_digits
        c["_debug_ctx_phones"] = ctx_phones
        cands.append(c)

    # Score chroma candidates (cap)
    for c in chroma_cands:
        c["score"] = min(0.7, float(c.get("score", 0.0)))
        cands.append(c)

    best = max(cands, key=lambda x: x.get("score", 0.0), default=None)
    try:
        logger.info(
            "decision_candidates",
            extra={
                "num_candidates": len(cands),
                "best_source": (best or {}).get("source"),
                "best_reason": (best or {}).get("reason"),
                "best_score": (best or {}).get("score"),
            },
        )
        # Also log top candidates at INFO for visibility
        top = sorted(cands, key=lambda x: x.get("score", 0.0), reverse=True)[
            :5
        ]
        for idx, cand in enumerate(top):
            logger.info(
                "decision_top_%d",
                idx,
                extra={
                    "source": cand.get("source"),
                    "reason": cand.get("reason"),
                    "score": cand.get("score"),
                    "neighbor_key": cand.get("neighbor_key"),
                    "place_id": cand.get("place_id"),
                    "place_name": cand.get("name"),
                    "phone": cand.get("phone"),
                    "address": cand.get("address"),
                    "distance": cand.get("distance"),
                    "_debug_phone_match": cand.get("_debug_phone_match"),
                },
            )
            # Inline message for readability in default formatters
            try:
                logger.info(
                    (
                        "decision_top_msg %d: source=%s reason=%s score=%.3f "
                        "neighbor_key=%s place_id=%s name=%s phone=%s address=%s distance=%s"
                    ),
                    idx,
                    cand.get("source"),
                    cand.get("reason"),
                    float(cand.get("score") or 0.0),
                    cand.get("neighbor_key"),
                    cand.get("place_id"),
                    cand.get("name"),
                    cand.get("phone"),
                    cand.get("address"),
                    cand.get("distance"),
                )
            except Exception:
                pass
        # Inline best summary
        try:
            logger.info(
                (
                    "decision_best_msg: source=%s reason=%s score=%.3f neighbor_key=%s "
                    "place_id=%s name=%s phone=%s address=%s"
                ),
                (best or {}).get("source"),
                (best or {}).get("reason"),
                float((best or {}).get("score") or 0.0),
                (best or {}).get("neighbor_key"),
                (best or {}).get("place_id"),
                (best or {}).get("name"),
                (best or {}).get("phone"),
                (best or {}).get("address"),
            )
        except Exception:
            pass
        for idx, cand in enumerate(cands):
            logger.debug(
                "decision_cand_%d",
                idx,
                extra={
                    "source": cand.get("source"),
                    "reason": cand.get("reason"),
                    "score": cand.get("score"),
                    "phone": cand.get("phone"),
                    "_debug_phone_match": cand.get("_debug_phone_match"),
                    "_debug_cand_phone_digits": cand.get(
                        "_debug_cand_phone_digits"
                    ),
                    "_debug_ctx_phones": cand.get("_debug_ctx_phones"),
                },
            )
    except Exception:
        pass
    return {"candidates": cands, "best": best}
