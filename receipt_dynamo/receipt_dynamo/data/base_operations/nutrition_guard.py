"""Nutrition rows never reach prohibited tables through any write path.

There is no override: no flag, no environment variable, no constructor
argument. Changing the denylist is a reviewed code change, not a switch.
"""

from __future__ import annotations

from typing import Any

from receipt_dynamo.data.shared_exceptions import EntityValidationError

PROHIBITED_NUTRITION_WRITE_TABLES = frozenset({"ReceiptsTable-d7ff76a"})
NUTRITION_TYPES = frozenset(
    {
        "FOOD_PRODUCT",
        "PRODUCT_ALIAS",
        "PRODUCT_ALIAS_OBSERVATION",
        "PRICE_OBSERVATION",
        "RECEIPT_NUTRITION_SUMMARY",
        "MERCHANT_LOOKUP_METHOD",
    }
)
# Keys under these partitions are nutrition rows even without a TYPE, so
# deletes are covered too. The receipt nutrition summary lives under the
# receipt's own IMAGE# partition and is only recognised by TYPE: a receipt
# cascade delete removes it by design (see delete_receipt_items).
NUTRITION_PARTITION_PREFIXES = (
    "FOOD_PRODUCT#",
    "PRODUCT_ALIAS#",
    "ALIAS_OBS#",
    "PRICE_OBS#",
    "MEAL#",
    "MERCHANT_LOOKUP_METHOD#",
)


def nutrition_table_is_prohibited(table: Any) -> bool:
    """DynamoDB accepts table ARNs as ``TableName``; match any spelling."""
    return not isinstance(table, str) or any(
        name in table for name in PROHIBITED_NUTRITION_WRITE_TABLES
    )


def _plain(value: Any) -> Any:
    return value.get("S") if isinstance(value, dict) else value


def is_nutrition_row(row: dict[str, Any]) -> bool:
    """``row`` is an item, a key, or anything carrying PK/TYPE attributes."""
    record_type = _plain(row.get("TYPE"))
    partition = _plain(row.get("PK"))
    return record_type in NUTRITION_TYPES or (
        isinstance(partition, str)
        and partition.startswith(NUTRITION_PARTITION_PREFIXES)
    )


def payload_touches_nutrition(payload: Any) -> bool:
    """Walk items, keys, entities, transact and batch request shapes."""
    if isinstance(payload, dict):
        if "PK" in payload or "TYPE" in payload:
            return is_nutrition_row(payload)
        return any(payload_touches_nutrition(v) for v in payload.values())
    if isinstance(payload, (list, tuple, set, frozenset)):
        return any(payload_touches_nutrition(v) for v in payload)
    key = getattr(payload, "key", None)
    return isinstance(key, dict) and is_nutrition_row(key)


def refuse_prohibited_nutrition_write(table: Any, payload: Any) -> None:
    """Raise before I/O when a nutrition row would reach a prohibited table."""
    if nutrition_table_is_prohibited(table) and payload_touches_nutrition(
        payload
    ):
        raise EntityValidationError(
            "nutrition writes to this table are prohibited"
        )
