"""DynamoDB entity for a normalized receipt field and its source words."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generator

from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
    validate_iso_timestamp,
    validate_positive_int,
)


@dataclass(eq=True, unsafe_hash=False)
class ReceiptField:
    """
    Represents a field in a receipt in DynamoDB.

    This class encapsulates the field data for a receipt, including the field
    type, associated words, reasoning for the field assignment, and timestamp.
    It provides methods for generating primary and secondary (GSI) keys for
    DynamoDB operations, converting the
    field to a DynamoDB item, and iterating over its attributes.

    Attributes:
        field_type (str): The type of field (e.g., "BUSINESS_NAME",
            "ADDRESS", etc.).
        image_id (str): UUID identifying the associated image.
        receipt_id (int): Number identifying the receipt.
        words (list[dict]): List of dictionaries containing word information:
            - word_id (int): ID of the word
            - line_id (int): ID of the line containing the word
            - label (str): Label assigned to the word
        reasoning (str): Explanation for why this field was assigned.
        timestamp_added (str): ISO formatted timestamp when the field was
            added.
    """

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "TYPE",
        "words",
        "reasoning",
        "timestamp_added",
    }

    field_type: str
    image_id: str
    receipt_id: int
    words: list[dict[str, Any]]
    reasoning: str
    timestamp_added: datetime | str

    def __post_init__(self):
        """Initializes and validates the ReceiptField object.

        Raises:
            ValueError: If any parameter is of an invalid type or has an
                invalid value.
        """
        if not isinstance(self.field_type, str):
            raise ValueError("field_type must be a string")
        if not self.field_type.strip():
            raise ValueError("field_type cannot be empty")
        self.field_type = self.field_type.strip().upper()

        assert_valid_uuid(self.image_id)

        validate_positive_int("receipt_id", self.receipt_id)

        if not isinstance(self.words, list):
            raise ValueError("words must be a list")
        normalized_words = []
        required_keys = {"word_id", "line_id", "label"}
        for word in self.words:
            if not isinstance(word, dict):
                raise ValueError("each word must be a dictionary")
            if set(word) != required_keys:
                missing_keys = required_keys - word.keys()
                extra_keys = word.keys() - required_keys
                raise ValueError(
                    "word must contain exactly word_id, line_id, and label; "
                    f"missing keys: {missing_keys}; extra keys: {extra_keys}"
                )
            validate_positive_int("word_id", word["word_id"])
            validate_positive_int("line_id", word["line_id"])
            if not isinstance(word["label"], str) or not word["label"].strip():
                raise ValueError("label must be a non-empty string")
            normalized_words.append(
                {
                    "word_id": word["word_id"],
                    "line_id": word["line_id"],
                    "label": word["label"].strip().upper(),
                }
            )
        self.words = normalized_words

        if not isinstance(self.reasoning, str):
            raise ValueError("reasoning must be a string")
        if not self.reasoning.strip():
            raise ValueError("reasoning cannot be empty")
        self.timestamp_added = validate_iso_timestamp(
            self.timestamp_added, "timestamp_added", default_now=False
        )

    @property
    def key(self) -> dict[str, Any]:
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

    def gsi1_key(self) -> dict[str, Any]:
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

    def to_item(self) -> dict[str, Any]:
        """Converts the ReceiptField object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the ReceiptField object as a
                DynamoDB item.
        """
        self.__post_init__()
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

    def __iter__(self) -> Generator[tuple[str, Any], None, None]:
        """Returns an iterator over the ReceiptField object's attributes.

        Returns:
            Generator[tuple[str, Any], None, None]: An iterator over the
                ReceiptField object's attribute name/value pairs.
        """
        yield "field_type", self.field_type
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "words", self.words
        yield "reasoning", self.reasoning
        yield "timestamp_added", self.timestamp_added

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

    @classmethod
    def from_item(  # pylint: disable=too-many-locals,too-many-branches
        cls, item: dict[str, Any]
    ) -> "ReceiptField":
        """Converts a DynamoDB item to a ReceiptField object.

        Args:
            item: The DynamoDB item to convert.

        Returns:
            ReceiptField: The ReceiptField object.

        Raises:
            ValueError: When the item format is invalid.
        """
        if not cls.REQUIRED_KEYS.issubset(item.keys()):
            missing_keys = cls.REQUIRED_KEYS - item.keys()
            additional_keys = item.keys() - cls.REQUIRED_KEYS
            raise ValueError(
                f"Invalid item format\nmissing keys: {missing_keys}\n"
                f"additional keys: {additional_keys}"
            )
        try:
            if item["TYPE"] != {"S": "RECEIPT_FIELD"}:
                raise ValueError("TYPE must be RECEIPT_FIELD")

            # Parse SK to get image_id and receipt_id
            sk_parts = item["SK"]["S"].split("#")
            if (
                len(sk_parts) != 4
                or sk_parts[0] != "IMAGE"
                or sk_parts[2] != "RECEIPT"
            ):
                raise ValueError("Invalid SK format for ReceiptField")
            image_id = sk_parts[1]
            receipt_id = int(sk_parts[3])

            # Parse PK to get field_type
            pk_parts = item["PK"]["S"].split("#")
            if len(pk_parts) != 2 or pk_parts[0] != "FIELD":
                raise ValueError("Invalid PK format for ReceiptField")
            field_type = pk_parts[1]

            # Convert words from DynamoDB format to list of dicts
            words = []
            for word_item in item["words"]["L"]:
                word_dict: dict[str, Any] = {}
                for key, value in word_item["M"].items():
                    if isinstance(value, dict):
                        if "S" in value:
                            word_dict[key] = value["S"]
                        elif "N" in value:
                            word_dict[key] = int(value["N"])
                        else:
                            raise ValueError(
                                f"Unsupported DynamoDB type in word field: "
                                f"{value}"
                            )
                    else:
                        word_dict[key] = value
                words.append(word_dict)

            result = cls(
                field_type=field_type,
                image_id=image_id,
                receipt_id=receipt_id,
                words=words,
                reasoning=item["reasoning"]["S"],
                timestamp_added=item["timestamp_added"]["S"],
            )
            for key_name, expected_value in {
                **result.key,
                **result.gsi1_key(),
            }.items():
                if key_name in item and item[key_name] != expected_value:
                    raise ValueError(f"{key_name} does not match entity keys")
            return result
        except Exception as e:
            raise ValueError(
                f"Error converting item to ReceiptField: {e}"
            ) from e


def item_to_receipt_field(item: dict[str, Any]) -> ReceiptField:
    """Converts a DynamoDB item to a ReceiptField object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptField: The ReceiptField object.

    Raises:
        ValueError: When the item format is invalid.
    """
    return ReceiptField.from_item(item)
