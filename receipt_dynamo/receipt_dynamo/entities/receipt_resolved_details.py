"""Persisted structured output from deterministic receipt resolution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any, ClassVar

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities.util import assert_valid_uuid


@dataclass(eq=True)
class ReceiptResolvedDetails:
    """Receipt-scoped D4 artifact with field-level provenance.

    The receipt document is stored as JSON so the resolver can evolve its
    merchant, transaction, item, total, and tender fields without destructive
    migrations. Resolved leaf fields use the public ``value``, ``provenance``,
    and ``confidence`` contract.
    """

    TYPE: ClassVar[str] = "RECEIPT_RESOLVED_DETAILS"
    REQUIRED_KEYS: ClassVar[set[str]] = {
        "PK",
        "SK",
        "TYPE",
        "details_json",
        "validation_status",
        "model_source",
        "created_at",
        "schema_version",
    }

    image_id: str
    receipt_id: int
    merchant: dict[str, Any] | None
    transaction: dict[str, Any]
    items: list[dict[str, Any]]
    totals: dict[str, Any]
    tender: dict[str, Any]
    conflicts: list[dict[str, Any]]
    validation_status: str
    model_source: str
    created_at: datetime | str
    schema_version: int = 1

    def __post_init__(self) -> None:
        """Normalize metadata and validate the JSON-backed document."""

        assert_valid_uuid(self.image_id)
        if (
            isinstance(self.receipt_id, bool)
            or not isinstance(self.receipt_id, int)
            or self.receipt_id <= 0
        ):
            raise ValueError("receipt_id must be a positive integer")
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version <= 0
        ):
            raise ValueError("schema_version must be a positive integer")
        if self.merchant is not None and not isinstance(self.merchant, dict):
            raise ValueError("merchant must be a dictionary or None")
        for name in ("transaction", "totals", "tender"):
            if not isinstance(getattr(self, name), dict):
                raise ValueError(f"{name} must be a dictionary")
        for name in ("items", "conflicts"):
            value = getattr(self, name)
            if not isinstance(value, list) or any(
                not isinstance(entry, dict) for entry in value
            ):
                raise ValueError(f"{name} must be a list of dictionaries")
        try:
            json.dumps(self._details_document(), sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "resolved details must be JSON serializable"
            ) from exc

        status = str(self.validation_status).upper()
        allowed_statuses = {
            ValidationStatus.VALID.value,
            ValidationStatus.PENDING.value,
            ValidationStatus.NEEDS_REVIEW.value,
        }
        if status not in allowed_statuses:
            raise ValueError(
                "validation_status must be VALID, PENDING, or NEEDS_REVIEW"
            )
        self.validation_status = status
        if not isinstance(self.model_source, str) or not self.model_source:
            raise ValueError("model_source must be a non-empty string")
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
        elif not isinstance(self.created_at, datetime):
            raise ValueError("created_at must be a datetime or ISO string")

        self._validate_resolved_fields(self._details_document())

    @classmethod
    def _validate_resolved_fields(cls, value: Any) -> None:
        """Validate confidence values on resolved-field dictionaries."""

        if isinstance(value, list):
            for entry in value:
                cls._validate_resolved_fields(entry)
            return
        if not isinstance(value, dict):
            return
        if "value" in value:
            missing = {"provenance", "confidence"} - set(value)
            if missing:
                raise ValueError(
                    "resolved fields with value must include provenance and "
                    "confidence"
                )
            provenance = value["provenance"]
            if not isinstance(provenance, str) or not provenance:
                raise ValueError("field provenance must be a non-empty string")
            confidence = value["confidence"]
            if (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not isfinite(float(confidence))
                or not 0.0 <= float(confidence) <= 1.0
            ):
                raise ValueError("field confidence must be in [0, 1]")
        for nested in value.values():
            cls._validate_resolved_fields(nested)

    def _details_document(self) -> dict[str, Any]:
        """Return only the evolvable, JSON-backed details payload."""

        return {
            "merchant": self.merchant,
            "transaction": self.transaction,
            "items": self.items,
            "totals": self.totals,
            "tender": self.tender,
            "conflicts": self.conflicts,
        }

    @property
    def field_count(self) -> int:
        """Count populated resolved fields across the structured output."""

        def count(value: Any) -> int:
            if isinstance(value, list):
                return sum(count(entry) for entry in value)
            if not isinstance(value, dict):
                return 0
            if "value" in value:
                return int(value["value"] is not None)
            return sum(count(entry) for entry in value.values())

        return sum(
            count(value)
            for value in (
                self.merchant,
                self.transaction,
                self.items,
                self.totals,
                self.tender,
            )
        )

    def to_document(self) -> dict[str, Any]:
        """Return the complete API document with persistence metadata."""

        self.__post_init__()
        assert isinstance(self.created_at, datetime)
        return {
            "image_id": self.image_id,
            "receipt_id": self.receipt_id,
            "schema_version": self.schema_version,
            **self._details_document(),
            "validation_status": self.validation_status,
            "model_source": self.model_source,
            "created_at": self.created_at.isoformat(),
        }

    @property
    def key(self) -> dict[str, Any]:
        """Return the receipt-scoped primary key."""

        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"RECEIPT#{self.receipt_id:05d}#RESOLVED_DETAILS"},
        }

    def gsi4_key(self) -> dict[str, Any]:
        """Return the single-query receipt-details index key."""

        return {
            "GSI4PK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}"
            },
            "GSI4SK": {"S": "7_RESOLVED_DETAILS"},
        }

    def to_item(self) -> dict[str, Any]:
        """Serialize the artifact to low-level DynamoDB JSON."""

        document = self.to_document()
        assert isinstance(self.created_at, datetime)
        return {
            **self.key,
            **self.gsi4_key(),
            "TYPE": {"S": self.TYPE},
            "details_json": {
                "S": json.dumps(self._details_document(), sort_keys=True)
            },
            "validation_status": {"S": self.validation_status},
            "model_source": {"S": self.model_source},
            "created_at": {"S": self.created_at.isoformat()},
            "schema_version": {"N": str(document["schema_version"])},
        }

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptResolvedDetails":
        """Deserialize a low-level DynamoDB item."""

        missing = cls.REQUIRED_KEYS - set(item)
        if missing:
            raise ValueError(f"Item is missing required keys: {missing}")
        if item["TYPE"].get("S") != cls.TYPE:
            raise ValueError(f"TYPE must be {cls.TYPE}")
        try:
            pk = item["PK"]["S"]
            if not pk.startswith("IMAGE#"):
                raise ValueError("invalid resolved-details partition key")
            image_id = pk.removeprefix("IMAGE#")
            sk_parts = item["SK"]["S"].split("#")
            if (
                len(sk_parts) != 3
                or sk_parts[0] != "RECEIPT"
                or sk_parts[2] != "RESOLVED_DETAILS"
            ):
                raise ValueError("invalid resolved-details sort key")
            details = json.loads(item["details_json"]["S"])
            if not isinstance(details, dict):
                raise ValueError("details_json must decode to a dictionary")
            return cls(
                image_id=image_id,
                receipt_id=int(sk_parts[1]),
                merchant=details["merchant"],
                transaction=details["transaction"],
                items=details["items"],
                totals=details["totals"],
                tender=details["tender"],
                conflicts=details["conflicts"],
                validation_status=item["validation_status"]["S"],
                model_source=item["model_source"]["S"],
                created_at=item["created_at"]["S"],
                schema_version=int(item["schema_version"]["N"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid ReceiptResolvedDetails: {exc}") from exc


def item_to_receipt_resolved_details(
    item: dict[str, Any],
) -> ReceiptResolvedDetails:
    """Convert a DynamoDB item to its resolved-details entity."""

    return ReceiptResolvedDetails.from_item(item)


__all__ = ["ReceiptResolvedDetails", "item_to_receipt_resolved_details"]
