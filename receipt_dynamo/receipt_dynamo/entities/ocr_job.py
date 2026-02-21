from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generator

from receipt_dynamo.constants import OCRJobType, OCRStatus
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
class OCRJob:
    """
    Represents an OCR job in DynamoDB.
    """

    REQUIRED_KEYS = {
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

    image_id: str
    job_id: str
    s3_bucket: str
    s3_key: str
    created_at: datetime
    updated_at: datetime | None = None
    status: str = OCRStatus.PENDING.value
    job_type: str = OCRJobType.FIRST_PASS.value
    receipt_id: int | None = None
    reocr_region: dict[str, float] | None = None
    reocr_reason: str | None = None

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

        if self.updated_at is not None and not isinstance(
            self.updated_at, datetime
        ):
            raise ValueError("updated_at must be a datetime or None")

        self.status = normalize_enum(self.status, OCRStatus)
        self.job_type = normalize_enum(self.job_type, OCRJobType)

        if self.receipt_id is not None and not isinstance(
            self.receipt_id, int
        ):
            raise ValueError("receipt_id must be an integer or None")

        if self.reocr_region is not None:
            if not isinstance(self.reocr_region, dict):
                raise ValueError("reocr_region must be a dict or None")
            required = {"x", "y", "width", "height"}
            if set(self.reocr_region.keys()) != required:
                raise ValueError(
                    "reocr_region must contain exactly: x, y, width, height"
                )
            for key in required:
                value = self.reocr_region[key]
                if not isinstance(value, int | float):
                    raise ValueError(
                        f"reocr_region[{key}] must be numeric, got {type(value)}"
                    )
            if self.reocr_region["width"] <= 0 or self.reocr_region["height"] <= 0:
                raise ValueError(
                    "reocr_region width and height must be greater than 0"
                )

        if self.reocr_reason is not None and not isinstance(
            self.reocr_reason, str
        ):
            raise ValueError("reocr_reason must be a string or None")

    @property
    def key(self) -> dict[str, Any]:
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"OCR_JOB#{self.job_id}"},
        }

    def gsi1_key(self) -> dict[str, Any]:
        return {
            "GSI1PK": {"S": f"OCR_JOB_STATUS#{self.status}"},
            "GSI1SK": {"S": f"OCR_JOB#{self.job_id}"},
        }

    def gsi2_key(self) -> dict[str, Any]:
        return {
            "GSI2PK": {"S": f"OCR_JOB_STATUS#{self.status}"},
            "GSI2SK": {"S": f"OCR_JOB#{self.job_id}"},
        }

    def to_item(self) -> dict[str, Any]:
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
            "reocr_region": (
                {
                    "M": {
                        key: {"N": str(float(value))}
                        for key, value in self.reocr_region.items()
                    }
                }
                if self.reocr_region is not None
                else {"NULL": True}
            ),
            "reocr_reason": (
                {"S": self.reocr_reason}
                if self.reocr_reason is not None
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
            f"receipt_id={self.receipt_id}, "
            f"reocr_region={self.reocr_region}, "
            f"reocr_reason={_repr_str(self.reocr_reason)}"
            ")"
        )

    def __iter__(self) -> Generator[tuple[str, Any], None, None]:
        yield "image_id", self.image_id
        yield "job_id", self.job_id
        yield "s3_bucket", self.s3_bucket
        yield "s3_key", self.s3_key
        yield "created_at", self.created_at
        yield "updated_at", self.updated_at
        yield "status", self.status
        yield "job_type", self.job_type
        yield "receipt_id", self.receipt_id
        yield "reocr_region", self.reocr_region
        yield "reocr_reason", self.reocr_reason

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
            and self.reocr_region == other.reocr_region
            and self.reocr_reason == other.reocr_reason
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
                (
                    tuple(sorted(self.reocr_region.items()))
                    if self.reocr_region is not None
                    else None
                ),
                self.reocr_reason,
            )
        )

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "OCRJob":
        """Converts a DynamoDB item to an OCRJob object.

        Args:
            item: The DynamoDB item to convert.

        Returns:
            OCRJob: The OCRJob object.

        Raises:
            ValueError: When the item format is invalid.
        """
        # OCRJob-specific extractors (in addition to common OCR extractors)
        def _extract_reocr_region(item_dict: dict[str, Any]) -> dict[str, float] | None:
            if "reocr_region" not in item_dict:
                return None
            raw_region = item_dict["reocr_region"]
            if raw_region.get("NULL"):
                return None
            region_map = raw_region.get("M")
            if not isinstance(region_map, dict):
                raise ValueError("reocr_region must be a map (M) or NULL")
            required = ("x", "y", "width", "height")
            parsed: dict[str, float] = {}
            for key in required:
                value = region_map.get(key, {}).get("N")
                if value is None:
                    raise ValueError(f"reocr_region missing numeric field: {key}")
                parsed[key] = float(value)
            return parsed

        def _extract_optional_string(field_name: str):
            def _extract(item_dict: dict[str, Any]) -> str | None:
                if field_name not in item_dict:
                    return None
                if item_dict[field_name].get("NULL"):
                    return None
                return item_dict[field_name].get("S")

            return _extract

        custom_extractors = {
            **create_ocr_job_extractors(),
            "job_type": EntityFactory.extract_string_field("job_type"),
            "receipt_id": EntityFactory.extract_int_field("receipt_id"),
            "reocr_region": _extract_reocr_region,
            "reocr_reason": _extract_optional_string("reocr_reason"),
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


def item_to_ocr_job(item: dict[str, Any]) -> OCRJob:
    """Converts a DynamoDB item to an OCRJob object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        OCRJob: The OCRJob object.

    Raises:
        ValueError: When the item format is invalid.
    """
    return OCRJob.from_item(item)
