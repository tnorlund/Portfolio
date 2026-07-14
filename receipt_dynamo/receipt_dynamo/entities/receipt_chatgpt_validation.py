# receipt_dynamo/receipt_dynamo/entities/receipt_chatgpt_validation.py
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from receipt_dynamo.entities.entity_mixins import SerializationMixin
from receipt_dynamo.entities.util import (
    assert_valid_uuid,
    validate_iso_timestamp,
    validate_metadata_field,
    validate_non_empty_string,
    validate_positive_int,
)


@dataclass(eq=True, unsafe_hash=False)
class ReceiptChatGPTValidation(SerializationMixin):
    """
    DynamoDB entity representing a second-pass validation by ChatGPT.
    This item contains ChatGPT's assessment of validation results and any
    corrections.
    """

    receipt_id: int
    image_id: str
    original_status: str
    revised_status: str
    reasoning: str
    corrections: list[dict[str, Any]]
    prompt: str
    response: str
    timestamp: str | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self):
        validate_positive_int("receipt_id", self.receipt_id)
        assert_valid_uuid(self.image_id)
        validate_non_empty_string("original_status", self.original_status)
        validate_non_empty_string("revised_status", self.revised_status)
        validate_non_empty_string("reasoning", self.reasoning)

        if not isinstance(self.corrections, list):
            raise ValueError("corrections must be a list")
        if not all(
            isinstance(correction, dict) for correction in self.corrections
        ):
            raise ValueError("corrections must contain dictionaries")
        self.corrections = deepcopy(self.corrections)

        validate_non_empty_string("prompt", self.prompt)
        validate_non_empty_string("response", self.response)

        self.timestamp = validate_iso_timestamp(self.timestamp, "timestamp")
        self.metadata = deepcopy(validate_metadata_field(self.metadata))

    @property
    def key(self) -> dict[str, dict[str, str]]:
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
    def gsi1_key(self) -> dict[str, dict[str, str]]:
        """Return the GSI1 key for this item."""
        return {
            "GSI1PK": {"S": "ANALYSIS_TYPE"},
            "GSI1SK": {"S": f"VALIDATION_CHATGPT#{self.timestamp}"},
        }

    @property
    def gsi3_key(self) -> dict[str, dict[str, str]]:
        """Return the GSI3 key for this item."""
        return {
            "GSI3PK": {"S": f"VALIDATION_STATUS#{self.revised_status}"},
            "GSI3SK": {"S": f"CHATGPT#{self.timestamp}"},
        }

    def to_item(self) -> dict[str, Any]:
        """Convert to a DynamoDB item."""
        assert self.timestamp is not None
        # Start with the keys which are already properly formatted
        item = {
            **self.key,
            **self.gsi1_key,
            **self.gsi3_key,
            "TYPE": {"S": "RECEIPT_CHATGPT_VALIDATION"},
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
    def from_item(cls, item: dict[str, Any]) -> "ReceiptChatGPTValidation":
        """Create a ReceiptChatGPTValidation from a DynamoDB item."""
        cls.validate_required_keys(
            item,
            {
                "PK",
                "SK",
                "original_status",
                "revised_status",
                "reasoning",
                "corrections",
                "prompt",
                "response",
            },
        )
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
            corrections=SerializationMixin._dynamo_to_python(
                item["corrections"]
            ),
            prompt=item["prompt"]["S"],
            response=item["response"]["S"],
            timestamp=timestamp,
            metadata=(
                SerializationMixin._dynamo_to_python(item["metadata"])
                if "metadata" in item
                else {}
            ),
        )

    def __repr__(self) -> str:
        return (
            f"ReceiptChatGPTValidation(receipt_id={self.receipt_id}, "
            f"image_id={self.image_id}, "
            f"original_status={self.original_status}, "
            f"revised_status={self.revised_status})"
        )


def item_to_receipt_chat_gpt_validation(
    item: dict[str, Any],
) -> ReceiptChatGPTValidation:
    """Convert a DynamoDB item to a ReceiptChatGPTValidation object."""
    return ReceiptChatGPTValidation.from_item(item)
