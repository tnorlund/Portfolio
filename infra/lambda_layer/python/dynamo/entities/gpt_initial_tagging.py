from typing import Generator, Tuple
from datetime import datetime
from dynamo.entities.util import assert_valid_uuid, _repr_str


class GPTInitialTagging:
    """Represents a single ChatGPT initial tagging query for a receipt in DynamoDB.

    By design, only one record is stored per (image_id, receipt_id).
    This class stores:
      - image_id
      - receipt_id
      - query (the ChatGPT prompt)
      - response (the ChatGPT response)
      - timestamp_added
    """

    def __init__(
        self,
        image_id: str,
        receipt_id: int,
        query: str,
        response: str,
        timestamp_added: datetime,
    ):
        """Initializes a new GPTInitialTagging object.

        Args:
            image_id (str): UUID identifying the image.
            receipt_id (int): The receipt number.
            query (str): The ChatGPT prompt for initial tagging.
            response (str): The ChatGPT response text.
            timestamp_added (datetime or str): When the query was performed.

        Raises:
            ValueError: If any parameter is invalid.
        """
        # Validate and assign the image identifier.
        assert_valid_uuid(image_id)
        self.image_id = image_id

        # Validate receipt_id.
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if receipt_id < 0:
            raise ValueError("receipt_id must be positive")
        self.receipt_id = receipt_id

        # Validate the query and response.
        if not query or not isinstance(query, str):
            raise ValueError("query must be a non-empty string")
        if not response or not isinstance(response, str):
            raise ValueError("response must be a non-empty string")
        self.query = query.strip()
        self.response = response.strip()

        # Validate timestamp.
        if isinstance(timestamp_added, datetime):
            self.timestamp_added = timestamp_added.isoformat()
        elif isinstance(timestamp_added, str):
            self.timestamp_added = timestamp_added
        else:
            raise ValueError("timestamp_added must be a datetime object or a string")

    def __eq__(self, other: object) -> bool:
        """Checks equality based on image_id, receipt_id, query, response, and timestamp."""
        if not isinstance(other, GPTInitialTagging):
            return False
        return (
            self.image_id == other.image_id
            and self.receipt_id == other.receipt_id
            and self.query == other.query
            and self.response == other.response
            and self.timestamp_added == other.timestamp_added
        )

    def __iter__(self) -> Generator[Tuple[str, str], None, None]:
        """Yields the attributes as key-value pairs."""
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "query", self.query
        yield "response", self.response
        yield "timestamp_added", self.timestamp_added

    def __repr__(self) -> str:
        """Returns a developer-friendly string representation of the GPTInitialTagging."""
        return (
            "GPTInitialTagging("
            f"image_id={_repr_str(self.image_id)}, "
            f"receipt_id={self.receipt_id}, "
            f"query={_repr_str(self.query)}, "
            f"response={_repr_str(self.response)}, "
            f"timestamp_added={_repr_str(self.timestamp_added)}"
            ")"
        )

    def key(self) -> dict:
        """
        Generates the primary key for the GPTInitialTagging record.

        By design, only ONE record can exist per (image_id, receipt_id).
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"RECEIPT#{self.receipt_id:05d}#QUERY#INITIAL_TAGGING"}
        }

    def to_item(self) -> dict:
        """Converts the GPTInitialTagging object to a DynamoDB item."""
        return {
            **self.key(),
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
        GPTInitialTagging: The GPTInitialTagging object represented by the item.

    Raises:
        ValueError: If the item is missing required keys or has malformed fields.
    """
    required_keys = {"PK", "SK", "query", "response", "timestamp_added"}
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - set(item.keys())
        raise ValueError(f"Item is missing required keys: {missing_keys}")

    try:
        pk_parts = item["PK"]["S"].split("#")  # e.g. IMAGE#<image_id>
        sk_parts = item["SK"]["S"].split("#")  # e.g. RECEIPT#<receipt_id>#QUERY#INITIAL_TAGGING

        image_id = pk_parts[1]
        receipt_id = int(sk_parts[1])

        query = item["query"]["S"]
        response = item["response"]["S"]
        timestamp_added = datetime.fromisoformat(item["timestamp_added"]["S"])

        return GPTInitialTagging(
            image_id=image_id,
            receipt_id=receipt_id,
            query=query,
            response=response,
            timestamp_added=timestamp_added,
        )
    except (IndexError, ValueError, KeyError) as e:
        raise ValueError(f"Error converting item to GPTInitialTagging: {e}")