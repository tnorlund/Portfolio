from typing import Generator, Tuple
from datetime import datetime


class WordTag:
    def __init__(
        self,
        image_id: int,
        word_id: int,
        tag: str,
    ):
        """Constructs a new WordTag object for DynamoDB"""
        self.image_id = image_id
        self.word_id = word_id
        self.tag = tag.strip()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, WordTag):
            return False
        return self.word_id == other.word_id and self.tag == other.tag

    def __iter__(self) -> Generator[Tuple[str, str], None, None]:
        yield "word_id", self.word_id
        yield "tag", self.tag

    def __repr__(self) -> str:
        return f"WordTag(word_id={self.word_id}, tag={self.tag})"

    def key(self) -> dict:
        tag_upper = self.tag.upper()
        spaced_tag_upper = f"{tag_upper:_>20}"
        return {
            "PK": {"S": f"IMAGE#{self.image_id:05d}"},
            "SK": {"S": f"TAG#{spaced_tag_upper}#WORD#{self.word_id:05d}"},
        }

    def gsi1_key(self) -> dict:
        tag_upper = self.tag.upper()
        spaced_tag_upper = f"{tag_upper:_>20}"
        return {
            "GSI1PK": {"S": f"TAG#{spaced_tag_upper}"},
            "GSI1SK": {"S": f"WORD#{self.word_id:05d}"},
        }

    def to_item(self) -> dict:
        return {
            **self.key(),
            **self.gsi1_key(),
            "TYPE": {"S": "WORD_TAG"},
        }


def WordTagFromItem(item: dict) -> WordTag:
    required_keys = {"PK", "SK"}
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        raise ValueError(f"Item is missing required keys: {missing_keys}")
    try:
        pk_parts = item["PK"]["S"].split("#")
        sk_parts = item["SK"]["S"].split("#")
        return WordTag(
            image_id=int(pk_parts[1]),
            word_id=int(sk_parts[3]),
            tag=sk_parts[1].replace("_", "").strip(),
        )
    except (IndexError, ValueError) as e:
        raise ValueError(f"Item is missing required values: {e}") from e
