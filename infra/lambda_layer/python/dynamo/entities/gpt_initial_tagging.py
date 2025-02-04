from typing import Generator, Tuple
from datetime import datetime
from dynamo.entities.util import assert_valid_uuid, _repr_str


class GPTInitialTagging:
    """Represents a ChatGPT initial tagging query applied to a receipt word for DynamoDB.

    This class encapsulates details such as the associated image, receipt, line, and word
    identifiers, the tag suggested by ChatGPT, the ChatGPT query and response, and the timestamp
    when the initial tagging query was performed.

    Attributes:
        image_id (str): UUID identifying the image.
        receipt_id (int): The receipt number.
        line_id (int): The line number containing the word.
        word_id (int): The word number.
        tag (str): The tag suggested by ChatGPT.
        query (str): The ChatGPT prompt for initial tagging.
        response (str): The ChatGPT response.
        timestamp_added (str): The ISO formatted timestamp when the query was performed.
    """

    def __init__(
        self,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
        tag: str,
        query: str,
        response: str,
        timestamp_added: datetime,
    ):
        """Initializes a new GPTInitialTagging object for DynamoDB.

        Args:
            image_id (str): UUID identifying the image.
            receipt_id (int): The receipt number.
            line_id (int): The line number containing the word.
            word_id (int): The word number.
            tag (str): The tag suggested by ChatGPT. Must be non-empty, not exceed 40 characters,
                and must not start with an underscore.
            query (str): The ChatGPT prompt for initial tagging.
            response (str): The ChatGPT response.
            timestamp_added (datetime): The timestamp when the query was performed.

        Raises:
            ValueError: If any parameter is of an invalid type or has an invalid value.
        """
        # Validate and assign the image identifier.
        assert_valid_uuid(image_id)
        self.image_id = image_id

        # Validate numeric identifiers.
        for name, value in (("receipt_id", receipt_id), ("line_id", line_id), ("word_id", word_id)):
            if not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} must be positive")
        self.receipt_id = receipt_id
        self.line_id = line_id
        self.word_id = word_id

        # Validate the tag.
        if not tag:
            raise ValueError("tag must not be empty")
        if not isinstance(tag, str):
            raise ValueError("tag must be a string")
        if len(tag) > 40:
            raise ValueError("tag must not exceed 40 characters")
        if tag.startswith("_"):
            raise ValueError("tag must not start with an underscore")
        self.tag = tag.strip()

        # Validate the query and response.
        if not query or not isinstance(query, str):
            raise ValueError("query must be a non-empty string")
        if not response or not isinstance(response, str):
            raise ValueError("response must be a non-empty string")
        self.query = query.strip()
        self.response = response.strip()

        # Validate and assign the timestamp.
        if isinstance(timestamp_added, datetime):
            self.timestamp_added = timestamp_added.isoformat()
        elif isinstance(timestamp_added, str):
            self.timestamp_added = timestamp_added
        else:
            raise ValueError("timestamp_added must be a datetime object or a string")

    def __eq__(self, other: object) -> bool:
        """Checks equality between this GPTInitialTagging and another object.

        Args:
            other (object): The object to compare against.

        Returns:
            bool: True if the other object is a GPTInitialTagging with the same identifiers,
                  tag, query, and response; False otherwise.
        """
        if not isinstance(other, GPTInitialTagging):
            return False
        return (
            self.image_id == other.image_id
            and self.receipt_id == other.receipt_id
            and self.line_id == other.line_id
            and self.word_id == other.word_id
            and self.tag == other.tag
            and self.query == other.query
            and self.response == other.response
            and self.timestamp_added == other.timestamp_added
        )

    def __iter__(self) -> Generator[Tuple[str, str], None, None]:
        """Yields the attributes of the GPTInitialTagging as key-value pairs.

        Yields:
            Generator[Tuple[str, str], None, None]: Tuples containing attribute names and their corresponding values.
        """
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "line_id", self.line_id
        yield "word_id", self.word_id
        yield "tag", self.tag
        yield "query", self.query
        yield "response", self.response
        yield "timestamp_added", self.timestamp_added

    def __repr__(self) -> str:
        """Returns a string representation of the GPTInitialTagging.

        Returns:
            str: A developer-friendly string representation of the GPTInitialTagging.
        """
        return (
            "GPTInitialTagging("
            f"image_id={_repr_str(self.image_id)}, "
            f"receipt_id={self.receipt_id}, "
            f"line_id={self.line_id}, "
            f"word_id={self.word_id}, "
            f"tag={_repr_str(self.tag)}, "
            f"query={_repr_str(self.query)}, "
            f"response={_repr_str(self.response)}, "
            f"timestamp_added={_repr_str(self.timestamp_added)}"
            ")"
        )

    def key(self) -> dict:
        """Generates the primary key for the GPTInitialTagging.

        The primary key is constructed using the image_id, receipt_id, line_id, word_id, tag,
        and a suffix that indicates this is an initial tagging query.

        Returns:
            dict: A dictionary containing the primary key for the GPTInitialTagging.
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
                    f"#QUERY#INITIAL_TAGGING"
                )
            },
        }

    def gsi1_key(self) -> dict:
        """Generates the secondary index key for the GPTInitialTagging.

        This key is used to query initial tagging queries in DynamoDB based on the tag attribute.

        Returns:
            dict: A dictionary containing the secondary index key for the GPTInitialTagging.
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
        """Converts the GPTInitialTagging object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the GPTInitialTagging as a DynamoDB item.
        """
        return {
            **self.key(),
            **self.gsi1_key(),
            "TYPE": {"S": "GPT_INITIAL_TAGGING"},
            "query": {"S": self.query},
            "response": {"S": self.response},
            "timestamp_added": {"S": self.timestamp_added},
        }


def itemToGPTInitialTagging(item: dict) -> GPTInitialTagging:
    """Converts a DynamoDB item to a GPTInitialTagging object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        GPTInitialTagging: The GPTInitialTagging object represented by the DynamoDB item.

    Raises:
        ValueError: If the item is missing required keys or has malformed fields.
    """
    required_keys = {"PK", "SK", "query", "response", "timestamp_added"}
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - set(item.keys())
        raise ValueError(f"Item is missing required keys: {missing_keys}")
    try:
        # Extract image_id from the partition key.
        pk_parts = item["PK"]["S"].split("#")
        # Extract receipt, line, word, and tag details from the sort key.
        sk_parts = item["SK"]["S"].split("#")
        image_id = pk_parts[1]
        receipt_id = int(sk_parts[1])
        line_id = int(sk_parts[3])
        word_id = int(sk_parts[5])
        # The tag is stored in the sort key with fixed-width formatting.
        tag = sk_parts[7].lstrip("_").strip()
        query = item["query"]["S"]
        response = item["response"]["S"]
        timestamp_added = datetime.fromisoformat(item["timestamp_added"]["S"])
        return GPTInitialTagging(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            tag=tag,
            query=query,
            response=response,
            timestamp_added=timestamp_added,
        )
    except (IndexError, ValueError, KeyError) as e:
        raise ValueError(f"Error converting item to GPTInitialTagging: {e}")