"""
Shared utilities for financial validation sub-agents.
"""

from typing import Optional

from receipt_dynamo.amounts import parse_receipt_amount


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
    return parse_receipt_amount(text)
