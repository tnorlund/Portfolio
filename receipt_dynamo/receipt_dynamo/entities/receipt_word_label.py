from datetime import datetime
from typing import Any, Dict, Generator, Optional, Tuple

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities.util import (
    _format_float,
    _repr_str,
    assert_valid_point,
    assert_valid_uuid,
    normalize_enum,
)


class ReceiptWordLabel:
    """
    Represents a label for a word in a receipt line in DynamoDB.

    This class encapsulates the label data for a word in a receipt line, including
    the label type, reasoning for the label assignment, and timestamp. It provides methods for generating
    primary and secondary (GSI) keys for DynamoDB operations, converting the label to a
    DynamoDB item, and iterating over its attributes.

    Attributes:
        image_id (str): UUID identifying the associated image.
        receipt_id (int): Number identifying the receipt.
        line_id (int): Number identifying the line containing the word.
        word_id (int): Number identifying the word.
        label (str): The label assigned to the word.
        reasoning (str): Explanation for why this label was assigned.
        timestamp_added (str): ISO formatted timestamp when the label was added.
    """

    def __init__(
        self,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
        label: str,
        reasoning: str | None,
        timestamp_added: datetime,
        validation_status: Optional[str] = None,
        label_proposed_by: Optional[str] = None,
        label_consolidated_from: Optional[str] = None,
    ):
        """Initializes a new ReceiptWordLabel object for DynamoDB.

        Args:
            image_id (str): UUID identifying the associated image.
            receipt_id (int): Number identifying the receipt.
            line_id (int): Number identifying the line containing the word.
            word_id (int): Number identifying the word.
            label (str): The label assigned to the word.
            reasoning (str): Explanation for why this label was assigned.
            timestamp_added (datetime): The timestamp when the label was added.
            validation_status (Optional[str]): The status of the label validation.
        Raises:
            ValueError: If any parameter is of an invalid type or has an invalid value.
        """
        assert_valid_uuid(image_id)
        self.image_id = image_id

        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if receipt_id <= 0:
            raise ValueError("receipt_id must be positive")
        self.receipt_id: int = receipt_id

        if not isinstance(line_id, int):
            raise ValueError("line_id must be an integer")
        if line_id <= 0:
            raise ValueError("line_id must be positive")
        self.line_id: int = line_id

        if not isinstance(word_id, int):
            raise ValueError("word_id must be an integer")
        if word_id <= 0:
            raise ValueError("word_id must be positive")
        self.word_id: int = word_id

        if not isinstance(label, str):
            raise ValueError("label must be a string")
        if not label:
            raise ValueError("label cannot be empty")
        self.label = label.upper()  # Store labels in uppercase for consistency

        if not isinstance(reasoning, str | None):
            raise ValueError("reasoning must be a string or None")
        if reasoning is not None and not reasoning:
            raise ValueError("reasoning cannot be empty")
        self.reasoning = reasoning

        self.timestamp_added: str
        if isinstance(timestamp_added, datetime):
            self.timestamp_added = timestamp_added.isoformat()
        elif isinstance(timestamp_added, str):
            self.timestamp_added = timestamp_added
        else:
            raise ValueError(
                "timestamp_added must be a datetime object or a string"
            )

        # Always assign a valid enum value for validation_status
        status = validation_status or ValidationStatus.NONE.value
        self.validation_status = normalize_enum(status, ValidationStatus)

        self.label_proposed_by: Optional[str]
        if label_proposed_by is not None:
            if not isinstance(label_proposed_by, str):
                raise ValueError("label_proposed_by must be a string")
            if not label_proposed_by:
                raise ValueError("label_proposed_by cannot be empty")
            self.label_proposed_by = label_proposed_by
        else:
            self.label_proposed_by = None

        self.label_consolidated_from: Optional[str]
        if label_consolidated_from is not None:
            if not isinstance(label_consolidated_from, str):
                raise ValueError("label_consolidated_from must be a string")
            if not label_consolidated_from:
                raise ValueError("label_consolidated_from cannot be empty")
            self.label_consolidated_from = label_consolidated_from
        else:
            self.label_consolidated_from = None

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the receipt word label.

        Returns:
            dict: The primary key for the receipt word label.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": f"RECEIPT#{self.receipt_id:05d}#LINE#{self.line_id:05d}#WORD#{self.word_id:05d}#LABEL#{self.label}"
            },
        }

    def gsi1_key(self) -> Dict[str, Any]:
        """Generate the GSI1 key for this ReceiptWordLabel.

        The GSI1PK will be exactly 40 characters long, with the format:
        "LABEL#<label><padding_underscores>"
        """
        label_upper = self.label
        prefix = "LABEL#"
        # Calculate padding needed to make total length 40
        current_length = len(prefix) + len(label_upper)
        padding_length = 40 - current_length
        spaced_label_upper = f"{prefix}{label_upper}{'_' * padding_length}"
        return {
            "GSI1PK": {"S": spaced_label_upper},
            "GSI1SK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}#LINE#{self.line_id:05d}#WORD#{self.word_id:05d}"
            },
        }

    def gsi2_key(self) -> Dict[str, Any]:
        """
        Generates the secondary index key for the receipt word label.

        Returns:
            dict: The secondary index key for the receipt word label.
        """
        return {
            "GSI2PK": {"S": "RECEIPT"},
            "GSI2SK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}#LINE#{self.line_id:05d}#WORD#{self.word_id:05d}"
            },
        }

    def gsi3_key(self) -> Dict[str, Any]:
        """
        Generates the GSI3 key for the receipt word label.

        Returns:
            dict: The GSI3 key for the receipt word label.
        """
        return {
            "GSI3PK": {"S": f"VALIDATION_STATUS#{self.validation_status}"},
            "GSI3SK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}#LINE#{self.line_id:05d}#WORD#{self.word_id:05d}#LABEL#{self.label}"
            },
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the ReceiptWordLabel object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the ReceiptWordLabel object as a DynamoDB item.
        """
        return {
            **self.key,
            **self.gsi1_key(),
            **self.gsi2_key(),
            **self.gsi3_key(),
            "TYPE": {"S": "RECEIPT_WORD_LABEL"},
            "reasoning": (
                {"S": self.reasoning}
                if self.reasoning is not None
                else {"NULL": True}
            ),
            "timestamp_added": {"S": self.timestamp_added},
            "validation_status": {"S": self.validation_status},
            "label_consolidated_from": (
                {"S": self.label_consolidated_from}
                if self.label_consolidated_from is not None
                else {"NULL": True}
            ),
            "label_proposed_by": (
                {"S": self.label_proposed_by}
                if self.label_proposed_by is not None
                else {"NULL": True}
            ),
        }

    def to_receipt_word_key(self) -> Dict[str, Any]:
        """Generates the key for the ReceiptWord table associated with this label.

        Returns:
            dict: A dictionary representing the key for the ReceiptWord in DynamoDB.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}"
                    f"#LINE#{self.line_id:05d}"
                    f"#WORD#{self.word_id:05d}"
                )
            },
        }

    def __repr__(self) -> str:
        """Returns a string representation of the ReceiptWordLabel object.

        Returns:
            str: A string representation of the ReceiptWordLabel object.
        """
        return (
            "ReceiptWordLabel("
            f"image_id={_repr_str(self.image_id)}, "
            f"receipt_id={self.receipt_id}, "
            f"line_id={self.line_id}, "
            f"word_id={self.word_id}, "
            f"label={_repr_str(self.label)}, "
            f"reasoning={_repr_str(self.reasoning)}, "
            f"timestamp_added={_repr_str(self.timestamp_added)}, "
            f"validation_status={_repr_str(self.validation_status)}, "
            f"label_consolidated_from={_repr_str(self.label_consolidated_from)}, "
            f"label_proposed_by={_repr_str(self.label_proposed_by)}"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the ReceiptWordLabel object's attributes.

        Returns:
            Generator[Tuple[str, Any], None, None]: An iterator over the ReceiptWordLabel object's attribute name/value pairs.
        """
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "line_id", self.line_id
        yield "word_id", self.word_id
        yield "label", self.label
        yield "reasoning", self.reasoning
        yield "timestamp_added", self.timestamp_added
        yield "validation_status", self.validation_status
        yield "label_consolidated_from", self.label_consolidated_from
        yield "label_proposed_by", self.label_proposed_by

    def __eq__(self, other) -> bool:
        """Determines whether two ReceiptWordLabel objects are equal.

        Args:
            other (ReceiptWordLabel): The other ReceiptWordLabel object to compare.

        Returns:
            bool: True if the ReceiptWordLabel objects are equal, False otherwise.

        Note:
            If other is not an instance of ReceiptWordLabel, NotImplemented is returned.
        """
        if not isinstance(other, ReceiptWordLabel):
            return NotImplemented
        return (
            self.image_id == other.image_id
            and self.receipt_id == other.receipt_id
            and self.line_id == other.line_id
            and self.word_id == other.word_id
            and self.label == other.label
            and self.reasoning == other.reasoning
            and self.timestamp_added == other.timestamp_added
            and self.validation_status == other.validation_status
            and self.label_consolidated_from == other.label_consolidated_from
            and self.label_proposed_by == other.label_proposed_by
        )

    def __hash__(self) -> int:
        """Returns the hash value of the ReceiptWordLabel object.

        Returns:
            int: The hash value of the ReceiptWordLabel object.
        """
        return hash(
            (
                self.image_id,
                self.receipt_id,
                self.line_id,
                self.word_id,
                self.label,
                self.reasoning,
                self.timestamp_added,
                self.validation_status,
                self.label_consolidated_from,
                self.label_proposed_by,
            )
        )


def item_to_receipt_word_label(item: Dict[str, Any]) -> ReceiptWordLabel:
    """Converts a DynamoDB item to a ReceiptWordLabel object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptWordLabel: The ReceiptWordLabel object.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {
        "PK",
        "SK",
        "reasoning",
        "timestamp_added",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}\nadditional keys: {additional_keys}"
        )
    try:
        sk_parts = item["SK"]["S"].split("#")
        image_id = item["PK"]["S"].split("#")[1]
        receipt_id = int(sk_parts[1])
        line_id = int(sk_parts[3])
        word_id = int(sk_parts[5])
        label = sk_parts[7]
        reasoning = (
            item["reasoning"]["S"] if "S" in item["reasoning"] else None
        )
        timestamp_added = item["timestamp_added"]["S"]
        validation_status = None
        if "validation_status" in item:
            # Check if the value is NULL (None in DynamoDB)
            if "NULL" in item["validation_status"]:
                validation_status = None
            # Check if it's a string value
            elif "S" in item["validation_status"]:
                validation_status = item["validation_status"]["S"]

        label_consolidated_from = None
        if "label_consolidated_from" in item:
            if "NULL" in item["label_consolidated_from"]:
                label_consolidated_from = None
            elif "S" in item["label_consolidated_from"]:
                label_consolidated_from = item["label_consolidated_from"]["S"]

        label_proposed_by = None
        if "label_proposed_by" in item:
            if "NULL" in item["label_proposed_by"]:
                label_proposed_by = None
            elif "S" in item["label_proposed_by"]:
                label_proposed_by = item["label_proposed_by"]["S"]

        return ReceiptWordLabel(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            label=label,
            reasoning=reasoning,
            timestamp_added=timestamp_added,
            validation_status=validation_status,
            label_consolidated_from=label_consolidated_from,
            label_proposed_by=label_proposed_by,
        )
    except Exception as e:
        raise ValueError(f"Error converting item to ReceiptWordLabel: {e}")
