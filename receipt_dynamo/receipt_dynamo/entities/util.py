import re
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any, Dict, Type, Union


def _repr_str(value: Any) -> str:
    """
    Return a string wrapped in single quotes, or the literal 'None' if value
    is None.
    """
    return "None" if value is None else f"'{value}'"


def format_type_error(name: str, value: Any, expected: type | tuple[type, ...]) -> str:
    """Return a standardized type error message."""
    if isinstance(expected, tuple):
        expected_names = ", ".join(t.__name__ for t in expected)
    else:
        expected_names = expected.__name__
    return f"{name} must be {expected_names}, got {type(value).__name__}"


def assert_type(
    name: str,
    value: Any,
    expected: type | tuple[type, ...],
    exc_type: type[Exception] = TypeError,
) -> None:
    """Raise an exception if ``value`` is not an instance of ``expected``."""
    if not isinstance(value, expected):
        raise exc_type(format_type_error(name, value, expected))


# Regex for UUID version 4 (case-insensitive, enforcing the '4' and the
# [89AB] variant).
UUID_V4_REGEX = re.compile(
    r"^[0-9A-Fa-f]{8}-"
    r"[0-9A-Fa-f]{4}-"
    r"4[0-9A-Fa-f]{3}-"
    r"[89ABab][0-9A-Fa-f]{3}-"
    r"[0-9A-Fa-f]{12}$"
)


def assert_valid_bounding_box(
    bounding_box: Dict[str, Union[int, float]],
) -> Dict[str, Union[int, float]]:
    """
    Assert that the bounding box is valid.
    """
    if not isinstance(bounding_box, dict):
        raise ValueError("bounding_box must be a dictionary")
    for key in ["x", "y", "width", "height"]:
        if key not in bounding_box:
            raise ValueError(f"bounding_box must contain the key '{key}'")
        if not isinstance(bounding_box[key], (int, float)):
            raise ValueError(f"bounding_box['{key}'] must be a number")
    return bounding_box


def assert_valid_point(
    point: Dict[str, Union[int, float]],
) -> Dict[str, Union[int, float]]:
    """
    Assert that the point is valid.
    """
    if not isinstance(point, dict):
        raise ValueError("point must be a dictionary")
    for key in ["x", "y"]:
        if key not in point:
            raise ValueError(f"point must contain the key '{key}'")
        if not isinstance(point[key], (int, float)):
            raise ValueError(f"point['{key}'] must be a number")
    return point


def _format_float(
    value: float,
    decimal_places: int = 10,
    total_length: int = 20,  # pylint: disable=unused-argument
) -> str:
    # Convert float → string → Decimal to avoid float binary representation
    # issues
    d_value = Decimal(str(value))

    # Create a "quantizer" for the desired number of decimal digits
    # e.g. decimal_places=10 → quantizer = Decimal('1.0000000000')
    quantizer = Decimal("1." + "0" * decimal_places)

    # Round using the chosen rounding mode (e.g. HALF_UP)
    d_rounded = d_value.quantize(quantizer, rounding=ROUND_HALF_UP)

    # Format as a string with exactly `decimal_places` decimals
    formatted = f"{d_rounded:.{decimal_places}f}"

    # If instead you wanted trailing zeros, you could do:
    # formatted = formatted.ljust(total_length, '0')

    return formatted


def assert_valid_uuid(uuid: str) -> None:
    """
    Assert that the UUID is valid.
    """
    if not isinstance(uuid, str):
        raise ValueError("uuid must be a string")
    if not UUID_V4_REGEX.match(uuid):
        raise ValueError("uuid must be a valid UUIDv4")


def normalize_enum(candidate: Any, enum_cls: Type[Enum]) -> str:
    """Return the normalized ``enum_cls`` value for ``candidate``.

    Args:
        candidate: A string or Enum instance to normalize.
        enum_cls: The Enum class to normalize against.

    Returns:
        str: The ``.value`` of the matching Enum member.

    Raises:
        ValueError: If ``candidate`` is not valid for ``enum_cls``.
    """
    if isinstance(candidate, enum_cls):
        return str(candidate.value)
    if isinstance(candidate, str):
        try:
            return str(enum_cls(candidate).value)
        except ValueError as exc:
            options = ", ".join(e.value for e in enum_cls)
            raise ValueError(
                f"{enum_cls.__name__} must be one of: {options}\n" f"Got: {candidate}"
            ) from exc
    raise ValueError(
        f"{enum_cls.__name__} must be a str or {enum_cls.__name__} instance"
    )


def shear_point(  # pylint: disable=too-many-arguments
    px: float,
    py: float,
    pivot_x: float,
    pivot_y: float,
    shx: float,
    shy: float,
) -> tuple[float, float]:
    """
    Shears point (px, py) around pivot (pivot_x, pivot_y)
    by shear factors `shx` (x-shear) and `shy` (y-shear).

    Forward transform (source -> dest):
        [x'] = [1    shx] [x - pivot_x]
        [y']   [shy    1] [y - pivot_y]

    Then translate back by adding pivot_x, pivot_y.
    """
    # 1) Translate so pivot is at origin
    translated_x = px - pivot_x
    translated_y = py - pivot_y

    # 2) Apply shear
    sheared_x = translated_x + shx * translated_y
    sheared_y = shy * translated_x + translated_y

    # 3) Translate back
    final_x = sheared_x + pivot_x
    final_y = sheared_y + pivot_y
    return final_x, final_y


def normalize_address(addr: str) -> str:
    """Normalize an address for consistent caching.

    Args:
        addr (str): The address to normalize

    Returns:
        str: The normalized address
    """
    # Convert to lowercase
    addr = addr.lower()
    # Replace common abbreviations with word boundaries
    addr = re.sub(r"\bblvd\.?\b", "boulevard", addr)
    addr = re.sub(r"\bst\.?\b", "street", addr)
    addr = re.sub(r"\bave\.?\b", "avenue", addr)
    addr = re.sub(r"\brd\.?\b", "road", addr)
    # Remove punctuation except for numbers and letters
    addr = re.sub(r"[^\w\s]", "", addr)
    # Normalize whitespace
    addr = " ".join(addr.split())
    return addr


# ============================================================================
# DynamoDB Serialization Utilities
# ============================================================================
# These utilities eliminate code duplication in entity to_item() methods


def serialize_bounding_box(bounding_box: Dict[str, float]) -> Dict[str, Any]:
    """
    Serialize a bounding box dictionary to DynamoDB format.

    Eliminates duplicate code across receipt_line, word, receipt_word,
    receipt_letter entities.

    Args:
        bounding_box: Dict with x, y, width, height float values

    Returns:
        DynamoDB-formatted bounding_box with proper number formatting
    """
    return {
        "M": {
            "x": {"N": _format_float(bounding_box["x"], 20, 22)},
            "y": {"N": _format_float(bounding_box["y"], 20, 22)},
            "width": {"N": _format_float(bounding_box["width"], 20, 22)},
            "height": {"N": _format_float(bounding_box["height"], 20, 22)},
        }
    }


def serialize_coordinate_point(point: Dict[str, float]) -> Dict[str, Any]:
    """
    Serialize a coordinate point (x, y) to DynamoDB format.

    Used for top_right, top_left, bottom_right, bottom_left fields
    in receipt_word, receipt_letter entities.

    Args:
        point: Dict with x, y float values

    Returns:
        DynamoDB-formatted coordinate point
    """
    return {
        "M": {
            "x": {"N": _format_float(point["x"], 20, 22)},
            "y": {"N": _format_float(point["y"], 20, 22)},
        }
    }


def serialize_confidence(confidence: float) -> Dict[str, str]:
    """
    Serialize a confidence value to DynamoDB format.

    Standardizes confidence field formatting across entities.

    Args:
        confidence: Float confidence value (0.0 to 1.0)

    Returns:
        DynamoDB-formatted confidence field
    """
    return {"N": _format_float(confidence, 2, 2)}


def build_base_item(entity, entity_type: str, gsi_keys=None) -> Dict[str, Any]:
    """
    Build the base structure for entity to_item() methods.

    Eliminates duplicate code across entity to_item() methods by providing
    a standard way to merge primary key, GSI keys, and TYPE field.

    Args:
        entity: Entity instance (must have .key property)
        entity_type: TYPE field value (e.g., "RECEIPT_WORD", "LETTER")
        gsi_keys: Optional list of GSI key method names to include
                  (e.g., ["gsi1_key", "gsi2_key"])

    Returns:
        Dict with merged keys and TYPE field ready for additional fields

    Example:
        # Simple entity with just primary key
        base = build_base_item(self, "LETTER")

        # Entity with GSI keys
        base = build_base_item(self, "RECEIPT_WORD",
                              ["gsi1_key", "gsi2_key", "gsi3_key"])
    """
    # Start with primary key
    item = {**entity.key}

    # Add GSI keys if specified
    if gsi_keys:
        for gsi_key_method in gsi_keys:
            if hasattr(entity, gsi_key_method):
                gsi_method = getattr(entity, gsi_key_method)
                # Handle both property and method patterns
                if callable(gsi_method):
                    item.update(gsi_method())
                else:
                    item.update(gsi_method)

    # Add TYPE field
    item["TYPE"] = {"S": entity_type}

    return item


# ============================================================================
# DynamoDB Deserialization Utilities
# ============================================================================
# These utilities eliminate code duplication in item_to_* conversion functions


def deserialize_bounding_box(item_field: Dict[str, Any]) -> Dict[str, float]:
    """
    Deserialize a DynamoDB bounding box field back to a Python dict.

    Eliminates duplicate code across item_to_* conversion functions
    for entities with bounding box data.

    Args:
        item_field: DynamoDB field in format {"M": {"x": {"N": "0.1"}, ...}}

    Returns:
        Python dict with float values: {"x": 0.1, "y": 0.2, ...}

    Example:
        # Instead of:
        bounding_box = {
            key: float(value["N"])
            for key, value in item["bounding_box"]["M"].items()
        }

        # Use:
        bounding_box = deserialize_bounding_box(item["bounding_box"])
    """
    return {key: float(value["N"]) for key, value in item_field["M"].items()}


def deserialize_coordinate_point(
    item_field: Dict[str, Any],
) -> Dict[str, float]:
    """
    Deserialize a DynamoDB coordinate point field back to a Python dict.

    Used for top_right, top_left, bottom_right, bottom_left fields
    in geometric entities.

    Args:
        item_field: DynamoDB field in format
            {"M": {"x": {"N": "0.5"}, "y": {"N": "0.7"}}}

    Returns:
        Python dict with float values: {"x": 0.5, "y": 0.7}
    """
    return {key: float(value["N"]) for key, value in item_field["M"].items()}


def deserialize_confidence(item_field: Dict[str, Any]) -> float:
    """
    Deserialize a DynamoDB confidence field back to a Python float.

    Standardizes confidence field deserialization across entities.

    Args:
        item_field: DynamoDB field in format {"N": "0.95"}

    Returns:
        Python float: 0.95
    """
    return float(item_field["N"])


# Validation utilities to eliminate duplicate __post_init__ code
def validate_positive_int(field_name: str, value: Any) -> None:
    """
    Validate that a field is a positive integer.

    Eliminates duplicate validation code across entities that require
    positive integer fields like receipt_id, line_id, word_id, etc.

    Args:
        field_name: Name of the field being validated (for error messages)
        value: Value to validate

    Raises:
        ValueError: If value is not an integer or not positive
    """
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")


def validate_non_negative_int(field_name: str, value: Any) -> None:
    """
    Validate that a field is a non-negative integer (>= 0).

    Eliminates duplicate validation code across entities that allow
    zero values like line_id, word_id in some contexts.

    Args:
        field_name: Name of the field being validated (for error messages)
        value: Value to validate

    Raises:
        ValueError: If value is not an integer or is negative
    """
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def validate_positive_dimensions(width: Any, height: Any) -> None:
    """
    Validate that width and height are positive integers.

    Eliminates duplicate validation code across Image, Receipt, and
    other entities that require positive dimensions.

    Args:
        width: Width value to validate
        height: Height value to validate

    Raises:
        ValueError: If values are not integers or not positive
    """
    if (
        not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
    ):
        raise ValueError("width and height must be positive integers")


def validate_confidence_range(field_name: str, value: Any) -> float:
    """
    Validate and normalize confidence values to float in range (0, 1].

    Eliminates duplicate validation code across entities with confidence
    fields. Handles int->float conversion and range validation.

    Args:
        field_name: Name of the field being validated (for error messages)
        value: Confidence value to validate and normalize

    Returns:
        Normalized float confidence value

    Raises:
        ValueError: If value is not numeric or not in valid range
    """
    # Convert int to float if needed
    if isinstance(value, int):
        value = float(value)

    if not isinstance(value, float):
        raise ValueError(f"{field_name} must be a float")

    if value <= 0.0 or value > 1.0:
        raise ValueError(f"{field_name} must be between 0 and 1")

    return value
