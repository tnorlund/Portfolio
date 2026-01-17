"""
DynamoDB entity to track a per-run Chroma compaction of delta DBs.

Use cases:
- Correlate separate lines/words delta merges under a single run_id
- Track state and timings for each collection
  (pending → processing → completed/failed)
- Provide a simple way to know when both collections are finished
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from receipt_dynamo.constants import CompactionState
from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
    validate_positive_int,
)

# Allowed compaction states come from constants.CompactionState
_COMPACTION_STATES = {c.value for c in CompactionState}


@dataclass(eq=True, unsafe_hash=False)
class CompactionRun(DynamoDBEntity):
    """
    Tracks a delta compaction run across both collections.

    Attributes
    ----------
    run_id: str
        UUID for this run (correlates lines/words operations).
    image_id: str | None
        Optional target image_id when the run is for a single receipt.
    receipt_id: int | None
        Optional target receipt_id when the run is for a single receipt.
    lines_delta_prefix: str
        S3 prefix for the lines delta uploaded for this run.
    words_delta_prefix: str
        S3 prefix for the words delta uploaded for this run.

    # Per-collection state
    lines_state: str
    words_state: str
    lines_started_at: str | datetime | None
    lines_finished_at: str | datetime | None
    words_started_at: str | datetime | None
    words_finished_at: str | datetime | None
    lines_error: str
    words_error: str
    lines_merged_vectors: int
    words_merged_vectors: int

    created_at: str | datetime
    updated_at: str | datetime | None
    """

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "TYPE",
        "lines_delta_prefix",
        "words_delta_prefix",
        "created_at",
    }

    run_id: str
    image_id: str
    receipt_id: int
    lines_delta_prefix: str
    words_delta_prefix: str

    lines_state: str = "PENDING"
    words_state: str = "PENDING"

    lines_started_at: str | datetime | None = None
    lines_finished_at: str | datetime | None = None
    words_started_at: str | datetime | None = None
    words_finished_at: str | datetime | None = None

    lines_error: str = ""
    words_error: str = ""

    lines_merged_vectors: int = 0
    words_merged_vectors: int = 0

    created_at: str | datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: str | datetime | None = None

    # ────────────────────────── validation ────────────────────────────
    def __post_init__(self) -> None:
        assert_valid_uuid(self.run_id)

        # Validate required targeting
        assert_valid_uuid(self.image_id)
        validate_positive_int("receipt_id", self.receipt_id)

        if self.lines_state not in _COMPACTION_STATES:
            raise ValueError(f"invalid lines_state: {self.lines_state}")
        if self.words_state not in _COMPACTION_STATES:
            raise ValueError(f"invalid words_state: {self.words_state}")

        if (
            not isinstance(self.lines_delta_prefix, str)
            or not self.lines_delta_prefix
        ):
            raise ValueError("lines_delta_prefix must be a non-empty string")
        if (
            not isinstance(self.words_delta_prefix, str)
            or not self.words_delta_prefix
        ):
            raise ValueError("words_delta_prefix must be a non-empty string")

        # Normalize datetime fields to ISO strings in-place for consistency
        for attr in (
            "lines_started_at",
            "lines_finished_at",
            "words_started_at",
            "words_finished_at",
            "created_at",
            "updated_at",
        ):
            val = getattr(self, attr)
            if val is None:
                continue
            if isinstance(val, datetime):
                setattr(self, attr, val.isoformat())
            elif not isinstance(val, str):
                raise ValueError(
                    f"{attr} must be datetime, ISO-8601 string, or None"
                )

        if (
            not isinstance(self.lines_merged_vectors, int)
            or self.lines_merged_vectors < 0
        ):
            raise ValueError("lines_merged_vectors must be a non-negative int")
        if (
            not isinstance(self.words_merged_vectors, int)
            or self.words_merged_vectors < 0
        ):
            raise ValueError("words_merged_vectors must be a non-negative int")

    # ───────────────────── DynamoDB keys ─────────────────────
    @property
    def key(self) -> dict[str, Any]:
        # Align keys with receipt design: partition by image,
        # sort by receipt/run
        sk = f"RECEIPT#{self.receipt_id:05d}#COMPACTION_RUN#{self.run_id}"
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": sk},
        }

    def gsi1_key(self) -> dict[str, Any]:
        # List runs by created_at (table-per-env, so no env in key)
        created = (
            self.created_at
            if isinstance(self.created_at, str)
            else self.created_at.isoformat()
        )
        return {
            "GSI1PK": {"S": "RUNS"},
            "GSI1SK": {"S": f"CREATED_AT#{created}"},
        }

    # ───────────────────── DynamoDB marshalling ───────────────────────
    def to_item(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            **self.key,
            **self.gsi1_key(),
            "TYPE": {"S": "COMPACTION_RUN"},
            "run_id": {"S": self.run_id},
            "image_id": {"S": self.image_id},
            "receipt_id": {"N": str(self.receipt_id)},
            "lines_delta_prefix": {"S": self.lines_delta_prefix},
            "words_delta_prefix": {"S": self.words_delta_prefix},
            "lines_state": {"S": self.lines_state},
            "words_state": {"S": self.words_state},
            "lines_merged_vectors": {"N": str(self.lines_merged_vectors)},
            "words_merged_vectors": {"N": str(self.words_merged_vectors)},
            "created_at": {"S": self.created_at},
        }

        # Optional timestamps and error fields
        def _opt_str(name: str, val: str | None) -> dict[str, Any]:
            return {name: {"S": val}} if val else {name: {"NULL": True}}

        for name in (
            "lines_started_at",
            "lines_finished_at",
            "words_started_at",
            "words_finished_at",
            "updated_at",
        ):
            item.update(_opt_str(name, getattr(self, name)))

        for name in ("lines_error", "words_error"):
            val = getattr(self, name)
            item[name] = {"S": val} if val else {"NULL": True}

        return item

    # ───────────────────────── string repr ────────────────────────────
    def __repr__(self) -> str:
        return (
            "CompactionRun("
            f"run_id={_repr_str(self.run_id)}, "
            f"image_id={_repr_str(self.image_id)}, "
            f"receipt_id={_repr_str(self.receipt_id)}, "
            f"lines_state={_repr_str(self.lines_state)}, "
            f"words_state={_repr_str(self.words_state)}, "
            f"lines_merged_vectors={self.lines_merged_vectors}, "
            f"words_merged_vectors={self.words_merged_vectors}"
            ")"
        )

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "CompactionRun":
        """Converts a DynamoDB item to a CompactionRun object.

        Args:
            item: The DynamoDB item to convert.

        Returns:
            CompactionRun: The CompactionRun object.

        Raises:
            ValueError: When the item format is invalid.
        """
        missing = DynamoDBEntity.validate_keys(item, cls.REQUIRED_KEYS)
        if missing:
            raise ValueError(f"CompactionRun item missing keys: {missing}")

        try:
            # Parse keys: PK=IMAGE#<image_id>,
            # SK=RECEIPT#<id>#COMPACTION_RUN#<run_id>
            pk = item["PK"]["S"]
            if not pk.startswith("IMAGE#"):
                raise ValueError(f"Invalid PK for CompactionRun: {pk}")
            image_id = pk.split("#", 1)[1]

            sk = item["SK"]["S"]
            parts = sk.split("#")
            if (
                len(parts) < 4
                or parts[0] != "RECEIPT"
                or parts[2] != "COMPACTION_RUN"
            ):
                raise ValueError(f"Invalid SK for CompactionRun: {sk}")
            try:
                receipt_id = int(parts[1])
            except ValueError as e:
                raise ValueError(f"Invalid receipt_id in SK: {sk}") from e
            run_id = parts[3]

            def _get_s(name: str, default: str = "") -> str:
                v = item.get(name)
                if v and "S" in v:
                    return v["S"]
                return default

            def _get_n(name: str, default: int = 0) -> int:
                v = item.get(name)
                if v and "N" in v:
                    try:
                        return int(v["N"])
                    except (KeyError, ValueError):
                        return default
                return default

            return cls(
                run_id=run_id,
                image_id=image_id,
                receipt_id=receipt_id,
                lines_delta_prefix=_get_s("lines_delta_prefix"),
                words_delta_prefix=_get_s("words_delta_prefix"),
                lines_state=_get_s("lines_state", "PENDING"),
                words_state=_get_s("words_state", "PENDING"),
                lines_started_at=_get_s("lines_started_at") or None,
                lines_finished_at=_get_s("lines_finished_at") or None,
                words_started_at=_get_s("words_started_at") or None,
                words_finished_at=_get_s("words_finished_at") or None,
                lines_error=_get_s("lines_error"),
                words_error=_get_s("words_error"),
                lines_merged_vectors=_get_n("lines_merged_vectors", 0),
                words_merged_vectors=_get_n("words_merged_vectors", 0),
                created_at=_get_s("created_at"),
                updated_at=_get_s("updated_at") or None,
            )
        except Exception as e:
            raise ValueError(
                f"Error converting item to CompactionRun: {e}"
            ) from e


def item_to_compaction_run(item: dict[str, Any]) -> CompactionRun:
    """Converts a DynamoDB item to a CompactionRun object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        CompactionRun: The CompactionRun object.

    Raises:
        ValueError: When the item format is invalid.
    """
    return CompactionRun.from_item(item)
