from datetime import datetime
from typing import Any, Generator, Tuple

from receipt_dynamo.constants import RefinementStatus
from receipt_dynamo.entities.util import (
    _format_float,
    _repr_str,
    assert_valid_point,
    assert_valid_uuid,
)


class RefinementJob:
    """
    Represents a refinement job in DynamoDB.
    """

    def __init__(
        self,
        image_id: str,
        job_id: str,
        s3_bucket: str,
        s3_key: str,
        created_at: datetime,
        updated_at: datetime | None = None,
        status: RefinementStatus | str = RefinementStatus.PENDING,
    ):
        assert_valid_uuid(image_id)
        self.image_id = image_id

        assert_valid_uuid(job_id)
        self.job_id = job_id

        if not isinstance(s3_bucket, str):
            raise ValueError("s3_bucket must be a string")
        self.s3_bucket = s3_bucket

        if not isinstance(s3_key, str):
            raise ValueError("s3_key must be a string")
        self.s3_key = s3_key

        if not isinstance(created_at, datetime):
            raise ValueError("created_at must be a datetime")
        self.created_at = created_at

        if updated_at is not None and not isinstance(updated_at, datetime):
            raise ValueError("updated_at must be a datetime or None")
        self.updated_at = updated_at

        if not isinstance(status, RefinementStatus):
            if not isinstance(status, str):
                raise ValueError(
                    "status must be a RefinementStatus or a string"
                )
            if status not in [s.value for s in RefinementStatus]:
                raise ValueError(
                    f"status must be one of: {', '.join(s.value for s in RefinementStatus)}\nGot: {status}"
                )
        if isinstance(status, RefinementStatus):
            self.status = status.value
        else:
            self.status = status

    def key(self) -> dict:
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"REFINEMENT_JOB#{self.job_id}"},
        }

    def gsi1_key(self) -> dict:
        return {
            "GSI1PK": {"S": f"REFINEMENT_JOB_STATUS#{self.status}"},
            "GSI1SK": {"S": f"REFINEMENT_JOB#{self.job_id}"},
        }

    def gsi2_key(self) -> dict:
        return {
            "GSI2PK": {"S": f"REFINEMENT_JOB_STATUS#{self.status}"},
            "GSI2SK": {"S": f"REFINEMENT_JOB#{self.job_id}"},
        }

    def to_item(self) -> dict:
        return {
            **self.key(),
            **self.gsi1_key(),
            **self.gsi2_key(),
            "TYPE": {"S": "REFINEMENT_JOB"},
            "s3_bucket": {"S": self.s3_bucket},
            "s3_key": {"S": self.s3_key},
            "created_at": {"S": self.created_at.isoformat()},
            "updated_at": (
                {"S": self.updated_at.isoformat()}
                if self.updated_at is not None
                else {"NULL": True}
            ),
            "status": {"S": self.status},
        }

    def __repr__(self) -> str:
        return (
            "RefinementJob("
            f"image_id={_repr_str(self.image_id)}, "
            f"job_id={_repr_str(self.job_id)}, "
            f"s3_bucket={_repr_str(self.s3_bucket)}, "
            f"s3_key={_repr_str(self.s3_key)}, "
            f"created_at={self.created_at}, "
            f"updated_at={self.updated_at}, "
            f"status={_repr_str(self.status)}"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        yield "image_id", self.image_id
        yield "job_id", self.job_id
        yield "s3_bucket", self.s3_bucket
        yield "s3_key", self.s3_key
        yield "created_at", self.created_at
        yield "updated_at", self.updated_at
        yield "status", self.status

    def __eq__(self, other) -> bool:
        if not isinstance(other, RefinementJob):
            return False
        return (
            self.image_id == other.image_id
            and self.job_id == other.job_id
            and self.s3_bucket == other.s3_bucket
            and self.s3_key == other.s3_key
            and self.created_at == other.created_at
            and self.updated_at == other.updated_at
            and self.status == other.status
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.image_id,
                self.job_id,
                self.s3_bucket,
                self.s3_key,
                self.created_at,
                self.updated_at,
                self.status,
            )
        )


def itemToRefinementJob(item: dict) -> RefinementJob:
    """Converts a DynamoDB item to a RefinementJob object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        RefinementJob: The RefinementJob object.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {
        "PK",
        "SK",
        "TYPE",
        "s3_bucket",
        "s3_key",
        "created_at",
        "status",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}"
            f"\nadditional keys: {additional_keys}"
        )
    try:
        sk_parts = item["SK"]["S"].split("#")
        image_id = item["PK"]["S"].split("#")[1]
        job_id = sk_parts[1]
        s3_bucket = item["s3_bucket"]["S"]
        s3_key = item["s3_key"]["S"]
        created_at = datetime.fromisoformat(item["created_at"]["S"])
        updated_at = (
            datetime.fromisoformat(item["updated_at"]["S"])
            if "updated_at" in item
            else None
        )
        status = item["status"]["S"]
        return RefinementJob(
            image_id=image_id,
            job_id=job_id,
            s3_bucket=s3_bucket,
            s3_key=s3_key,
            created_at=created_at,
            updated_at=updated_at,
            status=status,
        )
    except Exception as e:
        raise ValueError(f"Error converting item to RefinementJob: {e}") from e
