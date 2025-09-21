from typing import Callable, Dict, Any, List, Tuple, Optional
import logging

from receipt_label.vector_store import VectorClient
from receipt_label.merchant_validation.normalize import (
    normalize_phone,
    normalize_address,
)


EmbedFn = Callable[[List[str]], List[List[float]]]

logger = logging.getLogger(__name__)


def _street_queries(ctx: Dict[str, Any]) -> List[str]:
    """Street-first queries built from the full line text containing address anchors.

    Prefer the exact line text (like original script); fallback to ctx["address"].
    Limit to 3.
    """
    queries: List[str] = []
    seen_line_ids: set[int] = set()
    # Map line_id -> text for quick lookup
    line_text_by_id = {}
    for ln in ctx.get("lines") or []:
        try:
            line_text_by_id[int(getattr(ln, "line_id", -1))] = getattr(
                ln, "text", ""
            )
        except Exception:
            pass
    for w in ctx.get("words") or []:
        ext = getattr(w, "extracted_data", None) or {}
        if (ext.get("type") or "").lower() == "address":
            try:
                lid = int(getattr(w, "line_id", -1))
            except Exception:
                lid = -1
            if lid >= 0 and lid not in seen_line_ids:
                seen_line_ids.add(lid)
                lt = line_text_by_id.get(lid, "")
                if lt:
                    queries.append(lt)
    if not queries and ctx.get("address"):
        queries = [ctx["address"]]
    return queries[:3]


def _phone_line_queries(ctx: Dict[str, Any]) -> List[str]:
    """Phone-first queries using the full line text that contains a phone anchor.

    Mirrors original behavior of embedding the phone line, not just the digits.
    Limit to 2.
    """
    queries: List[str] = []
    seen_line_ids: set[int] = set()
    line_text_by_id = {}
    for ln in ctx.get("lines") or []:
        try:
            line_text_by_id[int(getattr(ln, "line_id", -1))] = getattr(
                ln, "text", ""
            )
        except Exception:
            pass
    for w in ctx.get("words") or []:
        ext = getattr(w, "extracted_data", None) or {}
        if (ext.get("type") or "").lower() == "phone":
            try:
                lid = int(getattr(w, "line_id", -1))
            except Exception:
                lid = -1
            if lid >= 0 and lid not in seen_line_ids:
                seen_line_ids.add(lid)
                lt = line_text_by_id.get(lid, "")
                if lt:
                    queries.append(lt)
    return queries[:2]


def chroma_find_candidates(
    line_client: VectorClient,
    embed_fn: EmbedFn,
    ctx: Dict[str, Any],
    get_neighbor_phones: Optional[
        Callable[[Tuple[str, int]], List[str]]
    ] = None,
) -> List[Dict[str, Any]]:
    """Query Chroma for likely neighbors using street- and phone-first inputs.

    Returns candidate dicts with: source, reason, neighbor_key, address, phone,
    distance, score.
    """
    out: List[Dict[str, Any]] = []
    # Diagnostics to explain fallback decisions
    accepted_count = 0
    rej_addr_only = 0
    rej_phone_only = 0
    rej_both = 0

    # Street-first
    s_qs = [q for q in _street_queries(ctx) if q]
    try:
        logger.info("chroma_street_queries", extra={"queries": s_qs})
    except Exception:
        pass
    if s_qs:
        embs = embed_fn(s_qs)
        for e, q in zip(embs, s_qs):
            qr = line_client.query(
                collection_name="lines",
                query_embeddings=[e],
                n_results=8,
                include=["metadatas", "distances"],
            )
            mds = (qr or {}).get("metadatas") or []
            dists = (qr or {}).get("distances") or []
            for md, dist in zip(
                mds[0] if mds else [], dists[0] if dists else []
            ):
                neighbor_addr = str(
                    (md or {}).get("normalized_full_address") or ""
                )
                neighbor_phone = str(
                    (md or {}).get("normalized_phone_10") or ""
                )
                street_norm = normalize_address(q)
                addr_ok = bool(neighbor_addr) and (
                    street_norm and street_norm.split()[0] in neighbor_addr
                )
                # Strict phone gating: require phone on receipt and equality with neighbor
                receipt_phones: List[str] = ctx.get("phones") or []
                if not receipt_phones:
                    phone_ok = False
                else:
                    neighbor_digits = neighbor_phone
                    if not neighbor_digits and get_neighbor_phones:
                        try:
                            nid = (
                                str((md or {}).get("image_id") or ""),
                                int((md or {}).get("receipt_id") or 0),
                            )
                            fetched = get_neighbor_phones(nid) or []
                            neighbor_digits = fetched[0] if fetched else ""
                        except Exception:
                            neighbor_digits = neighbor_digits or ""
                    phone_ok = bool(
                        neighbor_digits
                        and any(
                            normalize_phone(p) == neighbor_digits
                            for p in receipt_phones
                        )
                    )
                accepted = bool(addr_ok and phone_ok)
                if not accepted:
                    if not addr_ok and not phone_ok:
                        rej_both += 1
                    elif not addr_ok:
                        rej_addr_only += 1
                    elif not phone_ok:
                        rej_phone_only += 1
                    continue
                accepted_count += 1
                out.append(
                    {
                        "source": "chroma",
                        "reason": "street-first",
                        "neighbor_key": (
                            str((md or {}).get("image_id") or ""),
                            int((md or {}).get("receipt_id") or 0),
                        ),
                        "address": neighbor_addr,
                        "phone": (
                            neighbor_digits
                            if receipt_phones
                            else neighbor_phone
                        ),
                        "distance": float(dist or 1.0),
                        "score": max(0.0, 1.0 - float(dist or 1.0)),
                        "_debug_addr_ok": addr_ok,
                        "_debug_phone_ok": phone_ok,
                        "_debug_street_norm": street_norm,
                        "_debug_query": q,
                    }
                )

    # Phone-first (enforce phone equality when present)
    p_qs = [q for q in _phone_line_queries(ctx) if q]
    try:
        logger.info("chroma_phone_queries", extra={"queries": p_qs})
    except Exception:
        pass
    if p_qs:
        embs = embed_fn(p_qs)
        for e, q in zip(embs, p_qs):
            qr = line_client.query(
                collection_name="lines",
                query_embeddings=[e],
                n_results=8,
                include=["metadatas", "distances"],
            )
            mds = (qr or {}).get("metadatas") or []
            dists = (qr or {}).get("distances") or []
            # normalize phone from the query line text
            qn = normalize_phone(q)
            for md, dist in zip(
                mds[0] if mds else [], dists[0] if dists else []
            ):
                # Obtain neighbor phone from metadata or fetcher
                n_phone = str((md or {}).get("normalized_phone_10") or "")
                if not n_phone and get_neighbor_phones:
                    try:
                        nid = (
                            str((md or {}).get("image_id") or ""),
                            int((md or {}).get("receipt_id") or 0),
                        )
                        fetched = get_neighbor_phones(nid) or []
                        n_phone = fetched[0] if fetched else ""
                    except Exception:
                        n_phone = n_phone or ""
                # Strict phone equality required
                phone_ok = bool(qn and n_phone and qn == n_phone)
                if not phone_ok:
                    rej_phone_only += 1
                    continue
                # Also require address overlap using ctx address when available
                neighbor_addr = str(
                    (md or {}).get("normalized_full_address") or ""
                )
                addr_ok = True
                if ctx.get("address"):
                    street_norm = normalize_address(ctx.get("address") or "")
                    addr_ok = bool(
                        neighbor_addr
                        and street_norm
                        and street_norm.split()[0] in neighbor_addr
                    )
                if not addr_ok:
                    rej_addr_only += 1
                    continue
                out.append(
                    {
                        "source": "chroma",
                        "reason": "phone-first",
                        "neighbor_key": (
                            str((md or {}).get("image_id") or ""),
                            int((md or {}).get("receipt_id") or 0),
                        ),
                        "address": neighbor_addr,
                        "phone": n_phone,
                        "distance": float(dist or 1.0),
                        "score": max(0.0, 1.0 - float(dist or 1.0)),
                        "_debug_query": q,
                        "_debug_addr_ok": addr_ok,
                    }
                )
                accepted_count += 1

    # Summary logging to explain why Places fallback might be used
    try:
        logger.info(
            "chroma_summary",
            extra={
                "accepted": accepted_count,
                "rejected_addr_only": rej_addr_only,
                "rejected_phone_only": rej_phone_only,
                "rejected_both": rej_both,
                "num_street_queries": len(s_qs),
                "num_phone_queries": len(p_qs),
                "accepted_neighbor_keys": [
                    c.get("neighbor_key") for c in out[:5]
                ],
            },
        )
    except Exception:
        pass

    return out
