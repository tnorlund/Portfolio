# receipt_dynamo/receipt_dynamo/entities/receipt_validation_result.py
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from receipt_dynamo.entities.entity_mixins import SerializationMixin
from receipt_dynamo.entities.util import assert_valid_uuid


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
    field: Optional[str] = None
    expected_value: Optional[str] = None
    actual_value: Optional[str] = None
    validation_timestamp: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if not isinstance(self.receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if self.receipt_id <= 0:
            raise ValueError("receipt_id must be positive")

        assert_valid_uuid(self.image_id)

        if not isinstance(self.field_name, str):
            raise ValueError("field_name must be a string")
        if not self.field_name:
            raise ValueError("field_name must not be empty")

        if not isinstance(self.result_index, int):
            raise ValueError("result_index must be an integer")
        if self.result_index < 0:
            raise ValueError("result_index must be positive")

        if not isinstance(self.type, str):
            raise ValueError("type must be a string")
        if not self.type:
            raise ValueError("type must not be empty")

        if not isinstance(self.message, str):
            raise ValueError("message must be a string")
        if not self.message:
            raise ValueError("message must not be empty")

        if not isinstance(self.reasoning, str):
            raise ValueError("reasoning must be a string")
        if not self.reasoning:
            raise ValueError("reasoning must not be empty")

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

        if isinstance(self.validation_timestamp, datetime):
            self.validation_timestamp = self.validation_timestamp.isoformat()
        elif isinstance(self.validation_timestamp, str):
            pass  # Already a string
        elif self.validation_timestamp is None:
            pass  # Leave as None
        else:
            raise ValueError(
                "validation_timestamp must be a datetime, string, or None"
            )

        if self.metadata is not None and not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dictionary or None")
        if self.metadata is None:
            self.metadata = {}

    @property
    def key(self) -> Dict[str, Dict[str, str]]:
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

    @property
    def gsi1_key(self) -> Dict[str, Dict[str, str]]:
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
    def gsi3_key(self) -> Dict[str, Dict[str, str]]:
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

    def to_item(self) -> Dict[str, Any]:
        """Convert to a DynamoDB item."""
        # Start with the keys which are already properly formatted
        item: Dict[str, Any] = {
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
    def from_item(cls, item: Dict[str, Any]) -> "ReceiptValidationResult":
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
    item: Dict[str, Any],
) -> ReceiptValidationResult:
    """Convert a DynamoDB item to a ReceiptValidationResult object.

    Args:
        item: DynamoDB item dictionary.

    Returns:
        ReceiptValidationResult: The converted object.
    """
    return ReceiptValidationResult.from_item(item)
