from typing import Generator, Tuple
from datetime import datetime
from dynamo.entities.util import assert_valid_uuid


class ReceiptWordTag:
    """Represents a tag applied to a receipt word for DynamoDB.

    This class encapsulates tag-related information such as the associated image,
    receipt, line, and word identifiers, the tag itself, and the timestamp when the tag was added.
    It provides operations for generating DynamoDB keys for both the primary table and
    a secondary index.

    Attributes:
        image_id (str): UUID identifying the image.
        receipt_id (int): The receipt number.
        line_id (int): The line number of the word.
        word_id (int): The word number of the word.
        tag (str): The tag applied to the word.
        timestamp_added (str): The ISO formatted timestamp when the tag was added.
    """

    def __init__(
        self,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
        tag: str,
        timestamp_added: datetime,
    ):
        """Initializes a new ReceiptWordTag object for DynamoDB.

        Args:
            image_id (str): UUID identifying the image.
            receipt_id (int): The receipt number.
            line_id (int): The line number of the word.
            word_id (int): The word number of the word.
            tag (str): The tag to apply to the word. Must be non-empty, not exceed 40 characters,
                and must not start with an underscore.
            timestamp_added (datetime): The timestamp when the tag was added.

        Raises:
            ValueError: If any parameter is of an invalid type or has an invalid value.
        """
        assert_valid_uuid(image_id)
        self.image_id = image_id

        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if receipt_id < 0:
            raise ValueError("receipt_id must be positive")
        self.receipt_id = receipt_id

        if not isinstance(line_id, int):
            raise ValueError("line_id must be an integer")
        if line_id < 0:
            raise ValueError("line_id must be positive")
        self.line_id = line_id

        if not isinstance(word_id, int):
            raise ValueError("word_id must be an integer")
        if word_id < 0:
            raise ValueError("word_id must be positive")
        self.word_id = word_id

        if not tag:
            raise ValueError("tag must not be empty")
        if not isinstance(tag, str):
            raise ValueError("tag must be a string")
        if len(tag) > 40:
            raise ValueError("tag must not exceed 40 characters")
        if tag.startswith("_"):
            raise ValueError("tag must not start with an underscore")
        self.tag = tag.strip()

        if isinstance(timestamp_added, datetime):
            self.timestamp_added = timestamp_added.isoformat()
        elif isinstance(timestamp_added, str):
            self.timestamp_added = timestamp_added
        else:
            raise ValueError("timestamp_added must be a datetime object or a string")

    def __eq__(self, other: object) -> bool:
        """Checks equality between this ReceiptWordTag and another object.

        Args:
            other (object): The object to compare against.

        Returns:
            bool: True if the other object is a ReceiptWordTag with the same image, receipt, line, word identifiers
                  and tag; False otherwise.
        """
        if not isinstance(other, ReceiptWordTag):
            return False
        return (
            self.image_id == other.image_id
            and self.receipt_id == other.receipt_id
            and self.line_id == other.line_id
            and self.word_id == other.word_id
            and self.tag == other.tag
        )

    def __iter__(self) -> Generator[Tuple[str, str], None, None]:
        """Yields the attributes of the ReceiptWordTag as key-value pairs.

        Yields:
            Generator[Tuple[str, str], None, None]: Tuples containing attribute names and their corresponding values.
        """
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "line_id", self.line_id
        yield "word_id", self.word_id
        yield "tag", self.tag
        yield "timestamp_added", self.timestamp_added

    def __repr__(self) -> str:
        """Returns a string representation of the ReceiptWordTag.

        Returns:
            str: A developer-friendly string representation of the ReceiptWordTag.
        """
        return (
            f"ReceiptWordTag(image_id='{self.image_id}', "
            f"receipt_id={self.receipt_id}, "
            f"line_id={self.line_id}, "
            f"word_id={self.word_id}, "
            f"tag='{self.tag}')"
        )

    def key(self) -> dict:
        """Generates the primary key for the ReceiptWordTag.

        The primary key is constructed using the image_id, receipt_id, line_id, word_id, and tag.

        Returns:
            dict: A dictionary containing the primary key for the ReceiptWordTag.
        """
        tag_upper = self.tag
        spaced_tag_upper = f"{tag_upper:_>40}"
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}"
                    f"#LINE#{self.line_id:05d}"
                    f"#WORD#{self.word_id:05d}"
                    f"#TAG#{spaced_tag_upper}"
                )
            },
        }

    def gsi1_key(self) -> dict:
        """Generates the secondary index key for the ReceiptWordTag.

        This key is used to query tags in DynamoDB based on the tag attribute.

        Returns:
            dict: A dictionary containing the secondary index key for the ReceiptWordTag.
        """
        tag_upper = self.tag
        spaced_tag_upper = f"{tag_upper:_>40}"
        return {
            "GSI1PK": {"S": f"TAG#{spaced_tag_upper}"},
            "GSI1SK": {
                "S": (
                    f"IMAGE#{self.image_id}"
                    f"#RECEIPT#{self.receipt_id:05d}"
                    f"#LINE#{self.line_id:05d}"
                    f"#WORD#{self.word_id:05d}"
                )
            },
        }

    def to_item(self) -> dict:
        """Converts the ReceiptWordTag object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the ReceiptWordTag as a DynamoDB item.
        """
        return {
            **self.key(),
            **self.gsi1_key(),
            "TYPE": {"S": "RECEIPT_WORD_TAG"},
            "tag_name": {"S": self.tag},
            "timestamp_added": {"S": self.timestamp_added},
        }

    def to_ReceiptWord_key(self) -> dict:
        """Generates the key for the ReceiptWord table associated with this tag.

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


def itemToReceiptWordTag(item: dict) -> ReceiptWordTag:
    """Converts a DynamoDB item to a ReceiptWordTag object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptWordTag: The ReceiptWordTag object represented by the DynamoDB item.

    Raises:
        ValueError: If the item is missing required keys or has malformed fields.
    """
    required_keys = {"PK", "SK", "timestamp_added"}
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - set(item.keys())
        raise ValueError(f"Item is missing required keys: {missing_keys}")
    try:
        pk_parts = item["PK"]["S"].split("#")
        sk_parts = item["SK"]["S"].split("#")
        image_id = pk_parts[1]
        receipt_id = int(sk_parts[1])
        line_id = int(sk_parts[3])
        word_id = int(sk_parts[5])
        tag = sk_parts[7].lstrip("_").strip()
        timestamp_added = datetime.fromisoformat(item["timestamp_added"]["S"])
        return ReceiptWordTag(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            tag=tag,
            timestamp_added=timestamp_added,
        )
    except (IndexError, ValueError, KeyError) as e:
        raise ValueError(f"Error converting item to ReceiptWordTag: {e}")
