from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Generator, Optional, Tuple

from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid


@dataclass(eq=True, unsafe_hash=False)
class LabelHygieneResult:
    hygiene_id: str
    alias: str
    canonical_label: str
    reasoning: str
    gpt_agreed: bool
    source_batch_id: Optional[str]
    example_ids: list[str]
    timestamp: datetime
    image_id: str
    receipt_id: int

    def __post_init__(self) -> None:
        # Convert datetime to str if needed for timestamp
        if isinstance(self.timestamp, datetime):
            # Keep as datetime - no conversion needed for this field
            pass

        assert_valid_uuid(self.hygiene_id)

        if not isinstance(self.alias, str):
            raise ValueError("alias must be a string")

        if not isinstance(self.canonical_label, str):
            raise ValueError("canonical_label must be a string")

        if not isinstance(self.reasoning, str):
            raise ValueError("reasoning must be a string")

        if not isinstance(self.gpt_agreed, bool):
            raise ValueError("gpt_agreed must be a boolean")

        if self.source_batch_id is not None and not isinstance(
            self.source_batch_id, str
        ):
            raise ValueError("source_batch_id must be a string or None")

        if not isinstance(self.example_ids, list):
            raise ValueError("example_ids must be a list")

        if not isinstance(self.image_id, str):
            raise ValueError("image_id must be a string")

        if not isinstance(self.receipt_id, int):
            raise ValueError("receipt_id must be an integer")

        if not isinstance(self.timestamp, datetime):
            raise ValueError("timestamp must be a datetime object")

    @property
    def key(self) -> Dict[str, Any]:
        return {
            "PK": {"S": f"LABEL_HYGIENE#{self.hygiene_id}"},
            "SK": {"S": f"FROM#{self.alias}#TO#{self.canonical_label}"},
        }

    def gsi1_key(self) -> Dict[str, Any]:
        return {
            "GSI1PK": {"S": f"ALIAS#{self.alias}"},
            "GSI1SK": {"S": f"TO#{self.canonical_label}"},
        }

    def gsi2_key(self) -> Dict[str, Any]:
        return {
            "GSI2PK": {"S": f"CANONICAL_LABEL#{self.canonical_label}"},
            "GSI2SK": {"S": f"ALIAS#{self.alias}"},
        }

    def to_item(self) -> Dict[str, Any]:
        return {
            **self.key,
            **self.gsi1_key(),
            **self.gsi2_key(),
            "TYPE": {"S": "LABEL_HYGIENE_RESULT"},
            "alias": {"S": self.alias},
            "canonical_label": {"S": self.canonical_label},
            "reasoning": {"S": self.reasoning},
            "gpt_agreed": {"BOOL": self.gpt_agreed},
            "source_batch_id": {"S": self.source_batch_id or ""},
            "example_ids": {"SS": self.example_ids},
            "image_id": {
                "S": self.image_id
            },  # Include image_id in serialization
            "receipt_id": {
                "N": self.receipt_id
            },  # Include receipt_id in serialization
            "timestamp": {"S": self.timestamp.isoformat()},
        }

    def __repr__(self) -> str:
        return (
            "LabelHygieneResult("
            f"hygiene_id={_repr_str(self.hygiene_id)}, "
            f"alias={_repr_str(self.alias)}, "
            f"canonical_label={_repr_str(self.canonical_label)}, "
            f"reasoning={_repr_str(self.reasoning)}, "
            f"gpt_agreed={_repr_str(self.gpt_agreed)}, "
            f"source_batch_id={_repr_str(self.source_batch_id)}, "
            f"example_ids={_repr_str(self.example_ids)}, "
            f"image_id={_repr_str(self.image_id)}, "  # Include image_id
            f"receipt_id={self.receipt_id}, "  # Include receipt_id in repr
            f"timestamp={_repr_str(self.timestamp)}"
            ")"
        )

    def __str__(self) -> str:
        return self.__repr__()

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        yield "hygiene_id", self.hygiene_id
        yield "alias", self.alias
        yield "canonical_label", self.canonical_label
        yield "reasoning", self.reasoning
        yield "gpt_agreed", self.gpt_agreed
        yield "source_batch_id", self.source_batch_id
        yield "example_ids", self.example_ids
        yield "image_id", self.image_id  # Include image_id in iteration
        yield "receipt_id", self.receipt_id  # Include receipt_id in iteration
        yield "timestamp", self.timestamp

    def __hash__(self) -> int:
        return hash(
            (
                self.hygiene_id,
                self.alias,
                self.canonical_label,
                self.reasoning,
                self.gpt_agreed,
                self.source_batch_id,
                tuple(self.example_ids),
                self.image_id,  # Include image_id in hash
                self.receipt_id,  # Include receipt_id in hash
                self.timestamp,
            )
        )


def item_to_label_hygiene_result(item: Dict[str, Any]) -> LabelHygieneResult:
    """
    Converts an item from DynamoDB to a LabelHygieneResult object.
    """
    required_keys = {
        "PK",
        "SK",
        "TYPE",
        "alias",
        "canonical_label",
        "reasoning",
        "gpt_agreed",
        "source_batch_id",
        "example_ids",
        "image_id",  # Added image_id to required keys
        "receipt_id",  # Added receipt_id to required keys
        "timestamp",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}\n"
            f"additional keys: {additional_keys}"
        )
    try:
        hygiene_id = item["PK"]["S"].split("#")[1]
        alias = item["alias"]["S"]
        canonical_label = item["canonical_label"]["S"]
        reasoning = item["reasoning"]["S"]
        gpt_agreed = item["gpt_agreed"]["BOOL"]
        source_batch_id = item["source_batch_id"]["S"]
        example_ids = item["example_ids"]["SS"]
        image_id = item["image_id"]["S"]
        receipt_id = int(item["receipt_id"]["N"])
        timestamp = datetime.fromisoformat(item["timestamp"]["S"])
        return LabelHygieneResult(
            hygiene_id,
            alias,
            canonical_label,
            reasoning,
            gpt_agreed,
            source_batch_id,
            example_ids,
            timestamp,
            image_id,  # Pass image_id to constructor
            receipt_id,  # Pass receipt_id to constructor
        )
    except Exception as e:
        raise ValueError(
            f"Error converting item to LabelHygieneResult: {e}"
        ) from e
