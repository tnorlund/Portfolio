"""
Shared utilities for financial validation sub-agents.
"""

import re
from typing import Optional


def extract_number(text: str) -> Optional[float]:
    """
    Extract numeric value from text, handling currency symbols and formatting.

    Handles:
    - Currency symbols ($, €, £, ¥, ₹)
    - Thousands separators (commas)
    - Negative numbers (parentheses or minus signs)
    - Decimal points

    Args:
        text: Text string that may contain a numeric value

    Returns:
        Extracted float value, or None if no valid number found

    Examples:
        >>> extract_number("$45.67")
        45.67
        >>> extract_number("(3.49)")
        -3.49
        >>> extract_number("1,234.56")
        1234.56
        >>> extract_number("invalid")
        None
    """
    if not text:
        return None

    # Remove currency symbols and clean up
    clean_text = re.sub(r"[$€£¥₹]", "", text)

    # Detect accounting-style negatives: (123.45) or -123.45
    # Check for parentheses wrapping the entire text before cleaning
    is_negative = text.strip().startswith("(") and text.strip().endswith(")")

    clean_text = re.sub(r"[^\d.,-]", "", clean_text)
    clean_text = clean_text.replace(",", "")

    # Also check for minus sign in cleaned text
    if "-" in clean_text:
        is_negative = True
    clean_text = clean_text.replace("-", "")

    try:
        value = float(clean_text)
        return -value if is_negative else value
    except ValueError:
        return None
