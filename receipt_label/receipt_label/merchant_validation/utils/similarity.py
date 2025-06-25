"""Similarity comparison utilities for merchant validation."""

from fuzzywuzzy import fuzz

from .normalization import (normalize_address, normalize_phone,
                            preprocess_for_comparison)


def get_name_similarity(name1: str, name2: str) -> int:
    """
    Calculate similarity score between two business names.

    Uses fuzzy string matching with token set ratio to handle:
    - Word order differences (e.g., "Coffee Starbucks" vs "Starbucks Coffee")
    - Extra words (e.g., "Starbucks" vs "Starbucks Coffee Company")

    Args:
        name1: First business name
        name2: Second business name

    Returns:
        Similarity score from 0 to 100, where 100 is exact match
    """
    p_name1 = preprocess_for_comparison(name1)
    p_name2 = preprocess_for_comparison(name2)

    if not p_name1 or not p_name2:
        return 0

    return fuzz.token_set_ratio(p_name1, p_name2)


def get_address_similarity(addr1: str, addr2: str) -> int:
    """
    Calculate similarity score between two addresses.

    Uses comprehensive address normalization before comparison to handle:
    - Abbreviation differences (St. vs Street)
    - Directional differences (N vs North)
    - Punctuation and formatting differences

    Args:
        addr1: First address
        addr2: Second address

    Returns:
        Similarity score from 0 to 100, where 100 is exact match
    """
    p_addr1 = normalize_address(addr1)
    p_addr2 = normalize_address(addr2)

    if not p_addr1 or not p_addr2:
        return 0

    return fuzz.token_set_ratio(p_addr1, p_addr2)


def get_phone_similarity(ph1: str, ph2: str) -> int:
    """
    Calculate similarity score between two phone numbers.

    Normalizes phones to digits only and requires exact match.
    Phone numbers must have at least 7 digits to be considered valid.

    Args:
        ph1: First phone number
        ph2: Second phone number

    Returns:
        100 if phones match exactly after normalization, 0 otherwise
    """
    p_ph1 = normalize_phone(ph1)
    p_ph2 = normalize_phone(ph2)

    # Require at least 7 digits for valid comparison
    if not p_ph1 or not p_ph2 or len(p_ph1) < 7 or len(p_ph2) < 7:
        return 0

    return 100 if p_ph1 == p_ph2 else 0
