from typing import Dict, List, Optional, Union, Tuple
import re
import logging
from datetime import datetime
from .date import parse_datetime, is_valid_date, is_valid_time
from .address import normalize_address, compare_addresses

logger = logging.getLogger(__name__)


def validate_business_name(
    receipt_name: str, api_name: str, min_confidence: float = 0.8
) -> Tuple[bool, str, float]:
    """Validate business name against Places API data.

    Args:
        receipt_name: Business name from receipt
        api_name: Business name from Places API
        min_confidence: Minimum confidence threshold

    Returns:
        Tuple of (is_valid, message, confidence)
    """
    if not receipt_name or not api_name:
        return False, "Missing business name", 0.0

    # Normalize names
    receipt_norm = receipt_name.lower()
    api_norm = api_name.lower()

    # Remove common words and punctuation
    receipt_norm = re.sub(r"[^\w\s]", " ", receipt_norm)
    api_norm = re.sub(r"[^\w\s]", " ", api_norm)

    # Split into words
    receipt_words = set(receipt_norm.split())
    api_words = set(api_norm.split())

    # Calculate word overlap
    overlap = len(receipt_words.intersection(api_words))
    total = max(len(receipt_words), len(api_words))

    if total == 0:
        return False, "Invalid business names", 0.0

    confidence = overlap / total

    if confidence >= min_confidence:
        return True, "Business names match", confidence
    else:
        return (
            False,
            f"Business names don't match: '{receipt_name}' vs '{api_name}'",
            confidence,
        )


def validate_phone_number(
    receipt_phone: str, api_phone: str, min_confidence: float = 0.9
) -> Tuple[bool, str, float]:
    """Validate phone number against Places API data.

    Args:
        receipt_phone: Phone number from receipt
        api_phone: Phone number from Places API
        min_confidence: Minimum confidence threshold

    Returns:
        Tuple of (is_valid, message, confidence)
    """
    if not receipt_phone or not api_phone:
        return False, "Missing phone number", 0.0

    # Remove non-numeric characters
    receipt_clean = re.sub(r"\D", "", receipt_phone)
    api_clean = re.sub(r"\D", "", api_phone)

    # Compare cleaned numbers
    if receipt_clean == api_clean:
        return True, "Phone numbers match", 1.0
    else:
        return (
            False,
            f"Phone numbers don't match: '{receipt_phone}' vs '{api_phone}'",
            0.0,
        )


def validate_address(
    receipt_address: str, api_address: str, min_confidence: float = 0.8
) -> Tuple[bool, str, float]:
    """Validate address against Places API data.

    Args:
        receipt_address: Address from receipt
        api_address: Address from Places API
        min_confidence: Minimum confidence threshold

    Returns:
        Tuple of (is_valid, message, confidence)
    """
    if not receipt_address or not api_address:
        return False, "Missing address", 0.0

    # Compare addresses
    confidence = compare_addresses(receipt_address, api_address)

    if confidence >= min_confidence:
        return True, "Addresses match", confidence
    else:
        return (
            False,
            f"Addresses don't match: '{receipt_address}' vs '{api_address}'",
            confidence,
        )


def validate_datetime(
    date_str: str, time_str: Optional[str] = None, max_age_days: Optional[int] = None
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
    receipt_data: Dict, format_rules: Optional[Dict] = None
) -> Tuple[bool, List[str], float]:
    """Validate receipt format against rules.

    Args:
        receipt_data: Receipt data to validate
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

    violations = []

    # Check line lengths
    for line in receipt_data.get("lines", []):
        line_length = len(line.get("text", ""))
        if line_length > format_rules["max_line_length"]:
            violations.append(f"Line too long: {line_length} chars")
        if line_length < format_rules["min_line_length"]:
            violations.append(f"Line too short: {line_length} chars")

        # Check word count
        word_count = len(line.get("words", []))
        if word_count > format_rules["max_words_per_line"]:
            violations.append(f"Too many words: {word_count}")
        if word_count < format_rules["min_words_per_line"]:
            violations.append(f"Too few words: {word_count}")

    # Check required sections
    sections = receipt_data.get("sections", [])
    for required in format_rules["required_sections"]:
        if not any(s["name"] == required for s in sections):
            violations.append(f"Missing required section: {required}")

    if not violations:
        return True, [], 1.0

    confidence = 1.0 - (
        len(violations)
        / (len(receipt_data.get("lines", [])) + len(format_rules["required_sections"]))
    )
    return False, violations, confidence
