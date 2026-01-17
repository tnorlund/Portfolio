# receipt_dynamo/receipt_dynamo/entities/receipt_validation_result.py
from dataclasses import dataclass
from typing import Any

from receipt_dynamo.entities.entity_mixins import SerializationMixin
from receipt_dynamo.entities.util import (
    assert_valid_uuid,
    validate_iso_timestamp,
    validate_metadata_field,
    validate_non_empty_string,
    validate_non_negative_int,
    validate_positive_int,
)


@dataclass(eq=True, unsafe_hash=False)
class ReceiptValidationResult(SerializationMixin):
    """
    DynamoDB entity representing an individual validation result.
    Each result represents a specific validation check with its type, message,
    and reasoning.
    """

    receipt_id: int
    image_id: str
    field_name: str
    result_index: int
    type: str
    message: str
    reasoning: str
    field: str | None = None
    expected_value: str | None = None
    actual_value: str | None = None
    validation_timestamp: str | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self):
        validate_positive_int("receipt_id", self.receipt_id)
        assert_valid_uuid(self.image_id)
        validate_non_empty_string("field_name", self.field_name)
        validate_non_negative_int("result_index", self.result_index)
        validate_non_empty_string("type", self.type)
        validate_non_empty_string("message", self.message)
        validate_non_empty_string("reasoning", self.reasoning)

        if self.field is not None and not isinstance(self.field, str):
            raise ValueError("field must be a string or None")

        if self.expected_value is not None and not isinstance(
            self.expected_value, str
        ):
            raise ValueError("expected_value must be a string or None")

        if self.actual_value is not None and not isinstance(
            self.actual_value, str
        ):
            raise ValueError("actual_value must be a string or None")

        # pylint: disable=duplicate-code
        # ReceiptValidationResult and ReceiptValidationCategory share similar
        # validation patterns and DynamoDB key structures because they are
        # related validation entities. However, they have distinct SK patterns
        # (Result includes result_index, Category does not) and slightly
        # different validation logic. A shared mixin would require overriding
        # most methods, adding complexity without benefit.
        if self.validation_timestamp is not None:
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
                    f"CATEGORY#{self.field_name}#RESULT#{self.result_index}"
                )
            },
        }
        # pylint: enable=duplicate-code

    @property
    def gsi1_key(self) -> dict[str, dict[str, str]]:
        """Return the GSI1 key for this item."""
        return {
            "GSI1PK": {"S": "ANALYSIS_TYPE"},
            "GSI1SK": {
                "S": (
                    f"VALIDATION#{self.validation_timestamp}#"
                    f"CATEGORY#{self.field_name}#RESULT"
                )
            },
        }

    @property
    def gsi3_key(self) -> dict[str, dict[str, str]]:
        """Return the GSI3 key for this item."""
        return {
            "GSI3PK": {"S": f"RESULT_TYPE#{self.type}"},
            "GSI3SK": {
                "S": (
                    f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}#"
                    f"CATEGORY#{self.field_name}"
                )
            },
        }

    def to_item(self) -> dict[str, Any]:
        """Convert to a DynamoDB item."""
        # Start with the keys which are already properly formatted
        item: dict[str, Any] = {
            **self.key,
            **self.gsi1_key,
            **self.gsi3_key,
            "TYPE": {"S": "RECEIPT_VALIDATION_RESULT"},
        }

        # Add the required fields with proper DynamoDB typing
        item["type"] = {"S": self.type}
        item["message"] = {"S": self.message}
        item["reasoning"] = {"S": self.reasoning}
        # Add validation_timestamp conditionally to avoid type conflicts
        if self.validation_timestamp is not None:
            item["validation_timestamp"] = {"S": self.validation_timestamp}
        else:
            item["validation_timestamp"] = {"NULL": True}

        # Add metadata as a map
        item["metadata"] = self._python_to_dynamo(self.metadata)

        # Add optional fields if they exist
        if self.field is not None:
            item["field"] = {"S": self.field}
        if self.expected_value is not None:
            item["expected_value"] = {"S": self.expected_value}
        if self.actual_value is not None:
            item["actual_value"] = {"S": self.actual_value}

        return item

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptValidationResult":
        """Create a ReceiptValidationResult from a DynamoDB item."""
        # Extract image_id, receipt_id, field_name, and result_index from keys
        image_id = item["PK"]["S"].split("#")[1]
        sk_parts = item["SK"]["S"].split("#")
        receipt_id = int(sk_parts[1])
        field_name = sk_parts[5]
        result_index = int(sk_parts[7])

        # Extract other fields with proper type conversion
        result_type = item.get("type", {}).get("S", "")
        message = item.get("message", {}).get("S", "")
        reasoning = item.get("reasoning", {}).get("S", "")

        # Handle optional fields
        field = item.get("field", {}).get("S") if "field" in item else None
        expected_value = (
            item.get("expected_value", {}).get("S")
            if "expected_value" in item
            else None
        )
        actual_value = (
            item.get("actual_value", {}).get("S")
            if "actual_value" in item
            else None
        )
        validation_timestamp = item.get("validation_timestamp", {}).get("S")

        # Extract metadata with recursive conversion
        metadata = SerializationMixin._dynamo_to_python(
            item.get("metadata", {"M": {}})
        )

        # Create the ReceiptValidationResult
        return cls(
            receipt_id=receipt_id,
            image_id=image_id,
            field_name=field_name,
            result_index=result_index,
            type=result_type,
            message=message,
            reasoning=reasoning,
            field=field,
            expected_value=expected_value,
            actual_value=actual_value,
            validation_timestamp=validation_timestamp,
            metadata=metadata,
        )

    def __repr__(self) -> str:
        return (
            f"ReceiptValidationResult(receipt_id={self.receipt_id}, "
            f"image_id={self.image_id}, "
            f"field_name={self.field_name}, "
            f"result_index={self.result_index}, "
            f"type={self.type})"
        )


def item_to_receipt_validation_result(
    item: dict[str, Any],
) -> ReceiptValidationResult:
    """Convert a DynamoDB item to a ReceiptValidationResult object.

    Args:
        item: DynamoDB item dictionary.

    Returns:
        ReceiptValidationResult: The converted object.
    """
    return ReceiptValidationResult.from_item(item)
