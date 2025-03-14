# receipt_dynamo/receipt_dynamo/entities/receipt_validation_category.py
from typing import Dict, List, Optional, Any
from datetime import datetime
from receipt_dynamo.entities import assert_valid_uuid


class ReceiptValidationCategory:
    """
    DynamoDB entity representing a specific validation category for a receipt.
    Each category contains its own status, reasoning, and summary of results.
    """

    def __init__(
        self,
        receipt_id: int,
        image_id: str,
        field_name: str,
        field_category: str,
        status: str,
        reasoning: str,
        result_summary: Dict[str, int],
        validation_timestamp: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if receipt_id <= 0:
            raise ValueError("receipt_id must be positive")
        self.receipt_id = receipt_id

        assert_valid_uuid(image_id)
        self.image_id = image_id

        if not isinstance(field_name, str):
            raise ValueError("field_name must be a string")
        if not field_name:
            raise ValueError("field_name must not be empty")
        self.field_name = field_name

        if not isinstance(field_category, str):
            raise ValueError("field_category must be a string")
        if not field_category:
            raise ValueError("field_category must not be empty")
        self.field_category = field_category

        if not isinstance(status, str):
            raise ValueError("status must be a string")
        if not status:
            raise ValueError("status must not be empty")
        self.status = status

        if not isinstance(reasoning, str):
            raise ValueError("reasoning must be a string")
        if not reasoning:
            raise ValueError("reasoning must not be empty")
        self.reasoning = reasoning

        if not isinstance(result_summary, dict):
            raise ValueError("result_summary must be a dictionary")
        self.result_summary = result_summary

        if not isinstance(validation_timestamp, str):
            raise ValueError("validation_timestamp must be a string")
        self.validation_timestamp = (
            validation_timestamp or datetime.now().isoformat()
        )

        if not isinstance(metadata, dict):
            raise ValueError("metadata must be a dictionary")
        self.metadata = metadata or {}

    @property
    def key(self) -> Dict[str, Dict[str, str]]:
        """Return the DynamoDB key for this item."""
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": f"RECEIPT#{self.receipt_id}#ANALYSIS#VALIDATION#CATEGORY#{self.field_name}"
            },
        }

    @property
    def gsi1_key(self) -> Dict[str, Dict[str, str]]:
        """Return the GSI1 key for this item."""
        return {
            "GSI1PK": {"S": "ANALYSIS_TYPE"},
            "GSI1SK": {
                "S": f"VALIDATION#{self.validation_timestamp}#CATEGORY#{self.field_name}"
            },
        }

    @property
    def gsi2_key(self) -> Dict[str, Dict[str, str]]:
        """Return the GSI2 key for this item."""
        return {
            "GSI2PK": {"S": "RECEIPT"},
            "GSI2SK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id}#VALIDATION"
            },
        }

    @property
    def gsi3_key(self) -> Dict[str, Dict[str, str]]:
        """Return the GSI3 key for this item."""
        return {
            "GSI3PK": {"S": f"FIELD_STATUS#{self.field_name}#{self.status}"},
            "GSI3SK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id}"
            },
        }

    def _python_to_dynamo(self, value: Any) -> Dict[str, Any]:
        """Convert a Python value to a DynamoDB typed value."""
        if value is None:
            return {"NULL": True}
        elif isinstance(value, str):
            return {"S": value}
        elif isinstance(value, (int, float)):
            return {"N": str(value)}
        elif isinstance(value, bool):
            return {"BOOL": value}
        elif isinstance(value, dict):
            return {
                "M": {k: self._python_to_dynamo(v) for k, v in value.items()}
            }
        elif isinstance(value, list):
            return {"L": [self._python_to_dynamo(item) for item in value]}
        else:
            # Convert any other type to string
            return {"S": str(value)}

    def to_item(self) -> Dict[str, Any]:
        """Convert to a DynamoDB item."""
        # Start with the keys which are already properly formatted
        item = {
            **self.key,
            **self.gsi1_key,
            **self.gsi2_key,
            **self.gsi3_key,
        }

        # Add the required fields with proper DynamoDB typing
        item["field_category"] = {"S": self.field_category}
        item["status"] = {"S": self.status}
        item["reasoning"] = {"S": self.reasoning}
        item["result_summary"] = self._python_to_dynamo(self.result_summary)
        item["validation_timestamp"] = {"S": self.validation_timestamp}
        item["metadata"] = self._python_to_dynamo(self.metadata)

        return item

    @classmethod
    def from_item(cls, item: Dict[str, Any]) -> "ReceiptValidationCategory":
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
        result_summary = cls._dynamo_to_python(
            item.get("result_summary", {"M": {}})
        )
        metadata = cls._dynamo_to_python(item.get("metadata", {"M": {}}))

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

    @staticmethod
    def _dynamo_to_python(dynamo_value: Dict[str, Any]) -> Any:
        """Convert a DynamoDB typed value to a Python value."""
        if "NULL" in dynamo_value:
            return None
        elif "S" in dynamo_value:
            return dynamo_value["S"]
        elif "N" in dynamo_value:
            # Try to convert to int if possible, otherwise float
            try:
                return int(dynamo_value["N"])
            except ValueError:
                return float(dynamo_value["N"])
        elif "BOOL" in dynamo_value:
            return dynamo_value["BOOL"]
        elif "M" in dynamo_value:
            return {
                k: ReceiptValidationCategory._dynamo_to_python(v)
                for k, v in dynamo_value["M"].items()
            }
        elif "L" in dynamo_value:
            return [
                ReceiptValidationCategory._dynamo_to_python(item)
                for item in dynamo_value["L"]
            ]
        else:
            # Handle any other type
            for key, value in dynamo_value.items():
                return value
            return None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ReceiptValidationCategory):
            return False
        return (
            self.receipt_id == other.receipt_id
            and self.image_id == other.image_id
            and self.field_name == other.field_name
            and self.status == other.status
        )

    def __repr__(self) -> str:
        return (
            f"ReceiptValidationCategory(receipt_id={self.receipt_id}, "
            f"image_id={self.image_id}, "
            f"field_name={self.field_name}, "
            f"status={self.status})"
        )


def itemToReceiptValidationCategory(
    item: Dict[str, Any],
) -> ReceiptValidationCategory:
    """Convert a DynamoDB item to a ReceiptValidationCategory object.

    Args:
        item: DynamoDB item dictionary.

    Returns:
        ReceiptValidationCategory: The converted object.
    """
    return ReceiptValidationCategory.from_item(item)
