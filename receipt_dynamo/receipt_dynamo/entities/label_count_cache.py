import json
import time
from datetime import datetime
from typing import Any, Dict, Generator, Optional, Tuple


class LabelCountCache:
    """Represents cached label validation counts stored in DynamoDB."""

    def __init__(
        self,
        label: str,
        valid_count: int,
        invalid_count: int,
        pending_count: int,
        needs_review_count: int,
        none_count: int,
        last_updated: str,
        time_to_live: Optional[int] = None,
    ) -> None:
        if not isinstance(label, str) or not label:
            raise ValueError("label must be a non-empty string")
        if not isinstance(valid_count, int) or valid_count < 0:
            raise ValueError("valid_count must be a non-negative integer")
        if not isinstance(invalid_count, int) or invalid_count < 0:
            raise ValueError("invalid_count must be a non-negative integer")
        if not isinstance(pending_count, int) or pending_count < 0:
            raise ValueError("pending_count must be a non-negative integer")
        if not isinstance(needs_review_count, int) or needs_review_count < 0:
            raise ValueError(
                "needs_review_count must be a non-negative integer"
            )
        if not isinstance(none_count, int) or none_count < 0:
            raise ValueError("none_count must be a non-negative integer")
        try:
            datetime.fromisoformat(last_updated)
        except (TypeError, ValueError) as exc:
            raise ValueError("last_updated must be ISO formatted") from exc
        if time_to_live is not None:
            if not isinstance(time_to_live, int) or time_to_live < 0:
                raise ValueError("time_to_live must be non-negative integer")
            now = int(time.time())
            if time_to_live < now:
                raise ValueError("time_to_live must be in the future")
        self.label: str = label
        self.valid_count = valid_count
        self.invalid_count = invalid_count
        self.pending_count = pending_count
        self.needs_review_count = needs_review_count
        self.none_count = none_count
        self.last_updated = last_updated
        self.time_to_live = time_to_live

    @property
    def key(self) -> Dict[str, Dict[str, str]]:
        return {"PK": {"S": "LABEL_CACHE"}, "SK": {"S": f"LABEL#{self.label}"}}

    def to_item(self) -> Dict[str, Dict[str, Any]]:
        item = {
            **self.key,
            "TYPE": {"S": "LABEL_COUNT_CACHE"},
            "label": {"S": self.label},
            "valid_count": {"N": str(self.valid_count)},
            "invalid_count": {"N": str(self.invalid_count)},
            "pending_count": {"N": str(self.pending_count)},
            "needs_review_count": {"N": str(self.needs_review_count)},
            "none_count": {"N": str(self.none_count)},
            "last_updated": {"S": self.last_updated},
        }
        if self.time_to_live is not None:
            item["time_to_live"] = {"N": str(self.time_to_live)}
        return item

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        yield "label", self.label
        yield "valid_count", self.valid_count
        yield "invalid_count", self.invalid_count
        yield "pending_count", self.pending_count
        yield "needs_review_count", self.needs_review_count
        yield "none_count", self.none_count
        yield "last_updated", self.last_updated
        if self.time_to_live is not None:
            yield "time_to_live", self.time_to_live

    def __repr__(self) -> str:
        base = (
            f"LabelCountCache(label='{self.label}', "
            f"valid_count={self.valid_count}, "
            f"invalid_count={self.invalid_count}, "
            f"pending_count={self.pending_count}, "
            f"needs_review_count={self.needs_review_count}, "
            f"none_count={self.none_count}, "
            f"last_updated='{self.last_updated}'"
        )
        if self.time_to_live is not None:
            base += f", time_to_live={self.time_to_live}"
        return base + ")"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LabelCountCache):
            return False
        return (
            self.label == other.label
            and self.valid_count == other.valid_count
            and self.invalid_count == other.invalid_count
            and self.pending_count == other.pending_count
            and self.needs_review_count == other.needs_review_count
            and self.none_count == other.none_count
            and self.last_updated == other.last_updated
            and self.time_to_live == other.time_to_live
        )


def item_to_label_count_cache(
    item: Dict[str, Any],
) -> LabelCountCache:
    required = {
        "PK",
        "SK",
        "valid_count",
        "invalid_count",
        "pending_count",
        "needs_review_count",
        "none_count",
        "last_updated",
    }
    if not required.issubset(item):
        raise ValueError("Item is missing required keys")
    label = item["SK"]["S"].split("#", 1)[1]
    valid_count = int(item["valid_count"]["N"])
    invalid_count = int(item["invalid_count"]["N"])
    pending_count = int(item["pending_count"]["N"])
    needs_review_count = int(item["needs_review_count"]["N"])
    none_count = int(item["none_count"]["N"])
    last_updated = item["last_updated"]["S"]
    ttl = int(item["time_to_live"]["N"]) if "time_to_live" in item else None
    return LabelCountCache(
        label=label,
        valid_count=valid_count,
        invalid_count=invalid_count,
        pending_count=pending_count,
        needs_review_count=needs_review_count,
        none_count=none_count,
        last_updated=last_updated,
        time_to_live=ttl,
    )
