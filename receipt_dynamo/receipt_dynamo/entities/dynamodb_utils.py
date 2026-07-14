"""
Common DynamoDB utilities to eliminate duplicate code across entities.

This module provides shared functions for:
- Parsing DynamoDB maps and values
- Converting Python objects to DynamoDB format
- Common validation patterns
"""

from decimal import Decimal
from math import isfinite
from typing import Any, Dict


def _parse_number(value: str) -> int | float:
    """Parse a DynamoDB number without turning integer strings into floats."""
    try:
        return int(value)
    except ValueError:
        return float(value)


def freeze_for_hash(value: Any) -> Any:
    """Create an immutable representation consistent with nested equality."""
    if isinstance(value, dict):
        return tuple(
            sorted(
                ((key, freeze_for_hash(item)) for key, item in value.items()),
                key=lambda pair: repr(pair[0]),
            )
        )
    if isinstance(value, list):
        return tuple(freeze_for_hash(item) for item in value)
    if isinstance(value, set):
        return frozenset(freeze_for_hash(item) for item in value)
    return value


def parse_dynamodb_map(dynamodb_map: Dict) -> dict[str, Any]:
    """
    Parse a DynamoDB map into a Python dictionary.

    Args:
        dynamodb_map: A DynamoDB map in the format {"key": {"S": "value"}, ...}

    Returns:
        A Python dictionary with parsed values
    """
    return {k: parse_dynamodb_value(v) for k, v in dynamodb_map.items()}


def parse_dynamodb_value(value: Dict) -> Any:
    """
    Parse a single DynamoDB value.

    Args:
        value: A DynamoDB value in the format {"S": "value"} or {"N": "123"}

    Returns:
        The parsed Python value
    """
    # pylint: disable=too-many-return-statements
    # DynamoDB has 10 distinct type markers (S, N, M, L, BOOL, NULL, B, SS,
    # NS, BS). Each requires different handling, making multiple returns
    # inherent to this logic.
    if "M" in value:
        return parse_dynamodb_map(value["M"])
    if "L" in value:
        return [parse_dynamodb_value(item) for item in value["L"]]
    if "S" in value:
        return value["S"]
    if "N" in value:
        return _parse_number(value["N"])
    if "BOOL" in value:
        return value["BOOL"]
    if "NULL" in value:
        return None
    if "B" in value:
        return value["B"]
    if "SS" in value:
        return set(value["SS"])
    if "NS" in value:
        return {_parse_number(n) for n in value["NS"]}
    if "BS" in value:
        return set(value["BS"])
    return None
    # pylint: enable=too-many-return-statements


def dict_to_dynamodb_map(d: Dict) -> Dict:
    """
    Convert a Python dictionary to DynamoDB map format.

    Args:
        d: A Python dictionary

    Returns:
        A DynamoDB map in the format {"key": {"S": "value"}, ...}
    """
    result: dict[str, Any] = {}
    for k, v in d.items():
        result[k] = to_dynamodb_value(v)
    return result


def to_dynamodb_value(  # pylint: disable=too-many-branches
    value: Any,
) -> dict[str, Any]:
    """
    Convert a Python value to DynamoDB format.

    Args:
        value: Any Python value

    Returns:
        A DynamoDB value in the format {"S": "value"} or {"N": "123"}
    """
    # pylint: disable=too-many-return-statements
    # Maps Python types to DynamoDB type markers. Each Python type (dict, list,
    # str, bool, int, float, None, bytes, set) requires a different DynamoDB
    # representation, making multiple returns inherent to this type dispatch.
    if isinstance(value, dict):
        return {"M": dict_to_dynamodb_map(value)}
    if isinstance(value, list):
        return {"L": [to_dynamodb_value(item) for item in value]}
    if isinstance(value, str):
        return {"S": value}
    # Check bool before int since bool is a subclass of int in Python
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, int):
        return {"N": str(value)}
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("DynamoDB numbers must be finite")
        return {"N": str(value)}
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("DynamoDB numbers must be finite")
        return {"N": str(value)}
    if value is None:
        return {"NULL": True}
    if isinstance(value, bytes):
        return {"B": value}
    if isinstance(value, set):
        if not value:
            raise ValueError("DynamoDB sets cannot be empty")
        if any(isinstance(item, bool) for item in value):
            raise TypeError("DynamoDB sets cannot contain bool values")
        if all(isinstance(item, str) for item in value):
            if any(not item for item in value):
                raise ValueError("DynamoDB sets cannot contain empty values")
            return {"SS": sorted(value)}
        if all(isinstance(item, (int, float, Decimal)) for item in value):
            if any(
                (isinstance(item, float) and not isfinite(item))
                or (isinstance(item, Decimal) and not item.is_finite())
                for item in value
            ):
                raise ValueError("DynamoDB numbers must be finite")
            return {"NS": [str(item) for item in sorted(value)]}
        if all(isinstance(item, bytes) for item in value):
            if any(not item for item in value):
                raise ValueError("DynamoDB sets cannot contain empty values")
            return {"BS": sorted(value)}
        raise TypeError(
            "DynamoDB sets must contain only strings, numbers, or bytes"
        )

    # Fallback to string representation
    return {"S": str(value)}
    # pylint: enable=too-many-return-statements


def validate_required_keys(
    item: dict[str, Any], required_keys: set, entity_name: str = "item"
) -> None:
    """
    Validate that an item contains all required keys.

    Args:
        item: The item to validate
        required_keys: Set of required key names
        entity_name: Name of the entity for error messages

    Raises:
        ValueError: If required keys are missing
    """
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            f"Invalid {entity_name} format\n"
            f"missing keys: {missing_keys}\n"
            f"additional keys: {additional_keys}"
        )
