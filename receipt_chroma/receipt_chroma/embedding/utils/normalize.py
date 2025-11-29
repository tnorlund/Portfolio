"""Normalization utilities for embedding metadata.

These functions normalize phone numbers, addresses, and URLs for consistent
matching and storage in ChromaDB metadata.
"""

import re
from typing import Iterable, Optional


def normalize_phone(text: Optional[str]) -> str:
    """
    Normalize to a canonical 10-digit US phone number.
    - Remove non-digits
    - If starts with '1' and length > 10, drop the leading 1
    - If length > 10, keep the last 10 digits
    - If result length != 10, return empty string
    - Reject trivial sequences (all identical digits)
    """
    if not text:
        return ""
    digits = re.sub(r"\D+", "", str(text))
    if digits.startswith("1") and len(digits) > 10:
        digits = digits[1:]
    if len(digits) > 10:
        digits = digits[-10:]
    if len(digits) != 10:
        return ""
    if len(set(digits)) == 1:  # reject 0000000000, 1111111111, etc.
        return ""
    return digits


_SUFFIX_MAP = {
    "STREET": "ST",
    "ROAD": "RD",
    "AVENUE": "AVE",
    "BOULEVARD": "BLVD",
    "DRIVE": "DR",
    "LANE": "LN",
    "HIGHWAY": "HWY",
    "PARKWAY": "PKWY",
    "SUITE": "STE",
    "APARTMENT": "APT",
}


def normalize_address(text: Optional[str]) -> str:
    """
    Normalize a full address string for exact matching.
    - Uppercase
    - Collapse whitespace to single spaces
    - Trim leading/trailing punctuation and separators
    - Map common suffixes per _SUFFIX_MAP
    """
    if not text:
        return ""
    t = str(text).upper()
    t = re.sub(r"\s+", " ", t).strip(" ,.;:|/\\-\t")
    # Trim stray separators within tokens
    tokens = [tok.strip(",.;:|/\\-") for tok in t.split(" ") if tok]
    mapped = [_SUFFIX_MAP.get(tok, tok) for tok in tokens]
    return " ".join(mapped)


def _extract_address_candidates(words: Iterable[object]) -> list[str]:
    """Collect address candidates from word objects."""
    candidates: list[str] = []
    for word in words:
        try:
            ext = getattr(word, "extracted_data", None) or {}
        except AttributeError:
            continue
        try:
            if ext.get("type") == "address":
                val = str(ext.get("value") or "").strip()
                if val:
                    candidates.append(val)
        except (AttributeError, ValueError, TypeError):
            continue
    return [value.strip() for value in candidates if value.strip()]


def _combine_street_and_place(unique_sorted: list[str]) -> Optional[str]:
    """Combine street-containing and place-containing fragments if present."""
    street_idx = None
    place_idx = None
    for idx, val in enumerate(unique_sorted):
        if street_idx is None and re.search(r"\b\d{1,6}\b", val):
            street_idx = idx
    for idx, val in enumerate(unique_sorted):
        if idx == street_idx:
            continue
        if re.search(r"\b\d{5}(?:-\d{4})?\b", val) or re.search(
            r"\b[A-Z]{2}\b", val.upper()
        ):
            place_idx = idx
            break
    if street_idx is not None and place_idx is not None:
        return f"{unique_sorted[street_idx]} {unique_sorted[place_idx]}"
    return None


def build_full_address_from_words(words: Iterable[object]) -> str:
    """
    Build a receipt-level full address from word objects.
    Prefers extracted_data where type == "address"; picks longest value.
    """
    candidates = _extract_address_candidates(words)
    if not candidates:
        return ""

    # Deduplicate while preserving order by length (desc)
    unique_sorted = sorted(
        list(dict.fromkeys(candidates)), key=len, reverse=True
    )

    combined = _combine_street_and_place(unique_sorted)
    if combined:
        return normalize_address(combined)

    if len(unique_sorted) >= 2:
        return normalize_address(f"{unique_sorted[0]} {unique_sorted[1]}")

    return normalize_address(unique_sorted[0])


def build_full_address_from_lines(lines: Iterable[object]) -> str:
    """
    Build a receipt-level full address by joining line texts in reading order.
    """
    try:
        sorted_lines = sorted(lines, key=lambda x: int(getattr(x, "line_id")))
    except (TypeError, ValueError, AttributeError):
        sorted_lines = list(lines)
    parts = [
        str(getattr(ln, "text", "") or "")
        for ln in sorted_lines
        if not getattr(ln, "is_noise", False)
    ]
    return normalize_address(" ".join(parts))


def normalize_url(text: Optional[str]) -> str:
    """
    Normalize a URL for matching:
    - If it looks like an email (contains '@' without scheme), return empty
    - Lowercase hostname; remove scheme and leading www.
    - Drop query params and fragments
    - Normalize multiple slashes, strip trailing slash (except root)
    """
    if not text:
        return ""
    raw = str(text).strip()
    if "@" in raw and not re.match(r"^[a-z]+://", raw, re.I):
        return ""
    # Ensure we have a scheme to parse reliably
    tmp = raw if re.match(r"^[a-z]+://", raw, re.I) else f"http://{raw}"
    m = re.match(
        r"^(?P<scheme>[a-z]+)://(?P<host>[^/]+)(?P<path>/.*)?$", tmp, re.I
    )
    if not m:
        return ""
    host = m.group("host").lower()
    if host.startswith("www."):
        host = host[4:]
    path = m.group("path") or "/"
    path = re.split(r"[?#]", path)[0]
    path = re.sub(r"/+", "/", path)
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return f"{host}{path}"
