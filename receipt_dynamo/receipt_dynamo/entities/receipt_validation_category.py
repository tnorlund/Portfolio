# receipt_dynamo/receipt_dynamo/entities/receipt_validation_category.py
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from receipt_dynamo.entities.entity_mixins import SerializationMixin
from receipt_dynamo.entities.util import assert_valid_uuid


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
    result_summary: Dict[str, int]
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

        if not isinstance(self.field_category, str):
            raise ValueError("field_category must be a string")
        if not self.field_category:
            raise ValueError("field_category must not be empty")

        if not isinstance(self.status, str):
            raise ValueError("status must be a string")
        if not self.status:
            raise ValueError("status must not be empty")

        if not isinstance(self.reasoning, str):
            raise ValueError("reasoning must be a string")
        if not self.reasoning:
            raise ValueError("reasoning must not be empty")

        if not isinstance(self.result_summary, dict):
            raise ValueError("result_summary must be a dictionary")

        if self.validation_timestamp is not None and not isinstance(self.validation_timestamp, str):
            raise ValueError("validation_timestamp must be a string")
        if self.validation_timestamp is None:
            self.validation_timestamp = datetime.now().isoformat()

        if self.metadata is not None and not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dictionary")
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
                    f"CATEGORY#{self.field_name}"
                )
            },
        }

    @property
    def gsi1_key(self) -> Dict[str, Dict[str, str]]:
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
    def gsi3_key(self) -> Dict[str, Dict[str, str]]:
        """Return the GSI3 key for this item."""
        return {
            "GSI3PK": {"S": f"FIELD_STATUS#{self.field_name}#{self.status}"},
            "GSI3SK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}"
            },
        }

    def to_item(self) -> Dict[str, Any]:
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


def dynamo_to_python(dynamo_value):
    """
    Convert a DynamoDB typed value to a native Python value.

    Args:
        dynamo_value (dict): A DynamoDB formatted value with type indicators
            like S, N, BOOL, M, L, etc.

    Returns:
        The equivalent Python native value (str, int, float, bool, dict,
        list, None)
    """
    if not dynamo_value or not isinstance(dynamo_value, dict):
        return dynamo_value

    if "NULL" in dynamo_value:
        return None
    if "S" in dynamo_value:
        return dynamo_value["S"]
    if "N" in dynamo_value:
        # Try to convert to int if possible, otherwise float
        try:
            return int(dynamo_value["N"])
        except ValueError:
            return float(dynamo_value["N"])
    if "BOOL" in dynamo_value:
        return dynamo_value["BOOL"]
    if "M" in dynamo_value:
        return {k: dynamo_to_python(v) for k, v in dynamo_value["M"].items()}
    if "L" in dynamo_value:
        return [dynamo_to_python(item) for item in dynamo_value["L"]]
    if "SS" in dynamo_value:  # String Set
        return set(dynamo_value["SS"])
    if "NS" in dynamo_value:  # Number Set
        # Try to convert to int if possible, otherwise float
        result = set()
        for num_str in dynamo_value["NS"]:
            try:
                result.add(int(num_str))
            except ValueError:
                result.add(float(num_str))
        return result
    if "BS" in dynamo_value:  # Binary Set
        return set(dynamo_value["BS"])
    # Handle any other type by returning the first value
    for key, value in dynamo_value.items():
        return value
    return None


def item_to_receipt_validation_category(
    item: Dict[str, Any],
) -> ReceiptValidationCategory:
    """Convert a DynamoDB item to a ReceiptValidationCategory object.

    Args:
        item: DynamoDB item dictionary.

    Returns:
        ReceiptValidationCategory: The converted object.
    """
    # Safely extract SK parts
    sk_parts = item["SK"]["S"].split("#")

    # Get receipt_id from SK safely
    receipt_id = None
    for i, part in enumerate(sk_parts):
        if part == "RECEIPT" and i + 1 < len(sk_parts):
            receipt_id = sk_parts[i + 1]
            break

    # If receipt_id not found in SK, look for it as a separate attribute
    if receipt_id is None:
        if "receipt_id" in item:
            receipt_id = (
                int(item["receipt_id"]["N"])
                if "N" in item["receipt_id"]
                else item["receipt_id"]
            )
        else:
            raise ValueError("Could not extract receipt_id from item")
    else:
        # Convert to integer (removing any leading zeros like in "00001")
        try:
            receipt_id = int(receipt_id.lstrip("0"))
        except ValueError:
            receipt_id = int(receipt_id)

    # Get image_id safely
    image_id = (
        item["PK"]["S"].split("#")[1]
        if len(item["PK"]["S"].split("#")) > 1
        else None
    )
    if image_id is None and "image_id" in item:
        image_id = (
            item["image_id"]["S"]
            if "S" in item["image_id"]
            else item["image_id"]
        )
    if image_id is None:
        raise ValueError("Could not extract image_id from item")

    # Get field_name safely
    field_name = None
    # Try to find field_name after "CATEGORY#" in SK if it exists
    for i, part in enumerate(sk_parts):
        if part == "CATEGORY" and i + 1 < len(sk_parts):
            field_name = sk_parts[i + 1]
            break

    # If not found in SK, try direct attribute
    if field_name is None:
        if "field_name" in item:
            field_name = (
                item["field_name"]["S"]
                if "S" in item["field_name"]
                else item["field_name"]
            )
        else:
            # Use a default or extract from another part of the item
            field_name = "unknown"

    # Get field_category safely - first try from the direct attribute
    if "field_category" in item:
        field_category = item["field_category"]["S"]
    else:
        # Try to extract from SK if it follows a pattern like
        # CATEGORY#<field_name>#<category>
        field_category = None
        for i, part in enumerate(sk_parts):
            if part == "CATEGORY" and i + 2 < len(sk_parts):
                # Try to get the category after the field_name
                field_category = sk_parts[i + 2]
                break

        # If still not found, use a default value
        if field_category is None:
            field_category = "general"

    # Get status safely
    if "status" in item:
        status = item["status"]["S"]
    else:
        # Use a default status
        status = "unknown"

    # Get reasoning safely
    if "reasoning" in item:
        reasoning = item["reasoning"]["S"]
    else:
        # Use a default reasoning
        reasoning = "No reasoning provided"

    # Get result_summary safely
    result_summary: Dict[str, Any]
    if "result_summary" in item:
        result_summary = dynamo_to_python(item["result_summary"])
    else:
        # Use an empty dictionary as default
        result_summary = {}

    # Get validation_timestamp safely
    if "validation_timestamp" in item:
        validation_timestamp = item["validation_timestamp"]["S"]
    else:
        # Use current timestamp as default
        validation_timestamp = datetime.now().isoformat()

    # Get metadata safely
    metadata: Dict[str, Any]
    if "metadata" in item:
        metadata = dynamo_to_python(item["metadata"])
    else:
        # Use an empty dictionary as default
        metadata = {}

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
