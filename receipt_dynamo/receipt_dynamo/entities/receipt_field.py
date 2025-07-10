from datetime import datetime
from typing import Any, Dict, Generator, List, Tuple

from receipt_dynamo.entities.util import (
    _format_float,
    _repr_str,
    assert_valid_point,
    assert_valid_uuid,
)


class ReceiptField:
    """
    Represents a field in a receipt in DynamoDB.

    This class encapsulates the field data for a receipt, including the field type,
    associated words, reasoning for the field assignment, and timestamp. It provides methods
    for generating primary and secondary (GSI) keys for DynamoDB operations, converting the
    field to a DynamoDB item, and iterating over its attributes.

    Attributes:
        field_type (str): The type of field (e.g., "BUSINESS_NAME", "ADDRESS", etc.).
        image_id (str): UUID identifying the associated image.
        receipt_id (int): Number identifying the receipt.
        words (List[dict]): List of dictionaries containing word information:
            - word_id (int): ID of the word
            - line_id (int): ID of the line containing the word
            - label (str): Label assigned to the word
        reasoning (str): Explanation for why this field was assigned.
        timestamp_added (str): ISO formatted timestamp when the field was added.
    """

    def __init__(
        self,
        field_type: str,
        image_id: str,
        receipt_id: int,
        words: List[Dict[str, Any]],
        reasoning: str,
        timestamp_added: datetime,
    ):
        """Initializes a new ReceiptField object for DynamoDB.

        Args:
            field_type (str): The type of field.
            image_id (str): UUID identifying the associated image.
            receipt_id (int): Number identifying the receipt.
            words (List[dict]): List of word information dictionaries.
            reasoning (str): Explanation for why this field was assigned.
            timestamp_added (datetime): The timestamp when the field was added.

        Raises:
            ValueError: If any parameter is of an invalid type or has an invalid value.
        """
        if not isinstance(field_type, str):
            raise ValueError("field_type must be a string")
        if not field_type:
            raise ValueError("field_type cannot be empty")
        self.field_type = (
            field_type.upper()
        )  # Store field type in uppercase for consistency

        assert_valid_uuid(image_id)
        self.image_id = image_id

        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if receipt_id <= 0:
            raise ValueError("receipt_id must be positive")
        self.receipt_id: int = receipt_id

        if not isinstance(words, list):
            raise ValueError("words must be a list")
        for word in words:
            if not isinstance(word, dict):
                raise ValueError("each word must be a dictionary")
            required_keys = {"word_id", "line_id", "label"}
            if not required_keys.issubset(word.keys()):
                missing_keys = required_keys - word.keys()
                raise ValueError(f"word missing required keys: {missing_keys}")
            if not isinstance(word["word_id"], int) or word["word_id"] <= 0:
                raise ValueError("word_id must be a positive integer")
            if not isinstance(word["line_id"], int) or word["line_id"] <= 0:
                raise ValueError("line_id must be a positive integer")
            if not isinstance(word["label"], str) or not word["label"]:
                raise ValueError("label must be a non-empty string")
            word["label"] = word["label"].upper()  # Store labels in uppercase
        self.words: List[Dict[str, Any]] = words

        if not isinstance(reasoning, str):
            raise ValueError("reasoning must be a string")
        if not reasoning:
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

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the receipt field.

        Returns:
            dict: The primary key for the receipt field.
        """
        return {
            "PK": {"S": f"FIELD#{self.field_type}"},
            "SK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}"
            },
        }

    def gsi1_key(self) -> Dict[str, Any]:
        """Generate the GSI1 key for this ReceiptField.

        Returns:
            dict: The GSI1 key for the receipt field.
        """
        return {
            "GSI1PK": {"S": f"IMAGE#{self.image_id}"},
            "GSI1SK": {
                "S": f"RECEIPT#{self.receipt_id:05d}#FIELD#{self.field_type}"
            },
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the ReceiptField object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the ReceiptField object as a DynamoDB item.
        """
        return {
            **self.key,
            **self.gsi1_key(),
            "TYPE": {"S": "RECEIPT_FIELD"},
            "words": {
                "L": [
                    {
                        "M": {
                            "word_id": {"N": str(word["word_id"])},
                            "line_id": {"N": str(word["line_id"])},
                            "label": {"S": word["label"]},
                        }
                    }
                    for word in self.words
                ]
            },
            "reasoning": {"S": self.reasoning},
            "timestamp_added": {"S": self.timestamp_added},
        }

    def __repr__(self) -> str:
        """Returns a string representation of the ReceiptField object.

        Returns:
            str: A string representation of the ReceiptField object.
        """
        return (
            "ReceiptField("
            f"field_type={_repr_str(self.field_type)}, "
            f"image_id={_repr_str(self.image_id)}, "
            f"receipt_id={self.receipt_id}, "
            f"words={self.words}, "
            f"reasoning={_repr_str(self.reasoning)}, "
            f"timestamp_added={_repr_str(self.timestamp_added)}"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the ReceiptField object's attributes.

        Returns:
            Generator[Tuple[str, Any], None, None]: An iterator over the ReceiptField object's attribute name/value pairs.
        """
        yield "field_type", self.field_type
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "words", self.words
        yield "reasoning", self.reasoning
        yield "timestamp_added", self.timestamp_added

    def __eq__(self, other) -> bool:
        """Determines whether two ReceiptField objects are equal.

        Args:
            other (ReceiptField): The other ReceiptField object to compare.

        Returns:
            bool: True if the ReceiptField objects are equal, False otherwise.

        Note:
            If other is not an instance of ReceiptField, NotImplemented is returned.
        """
        if not isinstance(other, ReceiptField):
            return NotImplemented
        return (
            self.field_type == other.field_type
            and self.image_id == other.image_id
            and self.receipt_id == other.receipt_id
            and self.words == other.words
            and self.reasoning == other.reasoning
            and self.timestamp_added == other.timestamp_added
        )

    def __hash__(self) -> int:
        """Returns the hash value of the ReceiptField object.

        Returns:
            int: The hash value of the ReceiptField object.
        """
        return hash(
            (
                self.field_type,
                self.image_id,
                self.receipt_id,
                tuple(tuple(sorted(word.items())) for word in self.words),
                self.reasoning,
                self.timestamp_added,
            )
        )


def item_to_receipt_field(item: Dict[str, Any]) -> ReceiptField:
    """Converts a DynamoDB item to a ReceiptField object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptField: The ReceiptField object.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {
        "PK",
        "SK",
        "words",
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
        # Parse SK to get image_id and receipt_id
        sk_parts = item["SK"]["S"].split("#")
        image_id = sk_parts[1]
        receipt_id = int(sk_parts[3])

        # Parse PK to get field_type
        field_type = item["PK"]["S"].split("#")[1]

        # Convert words from DynamoDB format to list of dicts
        words = []
        for word_item in item["words"]["L"]:
            word_dict: Dict[str, Any] = {}
            for key, value in word_item["M"].items():
                if isinstance(value, dict):
                    if "S" in value:
                        word_dict[key] = value["S"]
                    elif "N" in value:
                        word_dict[key] = int(value["N"])
                    else:
                        raise ValueError(
                            f"Unsupported DynamoDB type in word field: {value}"
                        )
                else:
                    word_dict[key] = value
            words.append(word_dict)

        return ReceiptField(
            field_type=field_type,
            image_id=image_id,
            receipt_id=receipt_id,
            words=words,
            reasoning=item["reasoning"]["S"],
            timestamp_added=item["timestamp_added"]["S"],
        )
    except Exception as e:
        raise ValueError(f"Error converting item to ReceiptField: {e}")
