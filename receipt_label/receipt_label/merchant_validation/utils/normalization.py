"""Text normalization utilities for merchant validation."""

import re


def normalize_address(address: str) -> str:
    """
    Normalize an address string for consistent comparison.

    Performs the following transformations:
    - Converts to lowercase
    - Expands common abbreviations (st. → street, ave. → avenue, etc.)
    - Removes extra whitespace
    - Removes special characters except spaces, numbers, and letters

    Args:
        address: The address string to normalize

    Returns:
        Normalized address string, or empty string if input is None/empty
    """
    if not address:
        return ""

    # Convert to lowercase
    text = address.lower()

    # Common abbreviations and their expansions
    abbreviations = {
        r"\bst\.?\b": "street",
        r"\bave\.?\b": "avenue",
        r"\bblvd\.?\b": "boulevard",
        r"\brd\.?\b": "road",
        r"\bdr\.?\b": "drive",
        r"\bln\.?\b": "lane",
        r"\bct\.?\b": "court",
        r"\bpl\.?\b": "place",
        r"\bpkwy\.?\b": "parkway",
        r"\bhwy\.?\b": "highway",
        r"\bn\.?\b": "north",
        r"\bs\.?\b": "south",
        r"\be\.?\b": "east",
        r"\bw\.?\b": "west",
        r"\bne\.?\b": "northeast",
        r"\bnw\.?\b": "northwest",
        r"\bse\.?\b": "southeast",
        r"\bsw\.?\b": "southwest",
        r"\bapt\.?\b": "apartment",
        r"\bste\.?\b": "suite",
        r"\bfl\.?\b": "floor",
    }

    # Apply abbreviation expansions
    for abbrev, expansion in abbreviations.items():
        text = re.sub(abbrev, expansion, text)

    # Remove special characters except letters, numbers, and spaces
    text = re.sub(r"[^a-z0-9\s]", " ", text)

    # Normalize whitespace
    text = " ".join(text.split())

    return text


def normalize_phone(phone: str) -> str:
    """
    Normalize a phone number by removing all non-digit characters.

    Args:
        phone: Phone number string to normalize

    Returns:
        String containing only digits, or empty string if input is None/empty
    """
    return re.sub(r"\D", "", phone) if phone else ""


def normalize_text(text: str) -> str:
    """
    Generic text normalization for comparison.

    Performs:
    - Converts to lowercase
    - Removes special characters except letters, numbers, and spaces
    - Normalizes whitespace

    Args:
        text: Text to normalize

    Returns:
        Normalized text string, or empty string if input is None/empty
    """
    if not text:
        return ""

    # Convert to lowercase
    text = text.lower()

    # Remove special characters except letters, numbers, and spaces
    text = re.sub(r"[^a-z0-9\s]", " ", text)

    # Normalize whitespace
    text = " ".join(text.split())

    return text


def preprocess_for_comparison(text: str) -> str:
    """
    Minimal text preprocessing for fuzzy comparison.

    Performs:
    - Converts to lowercase
    - Strips leading/trailing whitespace
    - Normalizes internal whitespace

    Args:
        text: Text to preprocess

    Returns:
        Preprocessed text string, or empty string if input is None/empty
    """
    if not text:
        return ""

    # Convert to lowercase
    text = text.lower()

    # Strip and normalize whitespace
    text = " ".join(text.split())

    return text


def format_canonical_merchant_name(name: str) -> str:
    """
    Format a merchant name for canonical representation.

    Performs:
    - Strips whitespace
    - Converts to title case (preserving apostrophes)
    - Removes dashes only when surrounded by spaces
    - Normalizes whitespace

    Args:
        name: Merchant name to format

    Returns:
        Formatted merchant name, or empty string if input is None/empty
    """
    if not name:
        return ""

    # Strip whitespace
    name = name.strip()

    # Remove dashes only when surrounded by spaces (not dashes without spaces)
    name = re.sub(r"\s+-\s+", " ", name)  # Only remove " - " not "-"

    # Normalize whitespace first
    name = re.sub(r"\s+", " ", name)

    # Apply proper title case while preserving apostrophes and handling dashes
    words = []
    for word in name.split():
        if "'" in word:
            # Handle apostrophes - only capitalize the first letter, not after apostrophe
            parts = word.split("'")
            formatted_parts = []
            for i, part in enumerate(parts):
                if i == 0:
                    # First part - capitalize first letter only
                    formatted_parts.append(part.capitalize())
                else:
                    # After apostrophe - keep lowercase (e.g., "mcdonald's" not "mcdonald'S")
                    formatted_parts.append(part.lower())
            words.append("'".join(formatted_parts))
        elif "-" in word:
            # Handle dashes - capitalize after dash too (e.g., "cvs-pharmacy" -> "Cvs-Pharmacy")
            parts = word.split("-")
            formatted_parts = [part.capitalize() for part in parts]
            words.append("-".join(formatted_parts))
        elif "/" in word:
            # Handle forward slashes - capitalize after slash too (e.g., "cvs/pharmacy" -> "Cvs/Pharmacy")
            parts = word.split("/")
            formatted_parts = [part.capitalize() for part in parts]
            words.append("/".join(formatted_parts))
        else:
            # No special characters - just capitalize first letter
            words.append(word.capitalize())

    return " ".join(words)
