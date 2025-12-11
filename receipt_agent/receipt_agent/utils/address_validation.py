"""
Utilities for address validation and sanitization.

This module provides shared functionality for validating and sanitizing
addresses to prevent reasoning text and malformed ZIP codes.
"""

import re
from typing import Optional

# Module-level constant
_BAD_ADDRESS_MARKERS = ["?", "actually need", "lind0"]


def is_address_like(name: Optional[str]) -> bool:
    """
    Check if a name looks like an address rather than a business name.

    Args:
        name: Name to check

    Returns:
        True if name looks like an address
    """
    if not name:
        return False

    name_lower = name.lower().strip()

    # Check if it starts with a number
    if name_lower and name_lower[0].isdigit():
        street_indicators = [
            "st",
            "street",
            "ave",
            "avenue",
            "blvd",
            "boulevard",
            "rd",
            "road",
            "dr",
            "drive",
            "ln",
            "lane",
            "way",
            "ct",
            "court",
            "pl",
            "place",
            "cir",
            "circle",
        ]
        # Split into words and check if any word matches a street indicator
        words = name_lower.split()
        if any(word.rstrip(".,") in street_indicators for word in words):
            return True

    return False


def sanitize_address(
    address: Optional[str],
    previous_value: Optional[str] = None,
) -> Optional[str]:
    """
    Sanitize an address to remove reasoning text and fix malformed ZIPs.

    Args:
        address: Address to sanitize
        previous_value: Previous address value (returned if sanitization fails)

    Returns:
        Sanitized address, previous_value, or None
    """
    if not address:
        return previous_value

    cleaned = address.strip().strip('"')
    lower = cleaned.lower()

    # Reject addresses that clearly contain reasoning/commentary
    if any(marker in lower for marker in _BAD_ADDRESS_MARKERS):
        return previous_value

    # Fix malformed ZIPs like 913001 → 91301 (truncate extra trailing digits)
    # Pattern: find 5 digits followed by 1-2 extra digits at the end
    cleaned = re.sub(r"(\d{5})(\d{1,2})(?=\D*$)", r"\1", cleaned)

    return cleaned


def is_clean_address(address: Optional[str]) -> bool:
    """
    Check if an address is clean (no reasoning/commentary, no malformed ZIPs).

    Args:
        address: Address to check

    Returns:
        True if address is clean
    """
    if not address:
        return True

    lower = address.lower()

    # Reject if contains reasoning/commentary
    if "?" in address or "actually need" in lower or "lind0" in lower:
        return False

    # Reject obviously malformed ZIPs (6+ consecutive digits)
    if re.search(r"\d{6,}", address.replace(" ", "")):
        return False

    return True
