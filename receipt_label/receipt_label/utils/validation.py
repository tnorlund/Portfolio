import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Union

from ..models.receipt import ReceiptLine
from .address import compare_addresses, normalize_address
from .date import is_valid_date, is_valid_time, parse_datetime

logger = logging.getLogger(__name__)


def validate_business_name(
    receipt_name: str, api_name: str
) -> Tuple[bool, str, float]:
    """Validate business name against Places API data.

    Args:
        receipt_name: Business name from receipt
        api_name: Business name from Places API

    Returns:
        Tuple of (is_valid, message, confidence)
    """
    if not receipt_name or not api_name:
        return False, "Missing business name", 0.0

    # Normalize names
    receipt_name = receipt_name.lower()
    api_name = api_name.lower()

    # Remove common words
    common_words = {
        "the",
        "a",
        "an",
        "and",
        "&",
        "inc",
        "llc",
        "ltd",
        "corp",
        "corporation",
    }
    receipt_words = set(
        w for w in receipt_name.split() if w not in common_words
    )
    api_words = set(w for w in api_name.split() if w not in common_words)

    # Calculate word overlap
    intersection = receipt_words.intersection(api_words)
    union = receipt_words.union(api_words)

    # Calculate confidence
    confidence = len(intersection) / len(union) if union else 0.0

    # Check if one name is subset of other
    if receipt_words.issubset(api_words) or api_words.issubset(receipt_words):
        confidence = max(confidence, 0.8)

    is_valid = confidence >= 0.8
    message = (
        "Business names match"
        if is_valid
        else "Business names do not match sufficiently"
    )

    return is_valid, message, confidence


def validate_phone_number(
    receipt_phone: str, api_phone: str
) -> Tuple[bool, str, float]:
    """Validate phone number against Places API data.

    Args:
        receipt_phone: Phone number from receipt
        api_phone: Phone number from Places API

    Returns:
        Tuple of (is_valid, message, confidence)
    """
    if not receipt_phone or not api_phone:
        return False, "Missing phone number", 0.0

    # Normalize phone numbers to digits only
    receipt_digits = "".join(c for c in receipt_phone if c.isdigit())
    api_digits = "".join(c for c in api_phone if c.isdigit())

    # Compare last 10 digits
    receipt_digits = (
        receipt_digits[-10:] if len(receipt_digits) >= 10 else receipt_digits
    )
    api_digits = api_digits[-10:] if len(api_digits) >= 10 else api_digits

    is_valid = receipt_digits == api_digits
    confidence = 1.0 if is_valid else 0.0
    message = (
        "Phone numbers match" if is_valid else "Phone numbers do not match"
    )

    return is_valid, message, confidence


def validate_address(
    receipt_addr: str, api_addr: str
) -> Tuple[bool, str, float]:
    """Validate address against Places API data.

    Args:
        receipt_addr: Address from receipt
        api_addr: Address from Places API

    Returns:
        Tuple of (is_valid, message, confidence)
    """
    if not receipt_addr or not api_addr:
        return False, "Missing address", 0.0

    confidence = compare_addresses(receipt_addr, api_addr)
    is_valid = confidence >= 0.8
    message = (
        "Addresses match"
        if is_valid
        else "Addresses do not match sufficiently"
    )

    return is_valid, message, confidence


def validate_datetime(
    date_str: str,
    time_str: Optional[str] = None,
    max_age_days: Optional[int] = None,
) -> Tuple[bool, str, float]:
    """Validate date and time.

    Args:
        date_str: Date string to validate
        time_str: Optional time string to validate
        max_age_days: Maximum age of receipt in days

    Returns:
        Tuple of (is_valid, message, confidence)
    """
    if not date_str:
        return False, "Missing date", 0.0

    # Validate date format
    if not is_valid_date(date_str):
        return False, f"Invalid date format: {date_str}", 0.0

    # Validate time format if provided
    if time_str and not is_valid_time(time_str):
        return False, f"Invalid time format: {time_str}", 0.0

    # Parse datetime
    dt = parse_datetime(date_str, time_str)
    if not dt:
        return False, "Could not parse date/time", 0.0

    # Check age if max_age_days specified
    if max_age_days is not None:
        age = (datetime.now() - dt).days
        if age > max_age_days:
            return False, f"Receipt too old: {age} days", 0.0

    return True, "Date/time valid", 1.0


def validate_amounts(
    subtotal: float, tax: float, total: float, tolerance: float = 0.01
) -> Tuple[bool, str, float]:
    """Validate receipt amounts.

    Args:
        subtotal: Subtotal amount
        tax: Tax amount
        total: Total amount
        tolerance: Maximum allowed difference

    Returns:
        Tuple of (is_valid, message, confidence)
    """
    try:
        # Convert to float if strings
        if isinstance(subtotal, str):
            subtotal = float(subtotal)
        if isinstance(tax, str):
            tax = float(tax)
        if isinstance(total, str):
            total = float(total)

        # Check for negative amounts
        if any(x < 0 for x in [subtotal, tax, total]):
            return False, "Negative amount found", 0.0

        # Check total matches
        expected_total = subtotal + tax
        if abs(expected_total - total) <= tolerance:
            return True, "Amounts match", 1.0
        else:
            return False, f"Total mismatch: {subtotal} + {tax} != {total}", 0.0

    except (ValueError, TypeError) as e:
        return False, f"Invalid amount format: {str(e)}", 0.0


def validate_receipt_data(
    data: Dict, required_fields: Optional[List[str]] = None
) -> Tuple[bool, List[str], float]:
    """Validate receipt data completeness.

    Args:
        data: Receipt data to validate
        required_fields: List of required fields

    Returns:
        Tuple of (is_valid, missing_fields, confidence)
    """
    if not required_fields:
        required_fields = [
            "business_name",
            "address",
            "date",
            "subtotal",
            "tax",
            "total",
        ]

    missing_fields = []
    for field in required_fields:
        if field not in data or not data[field]:
            missing_fields.append(field)

    if not missing_fields:
        return True, [], 1.0

    confidence = 1.0 - (len(missing_fields) / len(required_fields))
    return False, missing_fields, confidence


def validate_receipt_format(
    receipt_data: Dict,
    receipt_lines: List[ReceiptLine],
    format_rules: Optional[Dict] = None,
) -> Tuple[bool, List[str], float]:
    """Validate receipt format against rules.

    Args:
        receipt_data: Receipt data to validate
        receipt_lines: List of receipt lines
        format_rules: Optional format rules to check

    Returns:
        Tuple of (is_valid, violations, confidence)
    """
    if not format_rules:
        format_rules = {
            "max_line_length": 80,
            "min_line_length": 10,
            "max_words_per_line": 20,
            "min_words_per_line": 1,
            "required_sections": ["header", "items", "totals"],
        }
    else:
        # Ensure all required rules are present
        default_rules = {
            "max_line_length": 80,
            "min_line_length": 10,
            "max_words_per_line": 20,
            "min_words_per_line": 1,
            "required_sections": ["header", "items", "totals"],
        }
        format_rules = {**default_rules, **format_rules}

    violations = []
    total_checks = 0
    passed_checks = 0

    # Check line lengths
    for line in receipt_lines:
        total_checks += 2
        if len(line.text) > format_rules["max_line_length"]:
            violations.append(f"Line too long: {line.text}")
        else:
            passed_checks += 1
        if len(line.text) < format_rules["min_line_length"]:
            violations.append(f"Line too short: {line.text}")
        else:
            passed_checks += 1

    # Check word counts
    for line in receipt_lines:
        total_checks += 2
        words = line.text.split()
        if len(words) > format_rules["max_words_per_line"]:
            violations.append(f"Too many words in line: {line.text}")
        else:
            passed_checks += 1
        if len(words) < format_rules["min_words_per_line"]:
            violations.append(f"Too few words in line: {line.text}")
        else:
            passed_checks += 1

    # Check required sections
    if "sections" in receipt_data:
        total_checks += 1
        missing_sections = set(format_rules["required_sections"]) - set(
            receipt_data["sections"]
        )
        if missing_sections:
            violations.append(
                f"Missing required sections: {', '.join(missing_sections)}"
            )
        else:
            passed_checks += 1

    confidence = passed_checks / total_checks if total_checks > 0 else 0.0
    is_valid = len(violations) == 0

    return is_valid, violations, confidence
