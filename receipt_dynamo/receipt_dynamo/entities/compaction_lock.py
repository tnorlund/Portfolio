"""
DynamoDB entity for managing compaction locks.

This module defines a single-row mutex that guards Chroma compaction jobs.
It uses DynamoDB TTL to automatically release locks after a specified time.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid


@dataclass(eq=True, unsafe_hash=False)
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
    heartbeat : Optional[str | datetime]
        Optional ISO timestamp updated periodically by long-running jobs.
    """

    lock_id: str
    owner: str
    expires: str | datetime
    heartbeat: Optional[str | datetime] = None

    # ────────────────────────── validation ────────────────────────────
    def __post_init__(self) -> None:
        assert_valid_uuid(self.owner)

        if isinstance(self.expires, datetime):
            self.expires = self.expires.isoformat()
        elif not isinstance(self.expires, str):
            raise ValueError("expires must be datetime or ISO-8601 string")

        if self.heartbeat and isinstance(self.heartbeat, datetime):
            self.heartbeat = self.heartbeat.isoformat()

    # ───────────────────────── DynamoDB keys ──────────────────────────
    @property
    def key(self) -> Dict[str, Any]:
        # One row per lock_id
        return {
            "PK": {"S": f"LOCK#{self.lock_id}"},
            "SK": {"S": "LOCK"},
        }

    @property
    def gsi1_key(self) -> Dict[str, Any]:
        # Enables "list all active locks by expiry" admin queries
        return {
            "GSI1PK": {"S": "LOCK"},
            "GSI1SK": {"S": f"EXPIRES#{self.expires}"},
        }

    # ───────────────────── DynamoDB marshalling ───────────────────────
    def to_item(self) -> Dict[str, Any]:
        return {
            **self.key,
            **self.gsi1_key,
            "TYPE": {"S": "COMPACTION_LOCK"},
            "owner": {"S": self.owner},
            "expires": {"S": self.expires},
            "heartbeat": (
                {"S": self.heartbeat} if self.heartbeat else {"NULL": True}
            ),
        }

    # ───────────────────────── string repr ────────────────────────────
    def __repr__(self) -> str:
        return (
            "CompactionLock("
            f"lock_id={_repr_str(self.lock_id)}, "
            f"owner={_repr_str(self.owner)}, "
            f"expires={self.expires}, "
            f"heartbeat={_repr_str(self.heartbeat)}"
            ")"
        )


# Helper for reverse conversion
def item_to_compaction_lock(item: Dict[str, Any]) -> "CompactionLock":
    required = {"PK", "SK", "owner", "expires"}
    missing = DynamoDBEntity.validate_keys(item, required)
    if missing:
        raise ValueError(f"Lock item missing keys: {missing}")

    return CompactionLock(
        lock_id=item["PK"]["S"].split("#", 1)[1],
        owner=item["owner"]["S"],
        expires=item["expires"]["S"],
        heartbeat=item.get("heartbeat", {}).get("S"),
    )
