"""
DynamoDB entity for managing compaction locks.

This module defines a single-row mutex that guards Chroma compaction jobs.
It uses DynamoDB TTL to automatically release locks after a specified time.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from receipt_dynamo.constants import ChromaDBCollection
from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid


@dataclass(eq=True, unsafe_hash=True)
class CompactionLock(DynamoDBEntity):
    """
    Single-row mutex that guards Chroma compaction jobs.

    A job acquires the lock with a conditional PutItem:
        ConditionExpression="attribute_not_exists(PK) OR expires < :now"

    Attributes
    ----------
    lock_id : str
        Logical name for the protected resource
        (e.g. "chroma-main-snapshot").
    owner : str
        UUID for the worker that currently holds the lock.
    expires : str | datetime
        ISO timestamp—when the lock auto-expires (TTL enabled on table).
    collection : ChromaDBCollection
        The ChromaDB collection this lock protects (lines or words).
    heartbeat : Optional[str | datetime]
        Optional ISO timestamp updated periodically by long-running jobs.
    """

    lock_id: str
    owner: str
    expires: str | datetime
    collection: ChromaDBCollection
    heartbeat: Optional[str | datetime] = None

    # ────────────────────────── validation ────────────────────────────
    def __post_init__(self) -> None:
        assert_valid_uuid(self.owner)

        # Validate lock_id
        if not isinstance(self.lock_id, str) or not self.lock_id.strip():
            raise ValueError("lock_id must be a non-empty string")

        # Validate collection
        if not isinstance(self.collection, ChromaDBCollection):
            if isinstance(self.collection, str):
                # Try to convert string to enum
                try:
                    self.collection = ChromaDBCollection(self.collection)
                except ValueError:
                    valid_values = [c.value for c in ChromaDBCollection]
                    raise ValueError(
                        f"ChromaDBCollection must be one of: {valid_values}, got: {self.collection}"
                    )
            else:
                raise ValueError(
                    f"collection must be ChromaDBCollection or str, got: {type(self.collection)}"
                )

        # Validate expires
        if isinstance(self.expires, datetime):
            self.expires = self.expires.isoformat()
        elif not isinstance(self.expires, str):
            raise ValueError("expires must be datetime or ISO-8601 string")

        # Validate heartbeat
        if self.heartbeat:
            if isinstance(self.heartbeat, datetime):
                self.heartbeat = self.heartbeat.isoformat()
            elif not isinstance(self.heartbeat, str):
                raise ValueError(
                    "heartbeat must be datetime, ISO-8601 string, or None"
                )

    # ───────────────────────── DynamoDB keys ──────────────────────────
    @property
    def key(self) -> Dict[str, Any]:
        # One row per lock_id and collection combination
        return {
            "PK": {"S": f"LOCK#{self.collection.value}#{self.lock_id}"},
            "SK": {"S": "LOCK"},
        }

    @property
    def gsi1_key(self) -> Dict[str, Any]:
        # Enables "list all active locks by collection and expiry" admin queries
        expires_str = (
            self.expires
            if isinstance(self.expires, str)
            else self.expires.isoformat()
        )
        return {
            "GSI1PK": {"S": f"LOCK#{self.collection.value}"},
            "GSI1SK": {"S": f"EXPIRES#{expires_str}"},
        }

    # ───────────────────── DynamoDB marshalling ───────────────────────
    def to_item(self) -> Dict[str, Any]:
        # Ensure datetime fields are converted to ISO strings
        expires_str = (
            self.expires
            if isinstance(self.expires, str)
            else self.expires.isoformat()
        )
        heartbeat_str = None
        if self.heartbeat:
            heartbeat_str = (
                self.heartbeat
                if isinstance(self.heartbeat, str)
                else self.heartbeat.isoformat()
            )

        return {
            **self.key,
            **self.gsi1_key,
            "TYPE": {"S": "COMPACTION_LOCK"},
            "owner": {"S": self.owner},
            "expires": {"S": expires_str},
            "collection": {"S": self.collection.value},
            "heartbeat": (
                {"S": heartbeat_str} if heartbeat_str else {"NULL": True}
            ),
        }

    # ───────────────────────── string repr ────────────────────────────
    def __repr__(self) -> str:
        return (
            "CompactionLock("
            f"lock_id={_repr_str(self.lock_id)}, "
            f"owner={_repr_str(self.owner)}, "
            f"expires={self.expires}, "
            f"collection={self.collection.value}, "
            f"heartbeat={_repr_str(self.heartbeat)}"
            ")"
        )


# Helper for reverse conversion
def item_to_compaction_lock(item: Dict[str, Any]) -> "CompactionLock":
    required = {"PK", "SK", "owner", "expires", "collection"}
    missing = DynamoDBEntity.validate_keys(item, required)
    if missing:
        raise ValueError(f"Lock item missing keys: {missing}")

    # Parse PK format: LOCK#{collection}#{lock_id}
    pk_parts = item["PK"]["S"].split("#")
    if len(pk_parts) < 3 or pk_parts[0] != "LOCK":
        raise ValueError(f"Invalid lock PK format: {item['PK']['S']}")

    collection_value = pk_parts[1]
    lock_id = "#".join(pk_parts[2:])  # Rejoin in case lock_id contains #

    # Validate collection value
    try:
        collection = ChromaDBCollection(collection_value)
    except ValueError:
        valid_values = [c.value for c in ChromaDBCollection]
        raise ValueError(
            f"Invalid collection in item: {collection_value}. Must be one of: {valid_values}"
        )

    return CompactionLock(
        lock_id=lock_id,
        owner=item["owner"]["S"],
        expires=item["expires"]["S"],
        collection=collection,
        heartbeat=item.get("heartbeat", {}).get("S"),
    )
