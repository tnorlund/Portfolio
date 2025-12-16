"""Models for DynamoDB stream processing."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple


@dataclass
class FieldChange:
    """Represents a field change in a DynamoDB stream record."""

    old: Any
    new: Any


@dataclass
class StreamMessage:
    """Represents a parsed DynamoDB stream message."""

    entity_type: str
    entity_data: Dict[str, Any]
    changes: Dict[str, FieldChange]
    event_name: str
    collections: Tuple
    timestamp: str
    stream_record_id: str
    aws_region: str
    record_snapshot: Optional[Dict[str, Any]] = None
