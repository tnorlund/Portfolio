"""DynamoDB entity for Claude visual reviews of synthetic receipt renders."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generator
from urllib.parse import quote

from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.dynamodb_utils import (
    parse_dynamodb_value,
    to_dynamodb_value,
)
from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid

VISUAL_REVIEW_STATUSES = {
    "accepted",
    "needs_iteration",
    "rejected",
    "blocked",
}


def synthetic_visual_review_key_part(value: str) -> str:
    """Return a stable key-safe representation for external identifiers."""
    return quote(str(value), safe="-_.:")


def _iso(value: str | datetime) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp values must be non-empty ISO strings")
    return value


def _optional_str(item: dict[str, Any], name: str) -> str | None:
    value = item.get(name)
    if not value or "NULL" in value:
        return None
    return str(parse_dynamodb_value(value))


def _optional_float(item: dict[str, Any], name: str) -> float | None:
    value = item.get(name)
    if not value or "NULL" in value:
        return None
    parsed = parse_dynamodb_value(value)
    if parsed is None:
        return None
    return float(parsed)


@dataclass(eq=True, unsafe_hash=False)
class SyntheticReceiptVisualReview(DynamoDBEntity):
    """A single visual review observation for a synthetic receipt render."""

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "TYPE",
        "review_id",
        "job_id",
        "candidate_id",
        "status",
        "reviewer",
        "created_at",
    }

    review_id: str
    job_id: str
    candidate_id: str
    status: str
    reviewer: str
    created_at: str | datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    synthetic_image_id: str | None = None
    image_uri: str | None = None
    local_image_path: str | None = None
    base_receipt_key: str | None = None
    merchant_name: str | None = None
    operation: str | None = None
    reviewer_model: str | None = None
    realism_score: float | None = None
    fidelity_score: float | None = None
    alignment_score: float | None = None
    issue_count: int = 0
    findings: list[dict[str, Any]] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    rubric: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        assert_valid_uuid(self.review_id)
        assert_valid_uuid(self.job_id)

        if not isinstance(self.candidate_id, str) or not self.candidate_id:
            raise ValueError("candidate_id must be a non-empty string")
        if not isinstance(self.reviewer, str) or not self.reviewer:
            raise ValueError("reviewer must be a non-empty string")

        self.status = str(self.status).strip().lower()
        if self.status not in VISUAL_REVIEW_STATUSES:
            allowed = ", ".join(sorted(VISUAL_REVIEW_STATUSES))
            raise ValueError(f"status must be one of: {allowed}")

        self.created_at = _iso(self.created_at)
        if not self.synthetic_image_id:
            self.synthetic_image_id = self.candidate_id

        for attr in ("realism_score", "fidelity_score", "alignment_score"):
            value = getattr(self, attr)
            if value is None:
                continue
            if not isinstance(value, (int, float)):
                raise ValueError(f"{attr} must be numeric or None")
            if value < 0 or value > 1:
                raise ValueError(f"{attr} must be between 0 and 1")
            setattr(self, attr, float(value))

        if not isinstance(self.issue_count, int) or self.issue_count < 0:
            raise ValueError("issue_count must be a non-negative int")
        if not isinstance(self.findings, list):
            raise ValueError("findings must be a list")
        if not all(isinstance(item, dict) for item in self.findings):
            raise ValueError("findings must contain dictionaries")
        if not isinstance(self.recommendations, list):
            raise ValueError("recommendations must be a list")
        self.recommendations = [str(item) for item in self.recommendations if item]
        if not isinstance(self.rubric, dict):
            raise ValueError("rubric must be a dictionary")
        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dictionary")

    @property
    def candidate_key(self) -> str:
        return synthetic_visual_review_key_part(self.candidate_id)

    @property
    def image_key(self) -> str:
        return synthetic_visual_review_key_part(self.synthetic_image_id or "")

    @property
    def key(self) -> dict[str, Any]:
        return {
            "PK": {"S": f"JOB#{self.job_id}"},
            "SK": {
                "S": (
                    "SYNTHETIC_VISUAL_REVIEW#"
                    f"CANDIDATE#{self.candidate_key}#"
                    f"CREATED#{self.created_at}#"
                    f"REVIEW#{self.review_id}"
                )
            },
        }

    def gsi1_key(self) -> dict[str, Any]:
        return {
            "GSI1PK": {
                "S": (
                    "SYNTHETIC_VISUAL_REVIEW#"
                    f"CANDIDATE#{self.candidate_key}"
                )
            },
            "GSI1SK": {
                "S": (
                    f"CREATED#{self.created_at}#"
                    f"JOB#{self.job_id}#REVIEW#{self.review_id}"
                )
            },
        }

    def gsi2_key(self) -> dict[str, Any]:
        return {
            "GSI2PK": {
                "S": f"SYNTHETIC_VISUAL_REVIEW#STATUS#{self.status}"
            },
            "GSI2SK": {
                "S": (
                    f"CREATED#{self.created_at}#JOB#{self.job_id}#"
                    f"CANDIDATE#{self.candidate_key}#REVIEW#{self.review_id}"
                )
            },
        }

    def gsi3_key(self) -> dict[str, Any]:
        return {
            "GSI3PK": {
                "S": f"SYNTHETIC_VISUAL_REVIEW#IMAGE#{self.image_key}"
            },
            "GSI3SK": {
                "S": (
                    f"CREATED#{self.created_at}#JOB#{self.job_id}#"
                    f"CANDIDATE#{self.candidate_key}#REVIEW#{self.review_id}"
                )
            },
        }

    def to_item(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            **self.key,
            **self.gsi1_key(),
            **self.gsi2_key(),
            **self.gsi3_key(),
            "TYPE": {"S": "SYNTHETIC_RECEIPT_VISUAL_REVIEW"},
            "review_id": {"S": self.review_id},
            "job_id": {"S": self.job_id},
            "candidate_id": {"S": self.candidate_id},
            "synthetic_image_id": {"S": self.synthetic_image_id or ""},
            "status": {"S": self.status},
            "reviewer": {"S": self.reviewer},
            "created_at": {"S": str(self.created_at)},
            "issue_count": {"N": str(self.issue_count)},
            "findings": to_dynamodb_value(self.findings),
            "recommendations": to_dynamodb_value(self.recommendations),
            "rubric": to_dynamodb_value(self.rubric),
            "metadata": to_dynamodb_value(self.metadata),
        }

        for name in (
            "image_uri",
            "local_image_path",
            "base_receipt_key",
            "merchant_name",
            "operation",
            "reviewer_model",
        ):
            value = getattr(self, name)
            item[name] = {"S": value} if value else {"NULL": True}

        for name in ("realism_score", "fidelity_score", "alignment_score"):
            value = getattr(self, name)
            item[name] = {"N": str(value)} if value is not None else {"NULL": True}

        return item

    def __iter__(self) -> Generator[tuple[str, Any], None, None]:
        yield from super().__iter__()

    def __repr__(self) -> str:
        return (
            "SyntheticReceiptVisualReview("
            f"review_id={_repr_str(self.review_id)}, "
            f"job_id={_repr_str(self.job_id)}, "
            f"candidate_id={_repr_str(self.candidate_id)}, "
            f"status={_repr_str(self.status)}, "
            f"reviewer={_repr_str(self.reviewer)}, "
            f"realism_score={self.realism_score}, "
            f"issue_count={self.issue_count}"
            ")"
        )

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "SyntheticReceiptVisualReview":
        missing = DynamoDBEntity.validate_keys(item, cls.REQUIRED_KEYS)
        if missing:
            raise ValueError(
                f"SyntheticReceiptVisualReview item missing keys: {missing}"
            )

        findings = parse_dynamodb_value(item.get("findings", {"L": []})) or []
        recommendations = (
            parse_dynamodb_value(item.get("recommendations", {"L": []})) or []
        )
        rubric = parse_dynamodb_value(item.get("rubric", {"M": {}})) or {}
        metadata = parse_dynamodb_value(item.get("metadata", {"M": {}})) or {}

        return cls(
            review_id=item["review_id"]["S"],
            job_id=item["job_id"]["S"],
            candidate_id=item["candidate_id"]["S"],
            status=item["status"]["S"],
            reviewer=item["reviewer"]["S"],
            created_at=item["created_at"]["S"],
            synthetic_image_id=_optional_str(item, "synthetic_image_id"),
            image_uri=_optional_str(item, "image_uri"),
            local_image_path=_optional_str(item, "local_image_path"),
            base_receipt_key=_optional_str(item, "base_receipt_key"),
            merchant_name=_optional_str(item, "merchant_name"),
            operation=_optional_str(item, "operation"),
            reviewer_model=_optional_str(item, "reviewer_model"),
            realism_score=_optional_float(item, "realism_score"),
            fidelity_score=_optional_float(item, "fidelity_score"),
            alignment_score=_optional_float(item, "alignment_score"),
            issue_count=int(parse_dynamodb_value(item.get("issue_count", {"N": "0"}))),
            findings=findings,
            recommendations=recommendations,
            rubric=rubric,
            metadata=metadata,
        )


def item_to_synthetic_receipt_visual_review(
    item: dict[str, Any],
) -> SyntheticReceiptVisualReview:
    """Convert a DynamoDB item to a SyntheticReceiptVisualReview."""
    return SyntheticReceiptVisualReview.from_item(item)
