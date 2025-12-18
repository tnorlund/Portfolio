from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Generator, Optional, Tuple

from receipt_dynamo.constants import OCRJobType, OCRStatus
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
    normalize_enum,
)


@dataclass(eq=True, unsafe_hash=False)
class OCRJob:
    """
    Represents an OCR job in DynamoDB.
    """

    image_id: str
    job_id: str
    s3_bucket: str
    s3_key: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    status: str = OCRStatus.PENDING.value
    job_type: str = OCRJobType.FIRST_PASS.value
    receipt_id: Optional[int] = None

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        assert_valid_uuid(self.image_id)
        assert_valid_uuid(self.job_id)

        if not isinstance(self.s3_bucket, str):
            raise ValueError("s3_bucket must be a string")

        if not isinstance(self.s3_key, str):
            raise ValueError("s3_key must be a string")

        if not isinstance(self.created_at, datetime):
            raise ValueError("created_at must be a datetime")

        if self.updated_at is not None and not isinstance(self.updated_at, datetime):
            raise ValueError("updated_at must be a datetime or None")

        self.status = normalize_enum(self.status, OCRStatus)
        self.job_type = normalize_enum(self.job_type, OCRJobType)

        if self.receipt_id is not None and not isinstance(self.receipt_id, int):
            raise ValueError("receipt_id must be an integer or None")

    @property
    def key(self) -> Dict[str, Any]:
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"OCR_JOB#{self.job_id}"},
        }

    def gsi1_key(self) -> Dict[str, Any]:
        return {
            "GSI1PK": {"S": f"OCR_JOB_STATUS#{self.status}"},
            "GSI1SK": {"S": f"OCR_JOB#{self.job_id}"},
        }

    def gsi2_key(self) -> Dict[str, Any]:
        return {
            "GSI2PK": {"S": f"OCR_JOB_STATUS#{self.status}"},
            "GSI2SK": {"S": f"OCR_JOB#{self.job_id}"},
        }

    def to_item(self) -> Dict[str, Any]:
        return {
            **self.key,
            **self.gsi1_key(),
            **self.gsi2_key(),
            "TYPE": {"S": "OCR_JOB"},
            "s3_bucket": {"S": self.s3_bucket},
            "s3_key": {"S": self.s3_key},
            "created_at": {"S": self.created_at.isoformat()},
            "updated_at": (
                {"S": self.updated_at.isoformat()}
                if self.updated_at is not None
                else {"NULL": True}
            ),
            "status": {"S": self.status},
            "job_type": {"S": self.job_type},
            "receipt_id": (
                {"N": str(self.receipt_id)}
                if self.receipt_id is not None
                else {"NULL": True}
            ),
        }

    def __repr__(self) -> str:
        return (
            "OCRJob("
            f"image_id={_repr_str(self.image_id)}, "
            f"job_id={_repr_str(self.job_id)}, "
            f"s3_bucket={_repr_str(self.s3_bucket)}, "
            f"s3_key={_repr_str(self.s3_key)}, "
            f"created_at={self.created_at}, "
            f"updated_at={self.updated_at}, "
            f"status={_repr_str(self.status)}, "
            f"job_type={_repr_str(self.job_type)}, "
            f"receipt_id={self.receipt_id}"
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
        yield "job_type", self.job_type
        yield "receipt_id", self.receipt_id

    def __eq__(self, other) -> bool:
        if not isinstance(other, OCRJob):
            return False
        return (
            self.image_id == other.image_id
            and self.job_id == other.job_id
            and self.s3_bucket == other.s3_bucket
            and self.s3_key == other.s3_key
            and self.created_at == other.created_at
            and self.updated_at == other.updated_at
            and self.status == other.status
            and self.job_type == other.job_type
            and self.receipt_id == other.receipt_id
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
                self.job_type,
                self.receipt_id,
            )
        )


def item_to_ocr_job(item: Dict[str, Any]) -> OCRJob:
    """Converts a DynamoDB item to a OCRJob object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        OCRJob: The OCRJob object.

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
        "job_type",
        "receipt_id",
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
            if "updated_at" in item and "S" in item["updated_at"]
            else None
        )
        status = item["status"]["S"]
        job_type = item["job_type"]["S"]
        receipt_id = (
            int(item["receipt_id"]["N"])
            if "receipt_id" in item and "N" in item["receipt_id"]
            else None
        )
        return OCRJob(
            image_id=image_id,
            job_id=job_id,
            s3_bucket=s3_bucket,
            s3_key=s3_key,
            created_at=created_at,
            updated_at=updated_at,
            status=status,
            job_type=job_type,
            receipt_id=receipt_id,
        )
    except Exception as e:
        raise ValueError(f"Error converting item to OCRJob: {e}") from e
