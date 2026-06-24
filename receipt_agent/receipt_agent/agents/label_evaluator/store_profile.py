"""Coherent store-profile extraction from the Google Places / Dynamo cache.

A receipt's header/footer prints a *coherent cluster* of store-identity fields:
the street line, the city/state/zip line, the phone, and the website all belong
to ONE physical store. The ``receipt_places`` cache (Google Places, keyed by
``place_id``) carries exactly that cluster, already validated against the real
receipt.

This module turns that cache into a small set of ``StoreProfile`` objects — one
per distinct store location of a merchant — which the ``compose_store_header``
synthesis operation uses to swap a receipt's store cluster for a *different*
branch of the same merchant (coherent location diversity), never mixing fields
from two places.

This is read-only over already-cached data — it makes no Google Places API
calls and bakes in no new content. Cache staleness/coverage is reported by
:func:`store_profile_coverage` so thin merchants can be flagged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


@dataclass(frozen=True)
class StoreProfile:
    """A single physical store's coherent identity cluster."""

    place_id: str
    merchant_name: str
    street: str | None = None
    city_state_zip: str | None = None
    phone: str | None = None
    website: str | None = None
    hours: tuple[str, ...] = field(default_factory=tuple)

    @property
    def address_lines(self) -> tuple[str, ...]:
        return tuple(
            line for line in (self.street, self.city_state_zip) if line
        )

    def is_complete(self) -> bool:
        """A profile usable for header composition needs at least a full address
        (street + city/state/zip); phone/website are optional extras."""
        return bool(self.street and self.city_state_zip)


def _clean_website(value: Any) -> str | None:
    """Normalize a Places website URL to the bare host[/path] a receipt prints.

    ``https://www.gelsons.com/stores/westlake-village`` -> ``gelsons.com`` so the
    swapped value matches the compact way receipts print a domain.
    """
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"^https?://", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^www\.", "", text, flags=re.IGNORECASE)
    host = text.split("/", 1)[0].strip().rstrip(".")
    return host.lower() or None


def _split_formatted_address(
    formatted: Any,
    short: Any = None,
) -> tuple[str | None, str | None]:
    """Split a Places ``formatted_address`` into (street, city_state_zip).

    ``"2734 Townsgate Rd, Westlake Village, CA 91361, USA"`` ->
    ``("2734 Townsgate Rd", "Westlake Village, CA 91361")``. The trailing country
    component is dropped; everything after the street collapses into the
    city/state/zip line the way receipts print it.
    """
    text = str(formatted or "").strip()
    if not text:
        return None, None
    parts = [part.strip() for part in text.split(",") if part.strip()]
    # Drop a trailing country token ("USA", "United States").
    if parts and parts[-1].upper() in {"USA", "US", "UNITED STATES"}:
        parts = parts[:-1]
    if not parts:
        return None, None
    street = parts[0]
    city_state_zip = ", ".join(parts[1:]) if len(parts) > 1 else None
    return street or None, city_state_zip or None


def _record_completeness(record: dict[str, Any]) -> int:
    """Rank Places records for the same place_id so the most complete wins."""
    score = 0
    for key in (
        "formatted_address",
        "phone_number",
        "website",
        "hours_summary",
    ):
        if record.get(key):
            score += 1
    return score


def extract_store_profiles(
    receipt_places: list[dict[str, Any]] | None,
) -> list[StoreProfile]:
    """Build one :class:`StoreProfile` per distinct ``place_id`` in the cache.

    When several cached rows share a ``place_id`` (one per receipt at that
    store), the most complete row wins. Rows without a ``place_id`` or without a
    parseable address are skipped.
    """
    if not isinstance(receipt_places, list):
        return []

    best_by_place: dict[str, dict[str, Any]] = {}
    for record in receipt_places:
        if not isinstance(record, dict):
            continue
        place_id = str(record.get("place_id") or "").strip()
        if not place_id:
            continue
        current = best_by_place.get(place_id)
        if current is None or _record_completeness(record) > _record_completeness(
            current
        ):
            best_by_place[place_id] = record

    profiles: list[StoreProfile] = []
    for place_id, record in best_by_place.items():
        street, city_state_zip = _split_formatted_address(
            record.get("formatted_address"),
            record.get("short_address"),
        )
        hours_summary = record.get("hours_summary")
        hours = tuple(
            str(entry).strip()
            for entry in (hours_summary or [])
            if str(entry).strip()
        )
        profile = StoreProfile(
            place_id=place_id,
            merchant_name=str(record.get("merchant_name") or "").strip(),
            street=street,
            city_state_zip=city_state_zip,
            phone=str(record.get("phone_number") or "").strip() or None,
            website=_clean_website(record.get("website")),
            hours=hours,
        )
        profiles.append(profile)
    # Stable order: most complete first, then by place_id for determinism.
    profiles.sort(key=lambda p: (not p.is_complete(), p.place_id))
    return profiles


def alternate_profiles(
    profiles: list[StoreProfile],
    own_place_id: str | None,
) -> list[StoreProfile]:
    """Complete profiles for a *different* store than ``own_place_id``.

    These are the coherent location-diversity sources for composing a header on a
    receipt whose own store is ``own_place_id``.
    """
    own = str(own_place_id or "").strip()
    return [
        profile
        for profile in profiles
        if profile.is_complete() and profile.place_id != own
    ]


def reflow_line_boxes(
    original_boxes: list[list[int]],
    new_tokens: list[str],
    *,
    min_gap: int = 4,
) -> list[list[int]] | None:
    """Lay ``new_tokens`` out across the x-span of one receipt line.

    Swapping a store field (e.g. an address line) for a different branch's value
    changes the token count and widths, so the new tokens must be re-placed in
    the original line's horizontal band. Each ``[x0, y0, x1, y1]`` (0-1000 scale)
    is allocated left-to-right, width proportional to token length, separated by
    a gap — guaranteeing strictly increasing, non-overlapping, in-band boxes so
    the loader's layout-integrity / valid-box gates accept the result.

    Returns one box per token, or ``None`` when the line has no usable span or
    the value cannot fit without degenerate (sub-1-unit) boxes — in which case
    the caller should skip the swap rather than emit corrupted geometry.
    """
    tokens = [str(token) for token in new_tokens if str(token).strip()]
    boxes = [
        box
        for box in original_boxes
        if isinstance(box, (list, tuple)) and len(box) == 4
    ]
    if not tokens or not boxes:
        return None

    x_start = min(int(box[0]) for box in boxes)
    x_end = max(int(box[2]) for box in boxes)
    y0 = min(int(box[1]) for box in boxes)
    y1 = max(int(box[3]) for box in boxes)
    span = x_end - x_start
    if span <= 0 or y1 <= y0:
        return None

    gap = min(min_gap, max(0, span // (len(tokens) * 4 + 1)))
    usable = span - gap * (len(tokens) - 1)
    if usable < len(tokens):  # cannot give every token even 1 unit of width
        return None

    weights = [max(1, len(token)) for token in tokens]
    total_weight = sum(weights)

    placed: list[list[int]] = []
    cursor = x_start
    for index, (token, weight) in enumerate(zip(tokens, weights)):
        if index == len(tokens) - 1:
            x1 = x_end  # pin the last token to the original right edge
        else:
            width = max(1, round(usable * weight / total_weight))
            x1 = min(x_end - 1, cursor + width)
        if x1 <= cursor:  # ran out of room -> degenerate box, skip the swap
            return None
        placed.append([cursor, y0, x1, y1])
        cursor = x1 + gap
        if cursor >= x_end and index < len(tokens) - 1:
            return None
    return placed


def store_profile_coverage(
    receipt_places: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Summarize how much coherent branch diversity the cache offers.

    ``distinct_complete_locations`` is the size of the header-composition pool;
    ``<= 1`` means there is no *alternate* branch to compose from (the cache is
    thin / stale for this merchant and would need more places fetched).
    """
    profiles = extract_store_profiles(receipt_places)
    complete = [profile for profile in profiles if profile.is_complete()]
    return {
        "distinct_locations": len(profiles),
        "distinct_complete_locations": len(complete),
        "with_phone": sum(1 for profile in complete if profile.phone),
        "with_website": sum(1 for profile in complete if profile.website),
        "supports_header_composition": len(complete) >= 2,
    }
