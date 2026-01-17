from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from receipt_dynamo.constants import OCRStatus
from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.entity_factory import (
    EntityFactory,
    create_image_receipt_pk_parser,
    create_ocr_job_extractors,
    create_ocr_job_sk_parser,
)
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
    normalize_enum,
)


@dataclass(eq=True, unsafe_hash=False)
class OCRRoutingDecision(DynamoDBEntity):
    """
    Represents an OCR routing decision stored in a DynamoDB table.

    This class encapsulates information about OCR job routing decisions,
    including the associated image, job details, S3 location, timestamps,
    receipt count, and processing status.

    Attributes:
        image_id (str): UUID identifying the image.
        job_id (str): UUID identifying the OCR job.
        s3_bucket (str): S3 bucket containing the image.
        s3_key (str): S3 key for the image.
        created_at (datetime): When the routing decision was created.
        updated_at (Optional[datetime]): When the routing decision was last
            updated.
        receipt_count (int): Number of receipts detected.
        status (str): Status of the OCR routing decision.
    """

    REQUIRED_KEYS = {
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

    image_id: str
    job_id: str
    s3_bucket: str
    s3_key: str
    created_at: datetime
    updated_at: Optional[datetime]
    receipt_count: int
    status: str = OCRStatus.PENDING.value

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        assert_valid_uuid(self.image_id)
        assert_valid_uuid(self.job_id)

        if not isinstance(self.s3_bucket, str):
            raise ValueError("s3_bucket must be a string")

        if not isinstance(self.s3_key, str):
            raise ValueError("s3_key must be a string")

        # Handle datetime conversion from string
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
        if not isinstance(self.created_at, datetime):
            raise ValueError("created_at must be a datetime or a string")

        if self.updated_at is not None:
            if isinstance(self.updated_at, str):
                self.updated_at = datetime.fromisoformat(self.updated_at)
            if not isinstance(self.updated_at, datetime):
                raise ValueError("updated_at must be a datetime or a string")

        if not isinstance(self.receipt_count, int):
            raise ValueError("receipt_count must be an integer")

        # Normalize status
        self.status = normalize_enum(self.status, OCRStatus)

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
            f"OCRRoutingDecision(image_id={_repr_str(self.image_id)}, "
            f"job_id={_repr_str(self.job_id)}, "
            f"s3_bucket={_repr_str(self.s3_bucket)}, "
            f"s3_key={_repr_str(self.s3_key)}, "
            f"created_at={self.created_at}, updated_at={self.updated_at}, "
            f"receipt_count={self.receipt_count}, "
            f"status={_repr_str(self.status)})"
        )

    def __hash__(self) -> int:
        """Returns the hash value of the OCRRoutingDecision object."""
        return hash(
            (
                self.image_id,
                self.job_id,
                self.s3_bucket,
                self.s3_key,
                self.created_at,
                self.updated_at,
                self.receipt_count,
                self.status,
            )
        )

    @classmethod
    def from_item(cls, item: Dict[str, Any]) -> "OCRRoutingDecision":
        """Converts a DynamoDB item to an OCRRoutingDecision object.

        Args:
            item: The DynamoDB item to convert.

        Returns:
            OCRRoutingDecision: The OCRRoutingDecision object.

        Raises:
            ValueError: When the item format is invalid.
        """
        # OCRRoutingDecision-specific extractors (in addition to common OCR
        # extractors)
        custom_extractors = {
            **create_ocr_job_extractors(),
            "receipt_count": EntityFactory.extract_int_field("receipt_count"),
        }

        return EntityFactory.create_entity(
            entity_class=cls,
            item=item,
            required_keys=cls.REQUIRED_KEYS,
            key_parsers={
                "PK": create_image_receipt_pk_parser(),
                "SK": create_ocr_job_sk_parser(),
            },
            custom_extractors=custom_extractors,
        )


def item_to_ocr_routing_decision(item: Dict[str, Any]) -> OCRRoutingDecision:
    """Converts a DynamoDB item to an OCRRoutingDecision object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        OCRRoutingDecision: The OCRRoutingDecision object.

    Raises:
        ValueError: When the item format is invalid.
    """
    return OCRRoutingDecision.from_item(item)
