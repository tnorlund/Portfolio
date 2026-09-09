"""Exact JSON and key primitives shared by nutrition storage records."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from receipt_dynamo.data.shared_exceptions import EntityValidationError


def nutrition_json(value: Any) -> str:
    """Canonical decimal strings; never round-trip facts through floats."""

    def convert(item: Any) -> Any:
        if isinstance(item, Decimal):
            if not item.is_finite():
                raise EntityValidationError("nonfinite nutrition number")
            if item == 0:
                return "0"
            result = format(item, "f")
            return result.rstrip("0").rstrip(".") if "." in result else result
        if isinstance(item, float):
            raise EntityValidationError("nutrition numbers must not be floats")
        if isinstance(item, (datetime, date)):
            return item.isoformat()
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise EntityValidationError("JSON keys must be strings")
            return {key: convert(val) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [convert(val) for val in item]
        if item is None or isinstance(item, (str, int, bool)):
            return item
        raise EntityValidationError("unsupported nutrition JSON value")

    result = json.dumps(convert(value), sort_keys=True, separators=(",", ":"))
    if len(result.encode("utf-8")) > 300_000:
        raise EntityValidationError("nutrition payload exceeds 300 KB")
    return result


def nutrition_hash(value: Any) -> str:
    return hashlib.sha256(nutrition_json(value).encode("utf-8")).hexdigest()


def read_nutrition_json(value: str) -> dict[str, Any]:
    """Facts encode decimals as strings, not JSON binary-float numbers."""
    try:
        payload = json.loads(value)
    except (TypeError, ValueError) as error:
        raise EntityValidationError("invalid nutrition JSON") from error
    if not isinstance(payload, dict):
        raise EntityValidationError("nutrition payload must be an object")
    nutrition_json(payload)
    return payload


def nutrition_key(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.encode("utf-8")) > 250
    ):
        raise EntityValidationError("invalid nutrition key component")
    return quote(value, safe="")


def check_nutrition_hash(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise EntityValidationError("invalid nutrition content hash")


def check_revision(value: int, *, minimum: int = 1) -> None:
    if type(value) is not int or not minimum <= value <= 2**53 - 1:
        raise EntityValidationError("invalid nutrition revision")


def nutrition_item(value: dict[str, Any]) -> dict[str, Any]:
    serializer = TypeSerializer()
    item = {key: serializer.serialize(val) for key, val in value.items()}
    if len(json.dumps(item).encode("utf-8")) > 380_000:
        raise EntityValidationError("nutrition item exceeds safe item size")
    return item


def nutrition_values(item: dict[str, Any]) -> dict[str, Any]:
    deserializer = TypeDeserializer()
    return {key: deserializer.deserialize(val) for key, val in item.items()}
