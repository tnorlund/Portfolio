# receipt_dynamo/receipt_dynamo/entities/receipt_chatgpt_validation.py
from datetime import datetime
from typing import Any, Dict, List, Optional

from receipt_dynamo.entities.util import assert_valid_uuid


class ReceiptChatGPTValidation:
    """
    DynamoDB entity representing a second-pass validation by ChatGPT.
    This item contains ChatGPT's assessment of validation results and any
    corrections.
    """

    def __init__(
        self,
        receipt_id: int,
        image_id: str,
        original_status: str,
        revised_status: str,
        reasoning: str,
        corrections: List[Dict[str, Any]],
        prompt: str,
        response: str,
        timestamp: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if receipt_id <= 0:
            raise ValueError("receipt_id must be positive")
        self.receipt_id: int = receipt_id

        assert_valid_uuid(image_id)
        self.image_id = image_id

        if not isinstance(original_status, str):
            raise ValueError("original_status must be a string")
        self.original_status = original_status

        if not isinstance(revised_status, str):
            raise ValueError("revised_status must be a string")
        self.revised_status = revised_status

        if not isinstance(reasoning, str):
            raise ValueError("reasoning must be a string")
        self.reasoning = reasoning

        if not isinstance(corrections, list):
            raise ValueError("corrections must be a list")
        self.corrections = corrections

        if not isinstance(prompt, str):
            raise ValueError("prompt must be a string")
        self.prompt = prompt

        if not isinstance(response, str):
            raise ValueError("response must be a string")
        self.response = response

        if not isinstance(timestamp, str):
            raise ValueError("timestamp must be a string")
        self.timestamp = timestamp or datetime.now().isoformat()

        if not isinstance(metadata, dict):
            raise ValueError("metadata must be a dictionary")
        self.metadata = metadata or {}

    @property
    def key(self) -> Dict[str, Dict[str, str]]:
        """Return the DynamoDB key for this item."""
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id}#ANALYSIS#VALIDATION#"
                    f"CHATGPT#{self.timestamp}"
                )
            },
        }

    @property
    def gsi1_key(self) -> Dict[str, Dict[str, str]]:
        """Return the GSI1 key for this item."""
        return {
            "GSI1PK": {"S": "ANALYSIS_TYPE"},
            "GSI1SK": {"S": f"VALIDATION_CHATGPT#{self.timestamp}"},
        }

    @property
    def gsi3_key(self) -> Dict[str, Dict[str, str]]:
        """Return the GSI3 key for this item."""
        return {
            "GSI3PK": {"S": f"VALIDATION_STATUS#{self.revised_status}"},
            "GSI3SK": {"S": f"CHATGPT#{self.timestamp}"},
        }

    def _python_to_dynamo(self, value: Any) -> Dict[str, Any]:
        """Convert a Python value to a DynamoDB typed value."""
        if value is None:
            return {"NULL": True}
        elif isinstance(
            value, bool
        ):  # Check for bool before int since bool is a subclass of int
            return {"BOOL": value}
        elif isinstance(value, str):
            return {"S": value}
        elif isinstance(value, (int, float)):
            return {"N": str(value)}
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
            **self.gsi3_key,
        }

        # Add the required fields with proper DynamoDB typing
        item["original_status"] = {"S": self.original_status}
        item["revised_status"] = {"S": self.revised_status}
        item["reasoning"] = {"S": self.reasoning}
        item["corrections"] = self._python_to_dynamo(self.corrections)
        item["prompt"] = {"S": self.prompt}
        item["response"] = {"S": self.response}
        item["timestamp"] = {"S": self.timestamp}
        item["metadata"] = self._python_to_dynamo(self.metadata)

        return item

    @classmethod
    def from_item(cls, item: Dict[str, Any]) -> "ReceiptChatGPTValidation":
        """Create a ReceiptChatGPTValidation from a DynamoDB item."""
        # Extract image_id, receipt_id, and timestamp from keys
        image_id = item["PK"]["S"].split("#")[1]
        sk_parts = item["SK"]["S"].split("#")
        receipt_id = int(sk_parts[1])
        timestamp = sk_parts[5]

        # Create the ReceiptChatGPTValidation
        return cls(
            receipt_id=receipt_id,
            image_id=image_id,
            original_status=item["original_status"]["S"],
            revised_status=item["revised_status"]["S"],
            reasoning=item["reasoning"]["S"],
            corrections=cls._dynamo_to_python(item["corrections"]),
            prompt=item["prompt"]["S"],
            response=item["response"]["S"],
            timestamp=timestamp,
            metadata=(
                cls._dynamo_to_python(item["metadata"])
                if "metadata" in item
                else {}
            ),
        )

    @staticmethod
    def _dynamo_to_python(dynamo_value: Dict[str, Any]) -> Any:
        """Convert a DynamoDB typed value to a Python value."""
        if "NULL" in dynamo_value:
            return None
        elif "S" in dynamo_value:
            return dynamo_value["S"]
        elif "N" in dynamo_value:
            # Try to convert to int first, then float if that fails
            try:
                return int(dynamo_value["N"])
            except ValueError:
                return float(dynamo_value["N"])
        elif "BOOL" in dynamo_value:
            return dynamo_value["BOOL"]
        elif "M" in dynamo_value:
            return {
                k: ReceiptChatGPTValidation._dynamo_to_python(v)
                for k, v in dynamo_value["M"].items()
            }
        elif "L" in dynamo_value:
            return [
                ReceiptChatGPTValidation._dynamo_to_python(item)
                for item in dynamo_value["L"]
            ]
        else:
            # Return empty dict for unsupported types
            return {}

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ReceiptChatGPTValidation):
            return False
        return (
            self.receipt_id == other.receipt_id
            and self.image_id == other.image_id
            and self.timestamp == other.timestamp
        )

    def __repr__(self) -> str:
        return (
            f"ReceiptChatGPTValidation(receipt_id={self.receipt_id}, "
            f"image_id={self.image_id}, "
            f"original_status={self.original_status}, "
            f"revised_status={self.revised_status})"
        )


def item_to_receipt_chat_gpt_validation(
    item: Dict[str, Any],
) -> ReceiptChatGPTValidation:
    """Convert a DynamoDB item to a ReceiptChatGPTValidation object."""
    return ReceiptChatGPTValidation.from_item(item)
