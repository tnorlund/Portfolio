from datetime import datetime
from typing import Any, Dict

from receipt_dynamo.constants import OCRStatus
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
    normalize_enum,
)


class OCRRoutingDecision:
    def __init__(
        self,
        image_id: str,
        job_id: str,
        s3_bucket: str,
        s3_key: str,
        created_at: datetime | str,
        updated_at: datetime | str | None,
        receipt_count: int,
        status: OCRStatus | str = OCRStatus.PENDING,
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

        if not isinstance(created_at, (datetime, str)):
            raise ValueError("created_at must be a datetime or a string")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        self.created_at: datetime = created_at

        if not isinstance(updated_at, (datetime, str)):
            raise ValueError("updated_at must be a datetime or a string")
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)
        self.updated_at: datetime = updated_at

        if not isinstance(receipt_count, int):
            raise ValueError("receipt_count must be an integer")
        self.receipt_count = receipt_count

        self.status: str = normalize_enum(status, OCRStatus)

    @property
    def key(self) -> Dict[str, Any]:
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"ROUTING#{self.job_id}"},
        }

    def gsi1_key(self) -> Dict[str, Any]:
        return {
            "GSI1PK": {"S": f"OCR_ROUTING_DECISION_STATUS#{self.status}"},
            "GSI1SK": {"S": f"ROUTING#{self.job_id}"},
        }

    def to_item(self) -> Dict[str, Any]:
        return {
            **self.key,
            **self.gsi1_key(),
            "TYPE": {"S": "OCR_ROUTING_DECISION"},
            "s3_bucket": {"S": self.s3_bucket},
            "s3_key": {"S": self.s3_key},
            "created_at": {"S": self.created_at.isoformat()},
            "updated_at": (
                {"S": self.updated_at.isoformat()}
                if self.updated_at is not None
                else {"NULL": True}
            ),
            "receipt_count": {"N": str(self.receipt_count)},
            "status": {"S": self.status},
        }

    def __repr__(self) -> str:
        return (
            f"OCRRoutingDecision(image_id={_repr_str(self.image_id)}, job_id={_repr_str(self.job_id)}, "
            f"s3_bucket={_repr_str(self.s3_bucket)}, s3_key={_repr_str(self.s3_key)}, "
            f"created_at={self.created_at}, updated_at={self.updated_at}, "
            f"receipt_count={self.receipt_count}, status={_repr_str(self.status)})"
        )


def item_to_ocr_routing_decision(item: Dict[str, Any]) -> OCRRoutingDecision:
    """Converts a DynamoDB item to a OCRRoutingDecision object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        OCRRoutingDecision: The OCRRoutingDecision object.

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
        "updated_at",
        "receipt_count",
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
            if "updated_at" in item and "S" in item["updated_at"]
            else None
        )
        receipt_count = int(item["receipt_count"]["N"])
        status = item["status"]["S"]
        return OCRRoutingDecision(
            image_id=image_id,
            job_id=job_id,
            s3_bucket=s3_bucket,
            s3_key=s3_key,
            created_at=created_at,
            updated_at=updated_at,
            receipt_count=receipt_count,
            status=status,
        )
    except Exception as e:
        raise ValueError(
            f"Invalid item format\nitem: {item}\nerror: {e}"
        ) from e
