from datetime import datetime
from typing import Generator, Tuple

from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid


class GPTValidation:
    """Represents a ChatGPT validation query for a receipt in DynamoDB.

    This class encapsulates details such as the associated image, receipt, line, and word
    identifiers, the tag under validation, the ChatGPT query and response, and the timestamp
    when the validation occurred.

    Attributes:
        image_id (str): UUID identifying the image.
        receipt_id (int): The receipt number.
        line_id (int): The line number containing the word.
        word_id (int): The word number.
        tag (str): The tag applied to the word.
        query (str): The ChatGPT query prompt used for validation.
        response (str): The ChatGPT response received.
        timestamp_added (str): The ISO formatted timestamp when the validation occurred.
    """

    def __init__(self,
        image_id: str,
        receipt_id: int,
        query: str,
        response: str,
        timestamp_added: datetime,):
        """Initializes a new GPTValidation object.

        Args:
            image_id (str): UUID identifying the image.
            receipt_id (int): The receipt number.
            line_id (int): The line number containing the word.
            word_id (int): The word number.
            tag (str): The tag under validation. Must be non-empty, not exceed 40 characters,
                and must not start with an underscore.
            query (str): The ChatGPT prompt for validation.
            response (str): The ChatGPT response text.
            timestamp_added (datetime or str): When the validation was performed.

        Raises:
            ValueError: If any parameter is invalid.
        """
        # Validate and assign the image identifier.
        assert_valid_uuid(image_id)
        self.image_id = image_id

        # Validate receipt_id.
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if receipt_id <= 0:
            raise ValueError("receipt_id must be positive")
        self.receipt_id = receipt_id

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
        """Checks equality between this GPTValidation and another object.

        Args:
            other (object): The object to compare against.

        Returns:
            bool: True if the other object is a GPTValidation with the same identifiers,
                  tag, query, and response; False otherwise.
        """
        if not isinstance(other, GPTValidation):
            return False
        return (self.image_id == other.image_id and
            self.receipt_id == other.receipt_id and
            self.query == other.query and
            self.response == other.response and
            self.timestamp_added == other.timestamp_added)

    def __iter__(self) -> Generator[Tuple[str, str], None, None]:
        """Yields the attributes of the GPTValidation as key-value pairs.

        Yields:
            Generator[Tuple[str, str], None, None]: Tuples containing attribute names and their corresponding values.
        """
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "query", self.query
        yield "response", self.response
        yield "timestamp_added", self.timestamp_added

    def __repr__(self) -> str:
        """Returns a string representation of the GPTValidation.

        Returns:
            str: A developer-friendly string representation of the GPTValidation.
        """
        return ("GPTValidation("
            f"image_id={_repr_str(self.image_id)}, "
            f"receipt_id={self.receipt_id}, "
            f"query={_repr_str(self.query)}, "
            f"response={_repr_str(self.response)}, "
            f"timestamp_added={_repr_str(self.timestamp_added)}"
            ")")

    def key(self) -> dict:
        """Generates the primary key for the GPTValidation.

        The primary key is constructed using the image_id, receipt_id, line_id, word_id, tag,
        and a suffix that indicates this is a validation query.

        Returns:
            dict: A dictionary containing the primary key for the GPTValidation.
        """
        # Use a fixed-width formatting for the tag as in receipt_word_tag.py.
        return {"PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"RECEIPT#{self.receipt_id:05d}#QUERY#VALIDATION"},}

    def to_item(self) -> dict:
        """Converts the GPTValidation object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the GPTValidation as a DynamoDB item.
        """
        return {**self.key(),
            "TYPE": {"S": "GPT_VALIDATION"},
            "query": {"S": self.query},
            "response": {"S": self.response},
            "timestamp_added": {"S": self.timestamp_added},}

    def __hash__(self) -> int:
        """Returns the hash value of the GPTValidation object.

        Returns:
            int: The hash value of the GPTValidation object.
        """
        return hash((self.image_id,
                self.receipt_id,
                self.query,
                self.response,
                self.timestamp_added,))


def itemToGPTValidation(item: dict) -> GPTValidation:
    """Converts a DynamoDB item to a GPTValidation object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        GPTValidation: The GPTValidation object represented by the DynamoDB item.

    Raises:
        ValueError: If the item is missing required keys or has malformed fields.
    """
    required_keys = {"PK", "SK", "query", "response", "timestamp_added"}
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - set(item.keys())
        raise ValueError(f"Item is missing required keys: {missing_keys}")
    try:
        # Split the partition key to extract image_id.
        pk_parts = item["PK"]["S"].split("#")
        sk_parts = item["SK"]["S"].split("#")
        image_id = pk_parts[1]
        receipt_id = int(sk_parts[1])
        query = item["query"]["S"]
        response = item["response"]["S"]
        timestamp_added = datetime.fromisoformat(item["timestamp_added"]["S"])
        return GPTValidation(image_id=image_id,
            receipt_id=receipt_id,
            query=query,
            response=response,
            timestamp_added=timestamp_added,)
    except (IndexError, ValueError, KeyError) as e:
        raise ValueError(f"Error converting item to GPTValidation: {e}")
