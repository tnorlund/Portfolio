"""Entity serialization for S3 data transfer.

Provides functions to serialize/deserialize receipt entities (ReceiptWord,
ReceiptWordLabel, ReceiptPlace) for JSON storage in S3.

Uses dataclasses.asdict() for serialization since all entities are dataclasses.
Deserialization parses datetime strings and passes kwargs to constructors.
"""

# pylint: disable=import-outside-toplevel
# Imports delayed to avoid circular dependencies with receipt_agent

from dataclasses import asdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from receipt_dynamo.entities import (
    ReceiptPlace,
    ReceiptWord,
    ReceiptWordLabel,
)

if TYPE_CHECKING:
    from evaluator_types import (
        SerializedLabel,
        SerializedPlace,
        SerializedWord,
    )


def serialize_word(word: ReceiptWord) -> "SerializedWord":
    """Serialize ReceiptWord for S3 storage using asdict."""
    data = asdict(word)
    # Convert embedding_status enum to string if present
    if data.get("embedding_status"):
        data["embedding_status"] = str(data["embedding_status"])
    return data  # type: ignore[return-value]


def deserialize_word(data: "SerializedWord") -> ReceiptWord:
    """Deserialize ReceiptWord from S3 data."""
    return ReceiptWord(**data)


def serialize_label(label: ReceiptWordLabel) -> "SerializedLabel":
    """Serialize ReceiptWordLabel for S3 storage using asdict."""
    data = asdict(label)
    # Convert timestamp_added to ISO string for JSON serialization
    if data.get("timestamp_added") and hasattr(
        data["timestamp_added"], "isoformat"
    ):
        data["timestamp_added"] = data["timestamp_added"].isoformat()
    return data  # type: ignore[return-value]


def deserialize_label(data: "SerializedLabel") -> ReceiptWordLabel:
    """Deserialize ReceiptWordLabel from S3 data."""
    # Make a mutable copy to avoid modifying the input
    label_data = dict(data)
    # Parse timestamp string back to datetime
    if isinstance(label_data.get("timestamp_added"), str):
        try:
            label_data["timestamp_added"] = datetime.fromisoformat(
                label_data["timestamp_added"]
            )
        except ValueError:
            label_data["timestamp_added"] = datetime.now(timezone.utc)
    return ReceiptWordLabel(**label_data)


def serialize_place(place: ReceiptPlace) -> "SerializedPlace":
    """Serialize ReceiptPlace for S3 storage using asdict."""
    data = asdict(place)
    # Convert datetime to ISO string
    if data.get("timestamp") and hasattr(data["timestamp"], "isoformat"):
        data["timestamp"] = data["timestamp"].isoformat()
    return data  # type: ignore[return-value]


def deserialize_place(data: "SerializedPlace | None") -> ReceiptPlace | None:
    """Deserialize ReceiptPlace from S3 data."""
    if not data:
        return None

    # Make a mutable copy to avoid modifying the input
    place_data = dict(data)
    # Parse timestamp string back to datetime
    if isinstance(place_data.get("timestamp"), str):
        try:
            ts = place_data["timestamp"]
            parsed = datetime.fromisoformat(ts)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            place_data["timestamp"] = parsed
        except ValueError:
            place_data["timestamp"] = datetime.now(timezone.utc)
    elif place_data.get("timestamp") is None:
        place_data["timestamp"] = datetime.now(timezone.utc)

    return ReceiptPlace(**place_data)


def serialize_words(words: list[ReceiptWord]) -> "list[SerializedWord]":
    """Serialize a list of ReceiptWord objects."""
    return [serialize_word(w) for w in words]


def deserialize_words(data: "list[SerializedWord]") -> list[ReceiptWord]:
    """Deserialize a list of ReceiptWord objects."""
    return [deserialize_word(d) for d in data]


def serialize_labels(
    labels: list[ReceiptWordLabel],
) -> "list[SerializedLabel]":
    """Serialize a list of ReceiptWordLabel objects."""
    return [serialize_label(label) for label in labels]


def deserialize_labels(
    data: "list[SerializedLabel]",
) -> list[ReceiptWordLabel]:
    """Deserialize a list of ReceiptWordLabel objects."""
    return [deserialize_label(d) for d in data]
