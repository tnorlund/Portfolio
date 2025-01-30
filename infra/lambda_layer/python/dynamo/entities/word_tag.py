from typing import Generator, Tuple
from datetime import datetime
from dynamo.entities.util import assert_valid_uuid

class WordTag:
    def __init__(
        self,
        image_id: str,
        line_id: int,
        word_id: int,
        tag: str,
        timestamp_added: datetime
    ):
        """Constructs a new WordTag object for DynamoDB

        Args:
            image_id (str): UUID identifying the image
            line_id (int): The line number of the word
            word_id (int): The word number of the word
            tag (str): The tag to apply to the word
            timestamp_added (datetime): The timestamp the tag was added
        
        Attributes:
            image_id (str): UUID identifying the image
            line_id (int): The line number of the word
            word_id (int): The word number of the word
            tag (str): The tag to apply to the word
            timestamp_added (datetime): The timestamp the tag was added
        """
        assert_valid_uuid(image_id)
        self.image_id = image_id
        self.line_id = line_id
        self.word_id = word_id
        if not tag:
            raise ValueError("tag must not be empty")
        if len(tag) > 40:
            raise ValueError("tag must not exceed 40 characters")
        if tag.startswith("_"):
            raise ValueError("tag must not start with an underscore")
        self.tag = tag.strip()
        if isinstance(timestamp_added, datetime):
            self.timestamp_added = timestamp_added.isoformat()
        elif isinstance(timestamp_added, str):
            self.timestamp_added = timestamp_added

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, WordTag):
            return False
        return (
            self.word_id == other.word_id
            and self.tag == other.tag
            and self.line_id == other.line_id
            and self.image_id == other.image_id
        )

    def __iter__(self) -> Generator[Tuple[str, str], None, None]:
        yield "image_id", self.image_id
        yield "line_id", self.line_id
        yield "word_id", self.word_id
        yield "tag", self.tag
        yield "timestamp_added", self.timestamp_added

    def __repr__(self) -> str:
        return f"WordTag(image_id='{self.image_id}', line_id={self.line_id} word_id={self.word_id}, tag='{self.tag}')"

    def key(self) -> dict:
        tag_upper = self.tag
        spaced_tag_upper = f"{tag_upper:_>40}"
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": f"LINE#{self.line_id:05d}"
                f"#WORD#{self.word_id:05d}"
                f"#TAG#{spaced_tag_upper}"
            },
        }

    def gsi1_key(self) -> dict:
        tag_upper = self.tag
        spaced_tag_upper = f"{tag_upper:_>40}"
        return {
            "GSI1PK": {"S": f"TAG#{spaced_tag_upper}"},
            "GSI1SK": {
                "S": f"IMAGE#{self.image_id}#LINE#{self.line_id:05d}#WORD#{self.word_id:05d}"
            },
        }

    def to_item(self) -> dict:
        return {
            **self.key(),
            **self.gsi1_key(),
            "TYPE": {"S": "WORD_TAG"},
            "tag_name": {"S": self.tag},
            "timestamp_added": {"S": self.timestamp_added},
        }
    
    def to_Word_key(self) -> dict:
        """Returns the key for the Word table"""
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": f"LINE#{self.line_id:05d}"
                f"#WORD#{self.word_id:05d}"
            }
        }


def itemToWordTag(item: dict) -> WordTag:
    required_keys = {"PK", "SK"}
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        raise ValueError(f"Item is missing required keys: {missing_keys}")
    try:
        pk_parts = item["PK"]["S"].split("#")
        sk_parts = item["SK"]["S"].split("#")

        tag = sk_parts[-1].lstrip("_").strip()
        return WordTag(
            image_id=pk_parts[1],
            line_id=int(sk_parts[1]),
            word_id=int(sk_parts[3]),
            tag=tag,
            timestamp_added=datetime.fromisoformat(item["timestamp_added"]["S"]),
        )
    except (IndexError, ValueError) as e:
        raise ValueError(f"Item is missing required values: {e}") from e
