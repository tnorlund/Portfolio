# receipt_dynamo/receipt_dynamo/entities/receipt_validation_category.py
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from receipt_dynamo.entities.dynamodb_utils import parse_dynamodb_value
from receipt_dynamo.entities.entity_mixins import SerializationMixin
from receipt_dynamo.entities.util import (
    assert_valid_uuid,
    validate_iso_timestamp,
    validate_metadata_field,
    validate_non_empty_string,
    validate_positive_int,
)


@dataclass(eq=True, unsafe_hash=False)
class ReceiptValidationCategory(SerializationMixin):
    """
    DynamoDB entity representing a specific validation category for a receipt.
    Each category contains its own status, reasoning, and summary of results.
    """

    receipt_id: int
    image_id: str
    field_name: str
    field_category: str
    status: str
    reasoning: str
    result_summary: dict[str, int]
    validation_timestamp: str | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self):
        validate_positive_int("receipt_id", self.receipt_id)
        assert_valid_uuid(self.image_id)
        validate_non_empty_string("field_name", self.field_name)
        validate_non_empty_string("field_category", self.field_category)
        validate_non_empty_string("status", self.status)
        validate_non_empty_string("reasoning", self.reasoning)

        if not isinstance(self.result_summary, dict):
            raise ValueError("result_summary must be a dictionary")

        self.validation_timestamp = validate_iso_timestamp(
            self.validation_timestamp, "validation_timestamp"
        )
        self.metadata = validate_metadata_field(self.metadata)

    @property
    def key(self) -> dict[str, dict[str, str]]:
        """Return the DynamoDB key for this item."""
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}#ANALYSIS#VALIDATION#"
                    f"CATEGORY#{self.field_name}"
                )
            },
        }

    @property
    def gsi1_key(self) -> dict[str, dict[str, str]]:
        """Return the GSI1 key for this item."""
        return {
            "GSI1PK": {"S": f"VALIDATION_STATUS#{self.status}"},
            "GSI1SK": {
                "S": (
                    f"VALIDATION#{self.validation_timestamp}#"
                    f"CATEGORY#{self.field_name}"
                )
            },
        }

    @property
    def gsi3_key(self) -> dict[str, dict[str, str]]:
        """Return the GSI3 key for this item."""
        return {
            "GSI3PK": {"S": f"FIELD_STATUS#{self.field_name}#{self.status}"},
            "GSI3SK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}"
            },
        }

    def to_item(self) -> dict[str, Any]:
        """Convert to a DynamoDB item."""
        # Start with the keys which are already properly formatted
        item = {
            **self.key,
            **self.gsi1_key,
            **self.gsi3_key,
            "TYPE": {"S": "RECEIPT_VALIDATION_CATEGORY"},
        }

        # Add the required fields with proper DynamoDB typing
        item["field_name"] = {"S": self.field_name}
        item["field_category"] = {"S": self.field_category}
        item["status"] = {"S": self.status}
        item["reasoning"] = {"S": self.reasoning}
        item["result_summary"] = self._python_to_dynamo(self.result_summary)
        item["validation_timestamp"] = {"S": self.validation_timestamp}
        item["metadata"] = self._python_to_dynamo(self.metadata)

        return item

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptValidationCategory":
        """Create a ReceiptValidationCategory from a DynamoDB item."""
        # Extract image_id, receipt_id, and field_name from keys
        image_id = item["PK"]["S"].split("#")[1]
        sk_parts = item["SK"]["S"].split("#")
        receipt_id = int(sk_parts[1])
        field_name = sk_parts[5]

        # Extract other fields with proper type conversion
        field_category = item.get("field_category", {}).get("S", "")
        status = item.get("status", {}).get("S", "")
        reasoning = item.get("reasoning", {}).get("S", "")
        validation_timestamp = item.get("validation_timestamp", {}).get("S")

        # Extract complex structures with recursive conversion
        result_summary = SerializationMixin._dynamo_to_python(
            item.get("result_summary", {"M": {}})
        )
        metadata = SerializationMixin._dynamo_to_python(
            item.get("metadata", {"M": {}})
        )

        # Create the ReceiptValidationCategory
        return cls(
            receipt_id=receipt_id,
            image_id=image_id,
            field_name=field_name,
            field_category=field_category,
            status=status,
            reasoning=reasoning,
            result_summary=result_summary,
            validation_timestamp=validation_timestamp,
            metadata=metadata,
        )

    def __repr__(self) -> str:
        return (
            f"ReceiptValidationCategory(receipt_id={self.receipt_id}, "
            f"image_id={self.image_id}, "
            f"field_name={self.field_name}, "
            f"status={self.status})"
        )


def _find_sk_value_after(sk_parts: list[str], key: str) -> str | None:
    """Find the value after a key in SK parts."""
    for i, part in enumerate(sk_parts):
        if part == key and i + 1 < len(sk_parts):
            return sk_parts[i + 1]
    return None


def _extract_receipt_id(item: dict[str, Any], sk_parts: list[str]) -> int:
    """Extract receipt_id from SK or item attributes."""
    receipt_id_str = _find_sk_value_after(sk_parts, "RECEIPT")

    if receipt_id_str is not None:
        try:
            return int(receipt_id_str.lstrip("0") or "0")
        except ValueError:
            return int(receipt_id_str)

    if "receipt_id" in item:
        return (
            int(item["receipt_id"]["N"])
            if "N" in item["receipt_id"]
            else item["receipt_id"]
        )
    raise ValueError("Could not extract receipt_id from item")


def _extract_image_id(item: dict[str, Any]) -> str:
    """Extract image_id from PK or item attributes."""
    pk_parts = item["PK"]["S"].split("#")
    if len(pk_parts) > 1:
        return pk_parts[1]

    if "image_id" in item:
        return (
            item["image_id"]["S"]
            if "S" in item["image_id"]
            else item["image_id"]
        )
    raise ValueError("Could not extract image_id from item")


def _extract_field_category(item: dict[str, Any], sk_parts: list[str]) -> str:
    """Extract field_category from item or SK."""
    if "field_category" in item:
        return item["field_category"]["S"]

    # Try SK pattern: CATEGORY#<field_name>#<category>
    for i, part in enumerate(sk_parts):
        if part == "CATEGORY" and i + 2 < len(sk_parts):
            return sk_parts[i + 2]
    return "general"


def item_to_receipt_validation_category(
    item: dict[str, Any],
) -> ReceiptValidationCategory:
    """Convert a DynamoDB item to a ReceiptValidationCategory object.

    Args:
        item: DynamoDB item dictionary.

    Returns:
        ReceiptValidationCategory: The converted object.
    """
    sk_parts = item["SK"]["S"].split("#")

    receipt_id = _extract_receipt_id(item, sk_parts)
    image_id = _extract_image_id(item)

    # Extract field_name from SK or attribute
    field_name = _find_sk_value_after(sk_parts, "CATEGORY")
    if field_name is None:
        field_name = (
            item["field_name"]["S"]
            if "field_name" in item and "S" in item["field_name"]
            else "unknown"
        )

    field_category = _extract_field_category(item, sk_parts)

    # Extract simple string fields with defaults
    status = item.get("status", {}).get("S", "unknown")
    reasoning = item.get("reasoning", {}).get("S", "No reasoning provided")
    validation_timestamp = item.get("validation_timestamp", {}).get(
        "S", datetime.now().isoformat()
    )

    # Extract complex fields using public parse function
    result_summary: dict[str, Any] = (
        parse_dynamodb_value(item["result_summary"])
        if "result_summary" in item
        else {}
    )
    metadata: dict[str, Any] = (
        parse_dynamodb_value(item["metadata"]) if "metadata" in item else {}
    )

    return ReceiptValidationCategory(
        receipt_id=receipt_id,
        image_id=image_id,
        field_name=field_name,
        field_category=field_category,
        status=status,
        reasoning=reasoning,
        result_summary=result_summary,
        validation_timestamp=validation_timestamp,
        metadata=metadata,
    )
