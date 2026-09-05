"""Flat DynamoDB items with validated keys and computed index attributes."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

SERIALIZER = TypeSerializer()
DESERIALIZER = TypeDeserializer()
KINDS = {"AREA", "ITEM", "ROUTINE", "WEEK", "PROPOSAL", "CONFIG"}


def plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral() else float(value)
    if isinstance(value, list):
        return [plain(x) for x in value]
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    return value


@dataclass(eq=True, unsafe_hash=False)
class PlannerRecord:
    """A planner entity; service validation owns its domain-specific fields."""

    record_type: str
    record_id: str
    fields: dict[str, Any]

    REQUIRED_KEYS = {"PK", "SK", "TYPE"}

    def __post_init__(self) -> None:
        if self.record_type not in KINDS:
            raise ValueError("Unknown planner record type")
        if (
            not isinstance(self.record_id, str)
            or not self.record_id
            or "#" in self.record_id
        ):
            raise ValueError("A record id must be nonempty and contain no #")
        if (
            not isinstance(self.fields, dict)
            or self.REQUIRED_KEYS & self.fields.keys()
        ):
            raise ValueError("Fields must not override DynamoDB keys")

    @property
    def key(self) -> dict:
        return {
            "PK": {"S": "PLANNER"},
            "SK": {"S": f"{self.record_type}#{self.record_id}"},
        }

    def to_item(self) -> dict:
        return {
            **self.key,
            "TYPE": {"S": self.record_type},
            **{
                key: SERIALIZER.serialize(value)
                for key, value in self.fields.items()
            },
        }

    @classmethod
    def from_item(cls, item: dict) -> "PlannerRecord":
        if not cls.REQUIRED_KEYS <= item.keys():
            raise ValueError("Missing required DynamoDB keys")
        if item["PK"] != {"S": "PLANNER"}:
            raise ValueError("Not a planner record")
        kind, identity = item["SK"]["S"].split("#", 1)
        if item["TYPE"] != {"S": kind}:
            raise ValueError("Record type disagrees with sort key")
        fields = {
            key: plain(DESERIALIZER.deserialize(value))
            for key, value in item.items()
            if key not in cls.REQUIRED_KEYS
        }
        return cls(kind, identity, fields)


def item_to_planner_record(item: dict) -> PlannerRecord:
    return PlannerRecord.from_item(item)
