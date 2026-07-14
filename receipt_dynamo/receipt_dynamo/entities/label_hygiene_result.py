from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generator

from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid


@dataclass(eq=True, unsafe_hash=False)
class LabelHygieneResult:
    REQUIRED_KEYS = {
        "PK",
        "SK",
        "TYPE",
        "alias",
        "canonical_label",
        "reasoning",
        "gpt_agreed",
        "source_batch_id",
        "example_ids",
        "image_id",
        "receipt_id",
        "timestamp",
    }

    hygiene_id: str
    alias: str
    canonical_label: str
    reasoning: str
    gpt_agreed: bool
    source_batch_id: str | None
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
        if not self.alias or "#" in self.alias:
            raise ValueError("alias must be non-empty and exclude '#'")

        if not isinstance(self.canonical_label, str):
            raise ValueError("canonical_label must be a string")
        if not self.canonical_label or "#" in self.canonical_label:
            raise ValueError(
                "canonical_label must be non-empty and exclude '#'"
            )

        if not isinstance(self.reasoning, str):
            raise ValueError("reasoning must be a string")

        if not isinstance(self.gpt_agreed, bool):
            raise ValueError("gpt_agreed must be a boolean")

        if self.source_batch_id is not None and not isinstance(
            self.source_batch_id, str
        ):
            raise ValueError("source_batch_id must be a string or None")
        if self.source_batch_id == "":
            raise ValueError("source_batch_id must be non-empty or None")

        if not isinstance(self.example_ids, list) or not all(
            isinstance(example_id, str) and example_id
            for example_id in self.example_ids
        ):
            raise ValueError("example_ids must be a list of non-empty strings")
        if len(set(self.example_ids)) != len(self.example_ids):
            raise ValueError("example_ids must not contain duplicates")
        self.example_ids = deepcopy(self.example_ids)

        assert_valid_uuid(self.image_id)

        if isinstance(self.receipt_id, bool) or not isinstance(
            self.receipt_id, int
        ):
            raise ValueError("receipt_id must be an integer")
        if self.receipt_id <= 0:
            raise ValueError("receipt_id must be greater than zero")

        if not isinstance(self.timestamp, datetime):
            raise ValueError("timestamp must be a datetime object")

    @property
    def key(self) -> dict[str, Any]:
        return {
            "PK": {"S": f"LABEL_HYGIENE#{self.hygiene_id}"},
            "SK": {"S": f"FROM#{self.alias}#TO#{self.canonical_label}"},
        }

    def gsi1_key(self) -> dict[str, Any]:
        return {
            "GSI1PK": {"S": f"ALIAS#{self.alias}"},
            "GSI1SK": {"S": f"TO#{self.canonical_label}"},
        }

    def gsi2_key(self) -> dict[str, Any]:
        return {
            "GSI2PK": {"S": f"CANONICAL_LABEL#{self.canonical_label}"},
            "GSI2SK": {"S": f"ALIAS#{self.alias}"},
        }

    def to_item(self) -> dict[str, Any]:
        self.__post_init__()
        return {
            **self.key,
            **self.gsi1_key(),
            **self.gsi2_key(),
            "TYPE": {"S": "LABEL_HYGIENE_RESULT"},
            "alias": {"S": self.alias},
            "canonical_label": {"S": self.canonical_label},
            "reasoning": {"S": self.reasoning},
            "gpt_agreed": {"BOOL": self.gpt_agreed},
            "source_batch_id": (
                {"S": self.source_batch_id}
                if self.source_batch_id is not None
                else {"NULL": True}
            ),
            "example_ids": (
                {"SS": self.example_ids} if self.example_ids else {"L": []}
            ),
            "image_id": {
                "S": self.image_id
            },  # Include image_id in serialization
            "receipt_id": {"N": str(self.receipt_id)},
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

    def __iter__(self) -> Generator[tuple[str, Any], None, None]:
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

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "LabelHygieneResult":
        """Converts a DynamoDB item to a LabelHygieneResult object.

        Args:
            item: The DynamoDB item to convert.

        Returns:
            LabelHygieneResult: The LabelHygieneResult object.

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
            hygiene_id = item["PK"]["S"].split("#")[1]
            alias = item["alias"]["S"]
            canonical_label = item["canonical_label"]["S"]
            reasoning = item["reasoning"]["S"]
            gpt_agreed = item["gpt_agreed"]["BOOL"]
            source_field = item["source_batch_id"]
            source_batch_id = source_field.get("S") or None
            example_field = item["example_ids"]
            if "SS" in example_field:
                example_ids = list(example_field["SS"])
            elif example_field == {"L": []}:
                example_ids = []
            else:
                raise ValueError("example_ids must be SS or an empty L")
            image_id = item["image_id"]["S"]
            receipt_id = int(item["receipt_id"]["N"])
            timestamp = datetime.fromisoformat(item["timestamp"]["S"])
            result = cls(
                hygiene_id,
                alias,
                canonical_label,
                reasoning,
                gpt_agreed,
                source_batch_id,
                example_ids,
                timestamp,
                image_id,
                receipt_id,
            )
            expected_keys = {
                **result.key,
                **result.gsi1_key(),
                **result.gsi2_key(),
                "TYPE": {"S": "LABEL_HYGIENE_RESULT"},
            }
            if any(
                item.get(key) != value for key, value in expected_keys.items()
            ):
                raise ValueError("Invalid LabelHygieneResult keys")
            return result
        except Exception as e:
            raise ValueError(
                f"Error converting item to LabelHygieneResult: {e}"
            ) from e


def item_to_label_hygiene_result(item: dict[str, Any]) -> LabelHygieneResult:
    """Converts a DynamoDB item to a LabelHygieneResult object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        LabelHygieneResult: The LabelHygieneResult object.

    Raises:
        ValueError: When the item format is invalid.
    """
    return LabelHygieneResult.from_item(item)
