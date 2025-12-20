"""Entity serialization for S3 data transfer.

Provides functions to serialize/deserialize receipt entities (ReceiptWord,
ReceiptWordLabel, ReceiptPlace) for JSON storage in S3.

Uses dataclasses.asdict() for serialization since all entities are dataclasses.
Deserialization parses datetime strings and passes kwargs to constructors.
"""

from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from receipt_dynamo.entities import (
    ReceiptPlace,
    ReceiptWord,
    ReceiptWordLabel,
)


def serialize_word(word: ReceiptWord) -> Dict[str, Any]:
    """Serialize ReceiptWord for S3 storage using asdict."""
    data = asdict(word)
    # Convert embedding_status enum to string if present
    if data.get("embedding_status"):
        data["embedding_status"] = str(data["embedding_status"])
    return data


def deserialize_word(data: Dict[str, Any]) -> ReceiptWord:
    """Deserialize ReceiptWord from S3 data."""
    return ReceiptWord(**data)


def serialize_label(label: ReceiptWordLabel) -> Dict[str, Any]:
    """Serialize ReceiptWordLabel for S3 storage using asdict."""
    data = asdict(label)
    # timestamp_added is already converted to string by asdict
    return data


def deserialize_label(data: Dict[str, Any]) -> ReceiptWordLabel:
    """Deserialize ReceiptWordLabel from S3 data."""
    # Parse timestamp string back to datetime
    if isinstance(data.get("timestamp_added"), str):
        try:
            data["timestamp_added"] = datetime.fromisoformat(
                data["timestamp_added"]
            )
        except ValueError:
            data["timestamp_added"] = None
    return ReceiptWordLabel(**data)


def serialize_place(place: ReceiptPlace) -> Dict[str, Any]:
    """Serialize ReceiptPlace for S3 storage using asdict."""
    data = asdict(place)
    # Convert datetime to ISO string
    if data.get("timestamp") and hasattr(data["timestamp"], "isoformat"):
        data["timestamp"] = data["timestamp"].isoformat()
    return data


def deserialize_place(data: Dict[str, Any]) -> Optional[ReceiptPlace]:
    """Deserialize ReceiptPlace from S3 data."""
    if not data:
        return None

    # Parse timestamp string back to datetime
    if isinstance(data.get("timestamp"), str):
        try:
            # Handle both with and without timezone
            ts = data["timestamp"]
            if ts.endswith("+00:00"):
                ts = ts.replace("+00:00", "")
            data["timestamp"] = datetime.fromisoformat(ts)
        except ValueError:
            data["timestamp"] = datetime.now()
    elif data.get("timestamp") is None:
        data["timestamp"] = datetime.now()

    return ReceiptPlace(**data)


def serialize_words(words: List[ReceiptWord]) -> List[Dict[str, Any]]:
    """Serialize a list of ReceiptWord objects."""
    return [serialize_word(w) for w in words]


def deserialize_words(data: List[Dict[str, Any]]) -> List[ReceiptWord]:
    """Deserialize a list of ReceiptWord objects."""
    return [deserialize_word(d) for d in data]


def serialize_labels(labels: List[ReceiptWordLabel]) -> List[Dict[str, Any]]:
    """Serialize a list of ReceiptWordLabel objects."""
    return [serialize_label(l) for l in labels]


def deserialize_labels(
    data: List[Dict[str, Any]]
) -> List[ReceiptWordLabel]:
    """Deserialize a list of ReceiptWordLabel objects."""
    return [deserialize_label(d) for d in data]
