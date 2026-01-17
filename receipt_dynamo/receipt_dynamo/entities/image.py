# infra/lambda_layer/python/dynamo/entities/image.py
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from receipt_dynamo.constants import ImageType
from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.entity_mixins import CDNFieldsMixin
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
    validate_positive_dimensions,
)


@dataclass(eq=True, unsafe_hash=False)
class Image(DynamoDBEntity, CDNFieldsMixin):
    """
    Represents an image and its associated metadata stored in a DynamoDB table.

    This class encapsulates image-related information such as its unique
    identifier, dimensions, upload timestamp, and S3 storage details. It is
    designed to support operations such as generating DynamoDB keys and
    converting image metadata to a DynamoDB-compatible item.

    Attributes:
        image_id (str): UUID identifying the image.
        width (int): The width of the image in pixels.
        height (int): The height of the image in pixels.
        timestamp_added (datetime): The timestamp when the image was added.
        raw_s3_bucket (str): The S3 bucket where the image is initially stored.
        raw_s3_key (str): The S3 key where the image is initially stored.
        sha256 (str): The SHA256 hash of the image.
        cdn_s3_bucket (str): The S3 bucket where the image is stored in the
            CDN.
        cdn_s3_key (str): The S3 key where the image is stored in the CDN.
        cdn_webp_s3_key (str, optional): The S3 key for the WebP version in
            the CDN.
        cdn_avif_s3_key (str, optional): The S3 key for the AVIF version in
            the CDN.
        image_type (ImageType): The type of image.
    """

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "TYPE",
        "width",
        "height",
        "timestamp_added",
        "raw_s3_bucket",
        "raw_s3_key",
        "image_type",
    }

    # Required fields
    image_id: str
    width: int
    height: int
    timestamp_added: str | datetime
    raw_s3_bucket: str
    raw_s3_key: str
    # Optional fields (CDN fields inherited from CDNFieldsMixin)
    image_type: ImageType | str = ImageType.SCAN
    receipt_count: Optional[int] = None  # Only for query filtering

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        assert_valid_uuid(self.image_id)
        validate_positive_dimensions(self.width, self.height)

        if isinstance(self.timestamp_added, datetime):
            self.timestamp_added = self.timestamp_added.isoformat()
        elif not isinstance(self.timestamp_added, str):
            raise ValueError(
                "timestamp_added must be a datetime object or a string"
            )

        if self.raw_s3_bucket and not isinstance(self.raw_s3_bucket, str):
            raise ValueError("raw_s3_bucket must be a string")

        if self.raw_s3_key and not isinstance(self.raw_s3_key, str):
            raise ValueError("raw_s3_key must be a string")

        # Use CDNFieldsMixin to validate sha256 and all CDN fields
        if self.sha256 and not isinstance(self.sha256, str):
            raise ValueError("sha256 must be a string")
        self.validate_cdn_fields()

        if isinstance(self.image_type, ImageType):
            self.image_type = self.image_type.value
        elif isinstance(self.image_type, str):
            if self.image_type not in [t.value for t in ImageType]:
                raise ValueError(
                    "image_type must be one of: "
                    f"{', '.join(t.value for t in ImageType)}\n"
                    f"Got: {self.image_type}"
                )
        else:
            raise ValueError("image_type must be a ImageType or a string")

        if self.receipt_count is not None:
            if not isinstance(self.receipt_count, int):
                raise ValueError("receipt_count must be an integer")
            if self.receipt_count < 0:
                raise ValueError(
                    "receipt_count must be a non-negative integer"
                )

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the image.

        Returns:
            dict: The primary key for the image.
        """
        return {"PK": {"S": f"IMAGE#{self.image_id}"}, "SK": {"S": "IMAGE"}}

    @property
    def gsi1_key(self) -> Dict[str, Any]:
        """Generates the GSI1 key for the image.

        Returns:
            dict: The GSI1 key for the image.
        """
        return {
            "GSI1PK": {"S": f"IMAGE#{self.image_id}"},
            "GSI1SK": {"S": "IMAGE"},
        }

    @property
    def gsi2_key(self) -> Dict[str, Any]:
        """Generates the GSI2 key for the image.

        Returns:
            dict: The GSI2 key for the image.
        """
        return {
            "GSI2PK": {"S": f"IMAGE#{self.image_id}"},
            "GSI2SK": {"S": "IMAGE"},
        }

    @property
    def gsi3_key(self) -> Dict[str, Any]:
        """Generates the GSI3 key for the image.

        Returns:
            dict: The GSI3 key for the image.
        """
        receipt_count_str = (
            f"{self.receipt_count:05d}"
            if self.receipt_count is not None
            else "00000"
        )
        return {
            "GSI3PK": {"S": f"IMAGE#{self.image_type}"},
            "GSI3SK": {"S": f"NUM_RECEIPTS#{receipt_count_str}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the Image object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the Image object as a DynamoDB
                item.
        """
        return {
            **self.key,
            **self.gsi1_key,
            **self.gsi2_key,
            **self.gsi3_key,
            "TYPE": {"S": "IMAGE"},
            "width": {"N": str(self.width)},
            "height": {"N": str(self.height)},
            "timestamp_added": {"S": self.timestamp_added},
            "raw_s3_bucket": {"S": self.raw_s3_bucket},
            "raw_s3_key": {"S": self.raw_s3_key},
            "sha256": {"S": self.sha256} if self.sha256 else {"NULL": True},
            "cdn_s3_bucket": (
                {"S": self.cdn_s3_bucket}
                if self.cdn_s3_bucket
                else {"NULL": True}
            ),
            **self.cdn_fields_to_dynamodb_item(),
            "image_type": {"S": self.image_type},
            "receipt_count": (
                {"N": str(self.receipt_count)}
                if self.receipt_count is not None
                else {"NULL": True}
            ),
        }

    def __repr__(self) -> str:
        return (
            "Image("
            f"image_id={_repr_str(self.image_id)}, "
            f"width={self.width}, "
            f"height={self.height}, "
            f"timestamp_added={self.timestamp_added}, "
            f"raw_s3_bucket={_repr_str(self.raw_s3_bucket)}, "
            f"raw_s3_key={_repr_str(self.raw_s3_key)}, "
            f"{self._get_cdn_repr_fields()}, "
            f"image_type={_repr_str(self.image_type)}, "
            f"receipt_count={_repr_str(self.receipt_count)}"
            ")"
        )

    @classmethod
    def from_item(cls, item: Dict[str, Any]) -> "Image":
        """Converts a DynamoDB item to an Image object.

        Args:
            item: The DynamoDB item to convert.

        Returns:
            Image: The Image object represented by the DynamoDB item.

        Raises:
            ValueError: When the item format is invalid.
        """
        missing_keys = DynamoDBEntity.validate_keys(item, cls.REQUIRED_KEYS)
        if missing_keys:
            additional_keys = set(item.keys()) - cls.REQUIRED_KEYS
            raise ValueError(
                f"Invalid item format\nmissing keys: {missing_keys}\n"
                f"additional keys: {additional_keys}"
            )

        try:
            image_type = item.get("image_type", {}).get("S")
            return cls(
                image_id=item["PK"]["S"].split("#")[1],
                width=int(item["width"]["N"]),
                height=int(item["height"]["N"]),
                timestamp_added=datetime.fromisoformat(
                    item["timestamp_added"]["S"]
                ),
                raw_s3_bucket=item["raw_s3_bucket"]["S"],
                raw_s3_key=item["raw_s3_key"]["S"],
                image_type=image_type if image_type else ImageType.SCAN.value,
                receipt_count=(
                    int(item["receipt_count"]["N"])
                    if "receipt_count" in item and "N" in item["receipt_count"]
                    else None
                ),
                **cls._cdn_fields_from_item(item),
            )
        except KeyError as e:
            raise ValueError(f"Error converting item to Image: {e}") from e


def item_to_image(item: Dict[str, Any]) -> Image:
    """Converts a DynamoDB item to an Image object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        Image: The Image object represented by the DynamoDB item.

    Raises:
        ValueError: When the item format is invalid.
    """
    return Image.from_item(item)
