"""Receipt-level provenance for deterministic label reconciliation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities.util import assert_valid_uuid


@dataclass(eq=True)
class ReceiptLabelReconciliation:
    """Additive D3 artifact containing corrections, checks, and conflicts."""

    image_id: str
    receipt_id: int
    corrections: list[dict[str, Any]]
    checks: list[dict[str, Any]]
    validation_status: str
    model_source: str
    created_at: datetime | str

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "corrections_json",
        "checks_json",
        "validation_status",
        "model_source",
        "created_at",
    }

    def __post_init__(self) -> None:
        """Normalize and validate the reconciliation artifact."""

        assert_valid_uuid(self.image_id)
        if (
            isinstance(self.receipt_id, bool)
            or not isinstance(self.receipt_id, int)
            or self.receipt_id <= 0
        ):
            raise ValueError("receipt_id must be a positive integer")
        for name in ("corrections", "checks"):
            value = getattr(self, name)
            if not isinstance(value, list) or any(
                not isinstance(entry, dict) for entry in value
            ):
                raise ValueError(f"{name} must be a list of dictionaries")
            try:
                json.dumps(value, sort_keys=True)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be JSON serializable") from exc
        status = str(self.validation_status).upper()
        if status not in {
            ValidationStatus.VALID.value,
            ValidationStatus.NEEDS_REVIEW.value,
        }:
            raise ValueError("validation_status must be VALID or NEEDS_REVIEW")
        self.validation_status = status
        if not isinstance(self.model_source, str) or not self.model_source:
            raise ValueError("model_source must be a non-empty string")
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
        elif not isinstance(self.created_at, datetime):
            raise ValueError("created_at must be a datetime or ISO string")

    @property
    def key(self) -> dict[str, Any]:
        """Return the receipt-scoped primary key."""

        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"RECEIPT#{self.receipt_id:05d}#LABEL_RECONCILIATION"},
        }

    def to_item(self) -> dict[str, Any]:
        """Serialize to low-level DynamoDB JSON."""

        self.__post_init__()
        assert isinstance(self.created_at, datetime)
        return {
            **self.key,
            "TYPE": {"S": "RECEIPT_LABEL_RECONCILIATION"},
            "corrections_json": {
                "S": json.dumps(self.corrections, sort_keys=True)
            },
            "checks_json": {"S": json.dumps(self.checks, sort_keys=True)},
            "validation_status": {"S": self.validation_status},
            "model_source": {"S": self.model_source},
            "created_at": {"S": self.created_at.isoformat()},
            "correction_count": {"N": str(len(self.corrections))},
            "conflict_count": {
                "N": str(
                    sum(
                        bool(correction.get("conflict"))
                        for correction in self.corrections
                    )
                )
            },
        }

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptLabelReconciliation":
        """Deserialize a low-level DynamoDB item."""

        missing = cls.REQUIRED_KEYS - set(item)
        if missing:
            raise ValueError(f"Item is missing required keys: {missing}")
        try:
            image_id = item["PK"]["S"].removeprefix("IMAGE#")
            sk = item["SK"]["S"].split("#")
            if (
                len(sk) != 3
                or sk[0] != "RECEIPT"
                or sk[2] != "LABEL_RECONCILIATION"
            ):
                raise ValueError("invalid label reconciliation sort key")
            return cls(
                image_id=image_id,
                receipt_id=int(sk[1]),
                corrections=json.loads(item["corrections_json"]["S"]),
                checks=json.loads(item["checks_json"]["S"]),
                validation_status=item["validation_status"]["S"],
                model_source=item["model_source"]["S"],
                created_at=item["created_at"]["S"],
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"Invalid ReceiptLabelReconciliation: {exc}"
            ) from exc


def item_to_receipt_label_reconciliation(
    item: dict[str, Any],
) -> ReceiptLabelReconciliation:
    """Convert a DynamoDB item to its reconciliation entity."""

    return ReceiptLabelReconciliation.from_item(item)


__all__ = [
    "ReceiptLabelReconciliation",
    "item_to_receipt_label_reconciliation",
]
