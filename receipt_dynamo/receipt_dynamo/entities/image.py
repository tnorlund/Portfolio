# infra/lambda_layer/python/dynamo/entities/image.py
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from receipt_dynamo.constants import ImageType
from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid


@dataclass(eq=True, unsafe_hash=True)
class Image(DynamoDBEntity):
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

    image_id: str
    width: int
    height: int
    timestamp_added: str | datetime
    raw_s3_bucket: str
    raw_s3_key: str
    sha256: Optional[str] = None
    cdn_s3_bucket: Optional[str] = None
    cdn_s3_key: Optional[str] = None
    cdn_webp_s3_key: Optional[str] = None
    cdn_avif_s3_key: Optional[str] = None
    image_type: ImageType | str = ImageType.SCAN

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        assert_valid_uuid(self.image_id)
        if (
            not isinstance(self.width, int)
            or not isinstance(self.height, int)
            or self.width <= 0
            or self.height <= 0
        ):
            raise ValueError("width and height must be positive integers")

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

        if self.sha256 and not isinstance(self.sha256, str):
            raise ValueError("sha256 must be a string")

        if self.cdn_s3_bucket and not isinstance(self.cdn_s3_bucket, str):
            raise ValueError("cdn_s3_bucket must be a string")

        if self.cdn_s3_key and not isinstance(self.cdn_s3_key, str):
            raise ValueError("cdn_s3_key must be a string")

        if self.cdn_webp_s3_key and not isinstance(self.cdn_webp_s3_key, str):
            raise ValueError("cdn_webp_s3_key must be a string")

        if self.cdn_avif_s3_key and not isinstance(self.cdn_avif_s3_key, str):
            raise ValueError("cdn_avif_s3_key must be a string")

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
        return {
            "GSI3PK": {"S": f"IMAGE#{self.image_type}"},
            "GSI3SK": {"S": f"IMAGE#{self.image_id}"},
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
            "cdn_s3_key": (
                {"S": self.cdn_s3_key} if self.cdn_s3_key else {"NULL": True}
            ),
            "cdn_webp_s3_key": (
                {"S": self.cdn_webp_s3_key}
                if self.cdn_webp_s3_key
                else {"NULL": True}
            ),
            "cdn_avif_s3_key": (
                {"S": self.cdn_avif_s3_key}
                if self.cdn_avif_s3_key
                else {"NULL": True}
            ),
            "image_type": {"S": self.image_type},
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
            f"sha256={_repr_str(self.sha256)}, "
            f"cdn_s3_bucket={_repr_str(self.cdn_s3_bucket)}, "
            f"cdn_s3_key={_repr_str(self.cdn_s3_key)}, "
            f"cdn_webp_s3_key={_repr_str(self.cdn_webp_s3_key)}, "
            f"cdn_avif_s3_key={_repr_str(self.cdn_avif_s3_key)}, "
            f"image_type={_repr_str(self.image_type)}"
            ")"
        )


def item_to_image(item: Dict[str, Any]) -> Image:
    """Converts a DynamoDB item to an Image object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        Image: The Image object represented by the DynamoDB item.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {
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
    missing_keys = DynamoDBEntity.validate_keys(item, required_keys)
    if missing_keys:
        additional_keys = set(item.keys()) - required_keys
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}\n"
            f"additional keys: {additional_keys}"
        )
    try:
        sha256 = item.get("sha256", {}).get("S")
        cdn_s3_bucket = item.get("cdn_s3_bucket", {}).get("S")
        cdn_s3_key = item.get("cdn_s3_key", {}).get("S")
        cdn_webp_s3_key = item.get("cdn_webp_s3_key", {}).get("S")
        cdn_avif_s3_key = item.get("cdn_avif_s3_key", {}).get("S")
        image_type = item.get("image_type", {}).get("S")
        return Image(
            image_id=item["PK"]["S"].split("#")[1],
            width=int(item["width"]["N"]),
            height=int(item["height"]["N"]),
            timestamp_added=datetime.fromisoformat(
                item["timestamp_added"]["S"]
            ),
            raw_s3_bucket=item["raw_s3_bucket"]["S"],
            raw_s3_key=item["raw_s3_key"]["S"],
            sha256=sha256 if sha256 else None,
            cdn_s3_bucket=cdn_s3_bucket if cdn_s3_bucket else None,
            cdn_s3_key=cdn_s3_key if cdn_s3_key else None,
            cdn_webp_s3_key=cdn_webp_s3_key if cdn_webp_s3_key else None,
            cdn_avif_s3_key=cdn_avif_s3_key if cdn_avif_s3_key else None,
            image_type=image_type if image_type else ImageType.SCAN.value,
        )
    except KeyError as e:
        raise ValueError(f"Error converting item to Image: {e}") from e
