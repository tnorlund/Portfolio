"""Low-cost tiers for resolving a receipt's Google Place."""

from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from receipt_agent.utils.address_validation import is_address_like
from receipt_agent.utils.llm_factory import CostTrackingCallback, create_llm

logger = logging.getLogger(__name__)

_PLACE_ID_SENTINELS = {"", "null", "NO_RESULTS", "INVALID"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_TIER2_MODEL = "openai/gpt-oss-120b"


@dataclass
class ReceiptClues:
    """Compact place evidence extracted from receipt labels and metadata."""

    merchant_name: str | None = None
    address: str | None = None
    phone: str | None = None
    expected_state: str | None = None
    snippet: str = ""
    sources: dict[str, str] = field(default_factory=dict)


@dataclass
class PlaceCandidate:
    """A deduplicated Google Places candidate."""

    place_id: str
    merchant_name: str
    address: str | None
    phone: str | None
    score: float
    search_methods: set[str] = field(default_factory=set)

    def as_prompt_dict(self) -> dict[str, Any]:
        """Return only the compact fields needed by the Tier 2 picker."""
        return {
            "place_id": self.place_id,
            "merchant_name": self.merchant_name,
            "address": self.address,
            "phone": self.phone,
            "deterministic_score": round(self.score, 3),
            "search_methods": sorted(self.search_methods),
        }


class Tier2Selection(BaseModel):
    """Single-call structured selection from known Places candidates."""

    place_id: str | None = Field(
        default=None,
        description="One candidate place_id, or null when none matches",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


def empty_llm_stats() -> dict[str, int | float]:
    """Return the common zero-cost statistics shape."""
    return {
        "total_cost": 0.0,
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "llm_calls": 0,
    }


def _value(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split()).strip()
    return cleaned or None


def _label_is_usable(label: Any) -> bool:
    status = str(_value(label, "validation_status") or "").upper()
    return not status.endswith("INVALID")


def _group_labeled_words(details: Any) -> dict[str, dict[int, list[str]]]:
    words = {
        (_value(word, "line_id"), _value(word, "word_id")): word
        for word in (_value(details, "words") or [])
    }
    grouped: dict[str, dict[int, list[tuple[int, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for label in _value(details, "labels") or []:
        if not _label_is_usable(label):
            continue
        line_id = _value(label, "line_id")
        word_id = _value(label, "word_id")
        word = words.get((line_id, word_id))
        text = _clean(_value(word, "text")) if word is not None else None
        label_name = _clean(_value(label, "label"))
        if text and label_name and isinstance(line_id, int):
            grouped[label_name.upper()][line_id].append((word_id, text))

    result: dict[str, dict[int, list[str]]] = {}
    for label_name, lines in grouped.items():
        result[label_name] = {
            line_id: [text for _, text in sorted(words_on_line)]
            for line_id, words_on_line in lines.items()
        }
    return result


def _best_merchant(lines: dict[int, list[str]]) -> str | None:
    candidates = []
    for line_id, words in lines.items():
        text = _clean(" ".join(words))
        if text and re.search(r"[A-Za-z]", text):
            candidates.append((len(words), len(text), -line_id, text))
    return max(candidates)[-1] if candidates else None


def _best_phone(lines: dict[int, list[str]]) -> str | None:
    candidates = []
    for line_id, words in lines.items():
        text = _clean(" ".join(words))
        digits = _phone_digits(text)
        if text and len(digits) >= 10:
            candidates.append((len(digits) in {10, 11}, -line_id, text))
    return max(candidates)[-1] if candidates else None


def _combined_address(lines: dict[int, list[str]]) -> str | None:
    parts = []
    for _, words in sorted(lines.items()):
        text = _clean(" ".join(words))
        if text and text not in parts:
            parts.append(text)
    # Header cities are sometimes mislabeled as ADDRESS_LINE before the
    # actual street.  Start at the first line containing a street number so
    # that the deterministic address query remains usable.
    street_start = next(
        (index for index, part in enumerate(parts) if re.search(r"\d", part)),
        None,
    )
    if street_start is not None:
        parts = parts[street_start:]
    return _clean(", ".join(parts))


def _receipt_snippet(details: Any, limit: int = 1200) -> str:
    lines = sorted(
        _value(details, "lines") or [],
        key=lambda line: _value(line, "line_id") or 0,
    )
    text = "\n".join(
        value for line in lines[:15] if (value := _clean(_value(line, "text")))
    )
    return text[:limit]


def extract_receipt_clues(details: Any) -> ReceiptClues:
    """Extract labeled OCR clues, with current place fields as fallbacks."""
    grouped = _group_labeled_words(details)
    merchant = _best_merchant(grouped.get("MERCHANT_NAME", {}))
    address = _combined_address(grouped.get("ADDRESS_LINE", {}))
    phone = _best_phone(grouped.get("PHONE_NUMBER", {}))
    sources = {
        key: "receipt_labels"
        for key, value in {
            "merchant_name": merchant,
            "address": address,
            "phone": phone,
        }.items()
        if value
    }

    current = _value(details, "place")
    fallbacks = {
        "merchant_name": _clean(_value(current, "merchant_name")),
        "address": _clean(_value(current, "formatted_address")),
        "phone": _clean(_value(current, "phone_number")),
    }
    values = {
        "merchant_name": merchant,
        "address": address,
        "phone": phone,
    }
    for key, value in fallbacks.items():
        if not values[key] and value:
            values[key] = value
            sources[key] = "current_place"

    expected_state = _extract_state(address)
    return ReceiptClues(
        merchant_name=values["merchant_name"],
        address=values["address"],
        phone=values["phone"],
        expected_state=expected_state,
        snippet=_receipt_snippet(details),
        sources=sources,
    )


def _phone_digits(value: str | None) -> str:
    return "".join(character for character in (value or "") if character.isdigit())


def _phones_match(left: str | None, right: str | None) -> bool:
    left_digits = _phone_digits(left)
    right_digits = _phone_digits(right)
    if not left_digits or not right_digits:
        return False
    if len(left_digits) >= 10 and len(right_digits) >= 10:
        return left_digits[-10:] == right_digits[-10:]
    return left_digits == right_digits


def _normalized_text(value: str | None) -> str:
    return "".join(
        character.lower()
        for character in (value or "")
        if character.isalnum() or character.isspace()
    ).strip()


def _names_match(left: str | None, right: str | None) -> bool:
    first = _normalized_text(left)
    second = _normalized_text(right)
    return bool(first and second) and (first in second or second in first)


def _street(value: str | None) -> str:
    street = (value or "").split(",", maxsplit=1)[0]
    # Unit notation is especially inconsistent between receipt OCR and
    # Google Places ("#107" vs "Suite 107").  It is not part of the street
    # identity, so remove it before comparing the primary address line.
    street = re.sub(
        r"(?:\b(?:suite|ste|unit|apt|apartment)\b|#)\s*[A-Z0-9-]+.*$",
        "",
        street,
        flags=re.IGNORECASE,
    )
    normalized = _normalized_text(street)
    replacements = {
        "street": "st",
        "avenue": "ave",
        "boulevard": "blvd",
        "road": "rd",
        "drive": "dr",
        "lane": "ln",
        "highway": "hwy",
    }
    return " ".join(replacements.get(token, token) for token in normalized.split())


def _addresses_match(left: str | None, right: str | None) -> bool:
    first = _street(left)
    second = _street(right)
    return bool(first and second) and (first in second or second in first)


def _extract_state(value: str | None) -> str | None:
    if not value:
        return None
    upper = value.upper()
    match = re.search(r"\b([A-Z]{2})\s+\d{5}(?:-\d{4})?\b", upper)
    if match:
        return match.group(1)
    match = re.search(r",\s*([A-Z]{2})\b", upper)
    return match.group(1) if match else None


def _place_phone(place: Any) -> str | None:
    return _clean(
        _value(place, "formatted_phone_number")
        or _value(place, "international_phone_number")
    )


def _candidate_score(clues: ReceiptClues, place: Any, search_method: str) -> float:
    name_match = _names_match(clues.merchant_name, _value(place, "name"))
    address_match = _addresses_match(clues.address, _value(place, "formatted_address"))
    phone_match = _phones_match(clues.phone, _place_phone(place))

    # A search method is not evidence by itself.  In particular, the Places
    # client's phone fallback is a broad text search and can return an
    # unrelated business.  Require the returned place to agree with the
    # primary receipt clue before granting that method's base confidence.
    if search_method == "phone":
        if not phone_match:
            return 0.0
        return min(
            1.0, 0.95 + (0.03 if address_match else 0.0) + (0.02 if name_match else 0.0)
        )
    if search_method == "address":
        if not address_match:
            return 0.0
        return min(
            1.0, 0.90 + (0.05 if name_match else 0.0) + (0.05 if phone_match else 0.0)
        )
    if search_method == "text":
        if not name_match:
            return 0.0
        return min(
            1.0,
            0.75 + (0.15 if address_match else 0.0) + (0.10 if phone_match else 0.0),
        )

    # Existing place IDs only reach Tier 0 when their labeled evidence is
    # consistent.  Keep inconsistent existing candidates available to the
    # structured picker, but never let them qualify for Tier 1 on base score.
    return (
        0.50
        + (0.15 if name_match else 0.0)
        + (0.15 if address_match else 0.0)
        + (0.20 if phone_match else 0.0)
    )


def _primary_evidence_matches(
    place: Any, clues: ReceiptClues, search_method: str
) -> bool:
    """Reject broad Places fallbacks that disagree with their receipt clue."""
    if search_method == "phone":
        return _phones_match(clues.phone, _place_phone(place))
    if search_method == "address":
        return _addresses_match(
            clues.address, _clean(_value(place, "formatted_address"))
        )
    if search_method == "text":
        return _names_match(clues.merchant_name, _clean(_value(place, "name")))
    return True


def _place_is_usable(place: Any, clues: ReceiptClues) -> bool:
    place_id = _clean(_value(place, "place_id"))
    name = _clean(_value(place, "name"))
    if not place_id or not name or is_address_like(name):
        return False
    place_types = {
        str(place_type).lower() for place_type in (_value(place, "types") or [])
    }
    address_types = {"premise", "street_address", "subpremise"}
    business_types = {"establishment", "point_of_interest"}
    if place_types & address_types and not place_types & business_types:
        return False
    result_state = _extract_state(_clean(_value(place, "formatted_address")))
    return not (
        clues.expected_state and result_state and result_state != clues.expected_state
    )


def _to_candidate(
    place: Any, clues: ReceiptClues, search_method: str
) -> PlaceCandidate:
    return PlaceCandidate(
        place_id=str(_value(place, "place_id")),
        merchant_name=str(_value(place, "name")),
        address=_clean(_value(place, "formatted_address")),
        phone=_place_phone(place),
        score=_candidate_score(clues, place, search_method),
        search_methods={search_method},
    )


def _merge_candidate(
    candidates: dict[str, PlaceCandidate], candidate: PlaceCandidate
) -> None:
    existing = candidates.get(candidate.place_id)
    if existing is None:
        candidates[candidate.place_id] = candidate
        return
    existing.score = max(existing.score, candidate.score)
    existing.search_methods.update(candidate.search_methods)
    existing.address = existing.address or candidate.address
    existing.phone = existing.phone or candidate.phone


def _existing_candidate(
    details: Any, places_client: Any, clues: ReceiptClues
) -> PlaceCandidate | None:
    current = _value(details, "place")
    place_id = _clean(_value(current, "place_id"))
    if not place_id or place_id in _PLACE_ID_SENTINELS:
        return None
    try:
        place = places_client.get_place_details(place_id)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Tier 0 place_id lookup failed")
        return None
    if not _place_is_usable(place, clues):
        return None
    return _to_candidate(place, clues, "existing_place_id")


def _tier0_is_consistent(candidate: PlaceCandidate, clues: ReceiptClues) -> bool:
    checks = []
    if clues.phone and clues.sources.get("phone") == "receipt_labels":
        checks.append(_phones_match(clues.phone, candidate.phone))
    if clues.address and clues.sources.get("address") == "receipt_labels":
        checks.append(_addresses_match(clues.address, candidate.address))
    return bool(checks) and all(checks)


def collect_candidates(
    places_client: Any,
    clues: ReceiptClues,
    initial: list[PlaceCandidate] | None = None,
) -> list[PlaceCandidate]:
    """Run the deterministic phone/address/text Places cascade."""
    candidates = {candidate.place_id: candidate for candidate in (initial or [])}
    searches = (
        ("phone", clues.phone, places_client.search_by_phone),
        ("address", clues.address, places_client.search_by_address),
        ("text", clues.merchant_name, places_client.search_by_text),
    )
    for method, query, search in searches:
        if not query:
            continue
        try:
            place = search(query)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Tier 1 Places %s search failed", method)
            continue
        if not _place_is_usable(place, clues):
            continue
        if not _primary_evidence_matches(place, clues, method):
            logger.warning(
                "Tier 1 rejected %s result because returned place did not "
                "match the receipt's primary clue",
                method,
            )
            continue
        _merge_candidate(candidates, _to_candidate(place, clues, method))
    return sorted(
        candidates.values(),
        key=lambda candidate: candidate.score,
        reverse=True,
    )


def select_deterministic_candidate(
    candidates: list[PlaceCandidate],
    minimum_confidence: float,
) -> PlaceCandidate | None:
    """Select one high-confidence candidate, rejecting high-score conflicts."""
    qualifying = [
        candidate for candidate in candidates if candidate.score >= minimum_confidence
    ]
    if not qualifying:
        return None
    if len({candidate.place_id for candidate in qualifying}) > 1:
        return None
    return qualifying[0]


def _result(
    candidate: PlaceCandidate,
    *,
    tier: str,
    confidence: float,
    reasoning: str,
) -> dict[str, Any]:
    values = {
        "place_id": candidate.place_id,
        "merchant_name": candidate.merchant_name,
        "address": candidate.address,
        "phone_number": candidate.phone,
    }
    fields_found = [key for key, value in values.items() if value]
    return {
        **values,
        "confidence": confidence,
        "field_confidence": dict.fromkeys(fields_found, confidence),
        "reasoning": reasoning,
        "sources": dict.fromkeys(fields_found, "google_places"),
        "search_methods_used": sorted(candidate.search_methods),
        "fields_found": fields_found,
        "found": True,
        "type": "deterministic" if tier != "tier2" else "llm_decided",
        "resolution_tier": tier,
    }


async def _select_with_llm(
    clues: ReceiptClues,
    candidates: list[PlaceCandidate],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    callback = CostTrackingCallback()
    model = os.environ.get("FIX_PLACE_TIER2_MODEL", _TIER2_MODEL)
    minimum_confidence = float(os.environ.get("FIX_PLACE_TIER2_MIN_CONFIDENCE", "0.85"))
    llm = create_llm(
        model=model,
        temperature=0.0,
        timeout=60,
        reasoning=True,
        stream_usage=True,
    ).with_structured_output(Tier2Selection)
    payload = {
        "receipt_clues": {
            "merchant_name": clues.merchant_name,
            "address": clues.address,
            "phone": clues.phone,
        },
        "receipt_text": clues.snippet,
        "candidates": [candidate.as_prompt_dict() for candidate in candidates[:5]],
    }
    messages = [
        SystemMessage(
            content=(
                "Choose the single Google Places candidate supported by the "
                "receipt. Return null when evidence conflicts or is too weak. "
                "Never invent a place_id."
            )
        ),
        HumanMessage(content=json.dumps(payload, ensure_ascii=True)),
    ]
    try:
        selection = await llm.ainvoke(
            messages,
            config={"callbacks": [callback.handler]},
        )
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Tier 2 structured place selection failed")
        return None, callback.get_stats()

    if isinstance(selection, dict):
        selection = Tier2Selection.model_validate(selection)
    selected = next(
        (
            candidate
            for candidate in candidates
            if candidate.place_id == selection.place_id
        ),
        None,
    )
    if selected is None or selection.confidence < minimum_confidence:
        return None, callback.get_stats()
    return (
        _result(
            selected,
            tier="tier2",
            confidence=selection.confidence,
            reasoning=(
                f"Single structured picker selected a known Places candidate: "
                f"{selection.reasoning}"
            ),
        ),
        callback.get_stats(),
    )


async def resolve_tiered_place(
    details: Any, places_client: Any
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Try Tiers 0-2 and return ``None`` when Tier 3 must run."""
    if places_client is None:
        logger.warning("Places client unavailable; escalating to Tier 3")
        return None, empty_llm_stats()
    clues = extract_receipt_clues(details)
    existing = _existing_candidate(details, places_client, clues)
    if existing and _tier0_is_consistent(existing, clues):
        return (
            _result(
                existing,
                tier="tier0",
                confidence=1.0,
                reasoning=(
                    "Existing place_id matches receipt-labeled phone/address "
                    "and current Google Places details."
                ),
            ),
            empty_llm_stats(),
        )

    candidates = collect_candidates(
        places_client,
        clues,
        initial=[existing] if existing else None,
    )
    tier1_minimum = float(os.environ.get("FIX_PLACE_TIER1_MIN_CONFIDENCE", "0.90"))
    deterministic = select_deterministic_candidate(candidates, tier1_minimum)
    if deterministic:
        return (
            _result(
                deterministic,
                tier="tier1",
                confidence=deterministic.score,
                reasoning=(
                    "Deterministic Places cascade produced one "
                    "high-confidence candidate."
                ),
            ),
            empty_llm_stats(),
        )

    tier2_enabled = (
        os.environ.get("FIX_PLACE_TIER2_ENABLED", "true").lower() not in _FALSE_VALUES
    )
    if candidates and tier2_enabled:
        return await _select_with_llm(clues, candidates)
    return None, empty_llm_stats()
