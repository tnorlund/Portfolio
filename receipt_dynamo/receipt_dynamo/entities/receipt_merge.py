"""Durable progress for one unordered pair of receipt fragments."""

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.util import assert_valid_uuid


@dataclass
class ReceiptMerge(DynamoDBEntity):
    """Keep the output identity and cleanup manifest across Lambda retries.

    The lease outlives the merge Lambda's ten-minute maximum runtime. Rows
    and ID reservations do not expire: a duplicate can arrive after cleanup.
    """

    image_id: str
    source_ids: tuple[int, int]
    output_id: int
    owner: str
    lease_until: int
    status: Literal["PREPARING", "READY", "COMPLETED"] = "PREPARING"
    result: dict[str, Any] = field(default_factory=dict)
    source_assets: dict[str, list[list[str]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        assert_valid_uuid(self.image_id)
        if (
            len(self.source_ids) != 2
            or len(set(self.source_ids)) != 2
            or any(
                not isinstance(rid, int) or isinstance(rid, bool) or rid < 1
                for rid in self.source_ids
            )
        ):
            raise ValueError("source_ids must be two distinct positive IDs")
        first, second = sorted(self.source_ids)
        self.source_ids = (first, second)
        if (
            not isinstance(self.output_id, int)
            or isinstance(self.output_id, bool)
            or self.output_id < 1
        ):
            raise ValueError("output_id must be a positive integer")
        if self.output_id in self.source_ids:
            raise ValueError("output_id must differ from the source IDs")
        if self.status not in ("PREPARING", "READY", "COMPLETED"):
            raise ValueError("invalid merge status")
        if not isinstance(self.owner, str) or not self.owner:
            raise ValueError("owner must be nonempty")
        if (
            not isinstance(self.lease_until, int)
            or isinstance(self.lease_until, bool)
            or self.lease_until < 0
        ):
            raise ValueError("lease_until must be a nonnegative integer")

    @property
    def key(self) -> dict[str, Any]:
        """The unordered source pair is the durable operation identity."""
        first, second = self.source_ids
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"MERGE#{first:05d}#{second:05d}"},
        }

    def to_item(self) -> dict[str, Any]:
        """Store only bounded response data and explicit object references."""
        self.__post_init__()
        return {
            **self.key,
            "TYPE": {"S": "RECEIPT_MERGE"},
            "output_id": {"N": str(self.output_id)},
            "owner": {"S": self.owner},
            "lease_until": {"N": str(self.lease_until)},
            "status": {"S": self.status},
            "result": {"S": json.dumps(self.result)},
            "source_assets": {"S": json.dumps(self.source_assets)},
        }


def item_to_receipt_merge(item: dict[str, Any]) -> ReceiptMerge:
    """Read a journal without depending on a GSI."""
    _, first, second = item["SK"]["S"].split("#")
    return ReceiptMerge(
        image_id=item["PK"]["S"].removeprefix("IMAGE#"),
        source_ids=(int(first), int(second)),
        output_id=int(item["output_id"]["N"]),
        owner=item["owner"]["S"],
        lease_until=int(item["lease_until"]["N"]),
        status=item["status"]["S"],
        result=json.loads(item["result"]["S"]),
        source_assets=json.loads(item["source_assets"]["S"]),
    )
