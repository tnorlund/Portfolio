"""Bounded, atomic private receipt snapshots and their source observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.nutrition_support import (
    check_nutrition_hash,
    nutrition_hash,
    nutrition_item,
    nutrition_json,
    nutrition_values,
    read_nutrition_json,
)


def nutrition_receipt_key(image_id: str, receipt_id: int) -> dict[str, Any]:
    try:
        if str(UUID(image_id)) != image_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as error:
        raise EntityValidationError("invalid nutrition image ID") from error
    if type(receipt_id) is not int or not 1 <= receipt_id <= 99999:
        raise EntityValidationError("invalid nutrition receipt ID")
    return {
        "PK": {"S": f"IMAGE#{image_id}"},
        "SK": {"S": f"RECEIPT#{receipt_id:05d}"},
    }


def nutrition_summary_key(image_id: str, receipt_id: int) -> dict[str, Any]:
    key = nutrition_receipt_key(image_id, receipt_id)
    key["SK"]["S"] += "#NUTRITION_SUMMARY"
    return key


@dataclass(frozen=True)
class NutritionInput:
    """Canonical raw source values; callers compute from these exact inputs."""

    image_id: str
    receipt_id: int
    source_json: str

    def __post_init__(self) -> None:
        nutrition_receipt_key(self.image_id, self.receipt_id)
        source = read_nutrition_json(self.source_json)
        parent = source.get("parent", {})
        key = nutrition_receipt_key(self.image_id, self.receipt_id)
        if (
            any(parent.get(name) != value for name, value in key.items())
            or not isinstance(parent.get("timestamp_added", {}).get("S"), str)
            or not isinstance(source.get("lines"), list)
        ):
            raise EntityValidationError("invalid nutrition source observation")
        object.__setattr__(self, "source_json", nutrition_json(source))

    @property
    def fingerprint(self) -> str:
        return nutrition_hash(read_nutrition_json(self.source_json))

    @property
    def parent(self) -> dict[str, Any]:
        return read_nutrition_json(self.source_json)["parent"]


@dataclass(frozen=True)
class ReceiptNutritionSnapshot:
    image_id: str
    receipt_id: int
    revision: str
    source_fingerprint: str
    context_hash: str
    payload_json: str
    stale: bool = True

    def to_item(self) -> dict[str, Any]:
        for digest in (
            self.revision,
            self.source_fingerprint,
            self.context_hash,
        ):
            check_nutrition_hash(digest)
        payload = read_nutrition_json(self.payload_json)
        if not isinstance(payload.get("rows"), list) or not isinstance(
            payload.get("summary"), dict
        ):
            raise EntityValidationError("snapshot needs rows and summary")
        return {
            **nutrition_summary_key(self.image_id, self.receipt_id),
            **nutrition_item(
                {
                    "TYPE": "RECEIPT_NUTRITION_SUMMARY",
                    "revision": self.revision,
                    "source_fingerprint": self.source_fingerprint,
                    "context_hash": self.context_hash,
                    "payload_json": nutrition_json(payload),
                }
            ),
        }


def item_to_receipt_nutrition_snapshot(
    image_id: str, receipt_id: int, item: dict[str, Any]
) -> ReceiptNutritionSnapshot:
    value = nutrition_values(item)
    try:
        record = ReceiptNutritionSnapshot(
            image_id,
            receipt_id,
            value["revision"],
            value["source_fingerprint"],
            value["context_hash"],
            value["payload_json"],
        )
        if record.to_item() != item:
            raise EntityValidationError("invalid nutrition snapshot item")
        return record
    except (KeyError, TypeError) as error:
        raise EntityValidationError(
            "invalid nutrition snapshot item"
        ) from error
