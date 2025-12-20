"""Entity serialization for S3 data transfer.

Provides functions to serialize/deserialize receipt entities (ReceiptWord,
ReceiptWordLabel, ReceiptMetadata) for JSON storage in S3.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from receipt_dynamo.entities import (
    ReceiptMetadata,
    ReceiptWord,
    ReceiptWordLabel,
)


def serialize_word(word: ReceiptWord) -> Dict[str, Any]:
    """Serialize ReceiptWord for S3 storage."""
    return {
        "image_id": word.image_id,
        "receipt_id": word.receipt_id,
        "line_id": word.line_id,
        "word_id": word.word_id,
        "text": word.text,
        "bounding_box": word.bounding_box,
        "top_right": word.top_right,
        "top_left": word.top_left,
        "bottom_right": word.bottom_right,
        "bottom_left": word.bottom_left,
        "angle_degrees": word.angle_degrees,
        "angle_radians": word.angle_radians,
        "confidence": word.confidence,
        "extracted_data": word.extracted_data,
        "embedding_status": (
            str(word.embedding_status)
            if word.embedding_status
            else None
        ),
        "is_noise": word.is_noise,
    }


def deserialize_word(data: Dict[str, Any]) -> ReceiptWord:
    """Deserialize ReceiptWord from S3 data."""
    return ReceiptWord(
        image_id=data["image_id"],
        receipt_id=data["receipt_id"],
        line_id=data["line_id"],
        word_id=data["word_id"],
        text=data["text"],
        bounding_box=data.get("bounding_box"),
        top_right=data.get("top_right"),
        top_left=data.get("top_left"),
        bottom_right=data.get("bottom_right"),
        bottom_left=data.get("bottom_left"),
        angle_degrees=data.get("angle_degrees"),
        angle_radians=data.get("angle_radians"),
        confidence=data.get("confidence"),
        extracted_data=data.get("extracted_data"),
        embedding_status=data.get("embedding_status", "NONE"),
        is_noise=data.get("is_noise", False),
    )


def serialize_label(label: ReceiptWordLabel) -> Dict[str, Any]:
    """Serialize ReceiptWordLabel for S3 storage."""
    timestamp = label.timestamp_added
    if hasattr(timestamp, "isoformat"):
        timestamp_str = timestamp.isoformat()
    else:
        timestamp_str = str(timestamp) if timestamp else None

    return {
        "image_id": label.image_id,
        "receipt_id": label.receipt_id,
        "line_id": label.line_id,
        "word_id": label.word_id,
        "label": label.label,
        "reasoning": label.reasoning,
        "timestamp_added": timestamp_str,
        "validation_status": label.validation_status,
        "label_proposed_by": label.label_proposed_by,
        "label_consolidated_from": label.label_consolidated_from,
    }


def deserialize_label(data: Dict[str, Any]) -> ReceiptWordLabel:
    """Deserialize ReceiptWordLabel from S3 data."""
    timestamp = data.get("timestamp_added")
    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp)
        except ValueError:
            timestamp = None

    return ReceiptWordLabel(
        image_id=data["image_id"],
        receipt_id=data["receipt_id"],
        line_id=data["line_id"],
        word_id=data["word_id"],
        label=data["label"],
        reasoning=data.get("reasoning"),
        timestamp_added=timestamp,
        validation_status=data.get("validation_status"),
        label_proposed_by=data.get("label_proposed_by"),
        label_consolidated_from=data.get("label_consolidated_from"),
    )


def serialize_metadata(metadata: ReceiptMetadata) -> Dict[str, Any]:
    """Serialize ReceiptMetadata for S3 storage."""
    return {
        "image_id": metadata.image_id,
        "receipt_id": metadata.receipt_id,
        "merchant_name": metadata.merchant_name,
        "canonical_merchant_name": metadata.canonical_merchant_name,
        "place_id": getattr(metadata, "place_id", None),
        "address": getattr(metadata, "address", None),
        "date": (
            metadata.date.isoformat()
            if hasattr(metadata, "date") and metadata.date
            else None
        ),
        "total": getattr(metadata, "total", None),
        "currency": getattr(metadata, "currency", None),
    }


def deserialize_metadata(data: Dict[str, Any]) -> Optional[ReceiptMetadata]:
    """Deserialize ReceiptMetadata from S3 data."""
    if not data:
        return None

    date_val = data.get("date")
    if isinstance(date_val, str):
        try:
            date_val = datetime.fromisoformat(date_val).date()
        except ValueError:
            date_val = None

    return ReceiptMetadata(
        image_id=data["image_id"],
        receipt_id=data["receipt_id"],
        merchant_name=data.get("merchant_name"),
        canonical_merchant_name=data.get("canonical_merchant_name"),
        place_id=data.get("place_id"),
        address=data.get("address"),
        date=date_val,
        total=data.get("total"),
        currency=data.get("currency"),
    )


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
