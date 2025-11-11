from typing import Dict, Any, List

from receipt_label.data.places_api import PlacesAPI


def places_find_candidates(
    api: PlacesAPI, ctx: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Return candidate merchants from Google Places based on receipt context.

    Each candidate is a dict with keys: source, reason, place_id, name, address,
    phone, types, score (0.0 initially).
    """
    out: List[Dict[str, Any]] = []
    if ctx.get("address"):
        r = api.search_by_address(ctx["address"], ctx.get("words") or [])
        # Only add candidate if it has a valid place_id (not empty/invalid cached results)
        if r and r.get("place_id") and r.get("place_id") not in ("NO_RESULTS", "INVALID", "TEXT"):
            out.append(
                {
                    "source": "places",
                    "reason": "address",
                    "place_id": r.get("place_id", ""),
                    "name": r.get("name", ""),
                    "address": r.get("formatted_address", ""),
                    "phone": r.get("formatted_phone_number", ""),
                    "types": r.get("types", []),
                    "score": 0.0,
                }
            )
    for ph in (ctx.get("phones") or [])[:2]:
        r = api.search_by_phone(ph)
        # Only add candidate if it has a valid place_id (not empty/invalid cached results)
        if r and r.get("place_id") and r.get("place_id") not in ("NO_RESULTS", "INVALID", "TEXT"):
            out.append(
                {
                    "source": "places",
                    "reason": "phone",
                    "place_id": r.get("place_id", ""),
                    "name": r.get("name", ""),
                    "address": r.get("formatted_address", ""),
                    "phone": r.get("formatted_phone_number", ""),
                    "types": r.get("types", []),
                    "score": 0.0,
                }
            )
    return out
