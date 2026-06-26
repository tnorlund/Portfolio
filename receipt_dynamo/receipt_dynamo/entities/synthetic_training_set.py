"""SyntheticTrainingSet entity.

A versioned, approvable record of a synthetic LayoutLM training set. The bundle
itself (the JSON of train-only synthetic examples) lives in S3; this record is
its metadata, provenance, gate evidence, mix-balance verdict, human-approval
status, and the links to the training Jobs that consumed it.

Keys mirror the Job entity:
  PK = SYNTHETIC_SET#<set_id>, SK = SYNTHETIC_SET
  GSI1 by approval status:   SYNTH_SET_STATUS#<status> / CREATED#<created_at>
  GSI2 by content hash:      SYNTH_SET_HASH#<content_hash> / CREATED#<created_at>
"""

from dataclasses import dataclass, field
from typing import Any

from receipt_dynamo.entities.dynamodb_utils import (
    dict_to_dynamodb_map,
    parse_dynamodb_map,
)
from receipt_dynamo.entities.util import assert_valid_uuid

VALID_SYNTHETIC_SET_STATUSES = (
    "draft",
    "pending_review",
    "approved",
    "rejected",
)


@dataclass(eq=True, unsafe_hash=False)
class SyntheticTrainingSet:
    """A synthetic LayoutLM training set tracked in DynamoDB."""

    set_id: str
    name: str
    created_at: str
    created_by: str
    status: str
    bundle_s3_uri: str
    content_hash: str
    merchants: list[str] = field(default_factory=list)
    accepted_count: int = 0
    candidates_seen: int = 0
    candidates_rejected: int = 0
    operation_counts: dict[str, int] = field(default_factory=dict)
    mix_balance: dict[str, Any] = field(default_factory=dict)
    source_receipt_keys: list[str] = field(default_factory=list)
    gate_versions: dict[str, Any] = field(default_factory=dict)
    generation_config: dict[str, Any] = field(default_factory=dict)
    approved_by: str | None = None
    approved_at: str | None = None
    used_by_jobs: list[str] = field(default_factory=list)
    notes: str | None = None

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "TYPE",
        "name",
        "created_at",
        "created_by",
        "status",
        "bundle_s3_uri",
        "content_hash",
    }

    def __post_init__(self) -> None:
        assert_valid_uuid(self.set_id)
        if self.status not in VALID_SYNTHETIC_SET_STATUSES:
            raise ValueError(
                "status must be one of "
                f"{VALID_SYNTHETIC_SET_STATUSES}, got {self.status!r}"
            )

    @property
    def key(self) -> dict[str, Any]:
        return {
            "PK": {"S": f"SYNTHETIC_SET#{self.set_id}"},
            "SK": {"S": "SYNTHETIC_SET"},
        }

    def gsi1_key(self) -> dict[str, Any]:
        return {
            "GSI1PK": {"S": f"SYNTH_SET_STATUS#{self.status}"},
            "GSI1SK": {"S": f"CREATED#{self.created_at}"},
        }

    def gsi2_key(self) -> dict[str, Any]:
        return {
            "GSI2PK": {"S": f"SYNTH_SET_HASH#{self.content_hash}"},
            "GSI2SK": {"S": f"CREATED#{self.created_at}"},
        }

    def to_item(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            **self.key,
            **self.gsi1_key(),
            **self.gsi2_key(),
            "TYPE": {"S": "SYNTHETIC_TRAINING_SET"},
            "name": {"S": self.name},
            "created_at": {"S": self.created_at},
            "created_by": {"S": self.created_by},
            "status": {"S": self.status},
            "bundle_s3_uri": {"S": self.bundle_s3_uri},
            "content_hash": {"S": self.content_hash},
            "merchants": {"L": [{"S": m} for m in self.merchants]},
            "accepted_count": {"N": str(self.accepted_count)},
            "candidates_seen": {"N": str(self.candidates_seen)},
            "candidates_rejected": {"N": str(self.candidates_rejected)},
            "operation_counts": {
                "M": dict_to_dynamodb_map(self.operation_counts)
            },
            "mix_balance": {"M": dict_to_dynamodb_map(self.mix_balance)},
            "source_receipt_keys": {
                "L": [{"S": k} for k in self.source_receipt_keys]
            },
            "gate_versions": {"M": dict_to_dynamodb_map(self.gate_versions)},
            "generation_config": {
                "M": dict_to_dynamodb_map(self.generation_config)
            },
            "used_by_jobs": {"L": [{"S": j} for j in self.used_by_jobs]},
        }
        if self.approved_by is not None:
            item["approved_by"] = {"S": self.approved_by}
        if self.approved_at is not None:
            item["approved_at"] = {"S": self.approved_at}
        if self.notes is not None:
            item["notes"] = {"S": self.notes}
        return item

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "SyntheticTrainingSet":
        if not cls.REQUIRED_KEYS.issubset(item.keys()):
            missing = cls.REQUIRED_KEYS - item.keys()
            raise ValueError(f"Invalid item format\nmissing keys: {missing}")
        try:
            return cls(
                set_id=item["PK"]["S"].split("#", 1)[1],
                name=item["name"]["S"],
                created_at=item["created_at"]["S"],
                created_by=item["created_by"]["S"],
                status=item["status"]["S"],
                bundle_s3_uri=item["bundle_s3_uri"]["S"],
                content_hash=item["content_hash"]["S"],
                merchants=[m["S"] for m in item.get("merchants", {}).get("L", [])],
                accepted_count=int(item.get("accepted_count", {}).get("N", "0")),
                candidates_seen=int(item.get("candidates_seen", {}).get("N", "0")),
                candidates_rejected=int(
                    item.get("candidates_rejected", {}).get("N", "0")
                ),
                operation_counts=parse_dynamodb_map(
                    item.get("operation_counts", {}).get("M", {})
                ),
                mix_balance=parse_dynamodb_map(
                    item.get("mix_balance", {}).get("M", {})
                ),
                source_receipt_keys=[
                    k["S"] for k in item.get("source_receipt_keys", {}).get("L", [])
                ],
                gate_versions=parse_dynamodb_map(
                    item.get("gate_versions", {}).get("M", {})
                ),
                generation_config=parse_dynamodb_map(
                    item.get("generation_config", {}).get("M", {})
                ),
                approved_by=item["approved_by"]["S"]
                if "approved_by" in item
                else None,
                approved_at=item["approved_at"]["S"]
                if "approved_at" in item
                else None,
                used_by_jobs=[
                    j["S"] for j in item.get("used_by_jobs", {}).get("L", [])
                ],
                notes=item["notes"]["S"] if "notes" in item else None,
            )
        except (KeyError, IndexError) as e:
            raise ValueError(
                f"Error converting item to SyntheticTrainingSet: {e}"
            ) from e

    def __hash__(self) -> int:
        return hash((self.set_id, self.content_hash))


def item_to_synthetic_training_set(
    item: dict[str, Any],
) -> SyntheticTrainingSet:
    return SyntheticTrainingSet.from_item(item)
