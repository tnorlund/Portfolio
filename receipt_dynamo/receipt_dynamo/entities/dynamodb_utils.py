"""
Common DynamoDB utilities to eliminate duplicate code across entities.

This module provides shared functions for:
- Parsing DynamoDB maps and values
- Converting Python objects to DynamoDB format
- Common validation patterns
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Union


def parse_dynamodb_map(dynamodb_map: Dict) -> Dict[str, Any]:
    """
    Parse a DynamoDB map into a Python dictionary.

    Args:
        dynamodb_map: A DynamoDB map in the format {"key": {"S": "value"}, ...}

    Returns:
        A Python dictionary with parsed values
    """
    result: Dict[str, Any] = {}
    for k, v in dynamodb_map.items():
        if "M" in v:
            result[k] = parse_dynamodb_map(v["M"])
        elif "L" in v:
            result[k] = [parse_dynamodb_value(item) for item in v["L"]]
        elif "S" in v:
            result[k] = v["S"]
        elif "N" in v:
            try:
                result[k] = int(v["N"])
            except ValueError:
                result[k] = float(v["N"])
        elif "BOOL" in v:
            result[k] = v["BOOL"]
        elif "NULL" in v:
            result[k] = None
        elif "B" in v:
            result[k] = v["B"]
        elif "SS" in v:
            result[k] = set(v["SS"])
        elif "NS" in v:
            result[k] = {int(n) if n.isdigit() else float(n) for n in v["NS"]}
        elif "BS" in v:
            result[k] = set(v["BS"])
    return result


def parse_dynamodb_value(value: Dict) -> Any:
    """
    Parse a single DynamoDB value.

    Args:
        value: A DynamoDB value in the format {"S": "value"} or {"N": "123"}

    Returns:
        The parsed Python value
    """
    if "M" in value:
        return parse_dynamodb_map(value["M"])
    if "L" in value:
        return [parse_dynamodb_value(item) for item in value["L"]]
    if "S" in value:
        return value["S"]
    if "N" in value:
        try:
            return int(value["N"])
        except ValueError:
            return float(value["N"])
    if "BOOL" in value:
        return value["BOOL"]
    if "NULL" in value:
        return None
    if "B" in value:
        return value["B"]
    if "SS" in value:
        return set(value["SS"])
    if "NS" in value:
        return {int(n) if n.isdigit() else float(n) for n in value["NS"]}
    if "BS" in value:
        return set(value["BS"])
    return None


def dict_to_dynamodb_map(d: Dict) -> Dict:
    """
    Convert a Python dictionary to DynamoDB map format.

    Args:
        d: A Python dictionary

    Returns:
        A DynamoDB map in the format {"key": {"S": "value"}, ...}
    """
    result: Dict[str, Any] = {}
    for k, v in d.items():
        result[k] = to_dynamodb_value(v)
    return result


def to_dynamodb_value(value: Any) -> Dict[str, Any]:
    """
    Convert a Python value to DynamoDB format.

    Args:
        value: Any Python value

    Returns:
        A DynamoDB value in the format {"S": "value"} or {"N": "123"}
    """
    if isinstance(value, dict):
        return {"M": dict_to_dynamodb_map(value)}
    if isinstance(value, list):
        return {"L": [to_dynamodb_value(item) for item in value]}
    if isinstance(value, str):
        return {"S": value}
    # Check bool before int since bool is a subclass of int in Python
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, (int, float)):
        return {"N": str(value)}
    if value is None:
        return {"NULL": True}
    if isinstance(value, bytes):
        return {"B": value}
    if isinstance(value, set):
        if all(isinstance(item, str) for item in value):
            return {"SS": list(value)}
        if all(isinstance(item, (int, float)) for item in value):
            return {"NS": [str(item) for item in value]}
        if all(isinstance(item, bytes) for item in value):
            return {"BS": list(value)}

    # Fallback to string representation
    return {"S": str(value)}


def validate_required_keys(
    item: Dict[str, Any], required_keys: set, entity_name: str = "item"
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
