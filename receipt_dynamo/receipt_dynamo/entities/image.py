# infra/lambda_layer/python/dynamo/entities/image.py
from datetime import datetime
from typing import Any, Generator, Optional, Tuple

from receipt_dynamo.constants import ImageType
from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid


class Image:
    """
    Represents an image and its associated metadata stored in a DynamoDB table.

    This class encapsulates image-related information such as its unique identifier,
    dimensions, upload timestamp, and S3 storage details. It is designed to support
    operations such as generating DynamoDB keys and converting image metadata to a
    DynamoDB-compatible item.

    Attributes:
        image_id (str): UUID identifying the image.
        width (int): The width of the image in pixels.
        height (int): The height of the image in pixels.
        timestamp_added (datetime): The timestamp when the image was added.
        raw_s3_bucket (str): The S3 bucket where the image is initially stored.
        raw_s3_key (str): The S3 key where the image is initially stored.
        sha256 (str): The SHA256 hash of the image.
        cdn_s3_bucket (str): The S3 bucket where the image is stored in the CDN.
        cdn_s3_key (str): The S3 key where the image is stored in the CDN.
        cdn_webp_s3_key (str, optional): The S3 key for the WebP version in the CDN.
        cdn_avif_s3_key (str, optional): The S3 key for the AVIF version in the CDN.
    """

    def __init__(
        self,
        image_id: str,
        width: int,
        height: int,
        timestamp_added: datetime,
        raw_s3_bucket: str,
        raw_s3_key: str,
        sha256: Optional[str] = None,
        cdn_s3_bucket: Optional[str] = None,
        cdn_s3_key: Optional[str] = None,
        cdn_webp_s3_key: Optional[str] = None,
        cdn_avif_s3_key: Optional[str] = None,
        image_type: ImageType | str = ImageType.SCAN,
    ):
        """Initializes a new Image object for DynamoDB.

        Args:
            image_id (str): UUID identifying the image.
            width (int): The width of the image in pixels.
            height (int): The height of the image in pixels.
            timestamp_added (datetime): The timestamp when the image was added.
            raw_s3_bucket (str): The S3 bucket where the image is initially stored.
            raw_s3_key (str): The S3 key where the image is initially stored.
            sha256 (str, optional): The SHA256 hash of the image.
            cdn_s3_bucket (str, optional): The S3 bucket where the image is stored in the CDN.
            cdn_s3_key (str, optional): The S3 key where the image is stored in the CDN.
            cdn_webp_s3_key (str, optional): The S3 key for the WebP version in the CDN.
            cdn_avif_s3_key (str, optional): The S3 key for the AVIF version in the CDN.

        Raises:
            ValueError: If any parameter is of an invalid type or has an invalid value.
        """
        assert_valid_uuid(image_id)
        self.image_id = image_id

        if (
            width <= 0
            or height <= 0
            or not isinstance(width, int)
            or not isinstance(height, int)
        ):
            raise ValueError("width and height must be positive integers")
        self.width = width
        self.height = height

        if isinstance(timestamp_added, datetime):
            self.timestamp_added = timestamp_added.isoformat()
        elif isinstance(timestamp_added, str):
            self.timestamp_added = timestamp_added
        else:
            raise ValueError("timestamp_added must be a datetime object or a string")

        if raw_s3_bucket and not isinstance(raw_s3_bucket, str):
            raise ValueError("raw_s3_bucket must be a string")
        self.raw_s3_bucket = raw_s3_bucket
        if raw_s3_key and not isinstance(raw_s3_key, str):
            raise ValueError("raw_s3_key must be a string")
        self.raw_s3_key = raw_s3_key

        if sha256 and not isinstance(sha256, str):
            raise ValueError("sha256 must be a string")
        self.sha256 = sha256

        if cdn_s3_bucket and not isinstance(cdn_s3_bucket, str):
            raise ValueError("cdn_s3_bucket must be a string")
        self.cdn_s3_bucket = cdn_s3_bucket

        if cdn_s3_key and not isinstance(cdn_s3_key, str):
            raise ValueError("cdn_s3_key must be a string")
        self.cdn_s3_key = cdn_s3_key

        if cdn_webp_s3_key and not isinstance(cdn_webp_s3_key, str):
            raise ValueError("cdn_webp_s3_key must be a string")
        self.cdn_webp_s3_key = cdn_webp_s3_key

        if cdn_avif_s3_key and not isinstance(cdn_avif_s3_key, str):
            raise ValueError("cdn_avif_s3_key must be a string")
        self.cdn_avif_s3_key = cdn_avif_s3_key

        if not isinstance(image_type, ImageType):
            if not isinstance(image_type, str):
                raise ValueError("image_type must be a ImageType or a string")
            if image_type not in [t.value for t in ImageType]:
                raise ValueError(
                    f"image_type must be one of: {', '.join(t.value for t in ImageType)}\nGot: {image_type}"
                )
        if isinstance(image_type, ImageType):
            self.image_type = image_type.value
        else:
            self.image_type = image_type

    def key(self) -> dict:
        """Generates the primary key for the image.

        Returns:
            dict: The primary key for the image.
        """
        return {"PK": {"S": f"IMAGE#{self.image_id}"}, "SK": {"S": "IMAGE"}}

    def gsi1_key(self) -> dict:
        """Generates the GSI1 key for the image.

        Returns:
            dict: The GSI1 key for the image.
        """
        return {
            "GSI1PK": {"S": f"IMAGE#{self.image_id}"},
            "GSI1SK": {"S": f"IMAGE"},
        }

    def gsi2_key(self) -> dict:
        """Generates the GSI2 key for the image.

        Returns:
            dict: The GSI2 key for the image.
        """
        return {
            "GSI2PK": {"S": f"IMAGE#{self.image_id}"},
            "GSI2SK": {"S": "IMAGE"},
        }

    def gsi3_key(self) -> dict:
        """Generates the GSI3 key for the image.

        Returns:
            dict: The GSI3 key for the image.
        """
        return {
            "GSI3PK": {"S": f"IMAGE#{self.image_type}"},
            "GSI3SK": {"S": f"IMAGE#{self.image_id}"},
        }

    def to_item(self) -> dict:
        """Converts the Image object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the Image object as a DynamoDB item.
        """
        return {
            **self.key(),
            **self.gsi1_key(),
            **self.gsi2_key(),
            **self.gsi3_key(),
            "TYPE": {"S": "IMAGE"},
            "width": {"N": str(self.width)},
            "height": {"N": str(self.height)},
            "timestamp_added": {"S": self.timestamp_added},
            "raw_s3_bucket": {"S": self.raw_s3_bucket},
            "raw_s3_key": {"S": self.raw_s3_key},
            "sha256": {"S": self.sha256} if self.sha256 else {"NULL": True},
            "cdn_s3_bucket": (
                {"S": self.cdn_s3_bucket} if self.cdn_s3_bucket else {"NULL": True}
            ),
            "cdn_s3_key": (
                {"S": self.cdn_s3_key} if self.cdn_s3_key else {"NULL": True}
            ),
            "cdn_webp_s3_key": (
                {"S": self.cdn_webp_s3_key} if self.cdn_webp_s3_key else {"NULL": True}
            ),
            "cdn_avif_s3_key": (
                {"S": self.cdn_avif_s3_key} if self.cdn_avif_s3_key else {"NULL": True}
            ),
            "image_type": {"S": self.image_type},
        }

    def __repr__(self) -> str:
        """Returns a string representation of the Image object.

        Returns:
            str: A string representation of the Image object.
        """
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
            f"cdn_webp_s3_key={_repr_str(self.cdn_webp_s3_key) if self.cdn_webp_s3_key else None}, "
            f"cdn_avif_s3_key={_repr_str(self.cdn_avif_s3_key) if self.cdn_avif_s3_key else None}, "
            f"image_type={_repr_str(self.image_type)}"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the Image object's attributes.

        Returns:
            Generator[Tuple[str, Any], None, None]: An iterator over the Image object's attribute name/value pairs.
        """
        yield "image_id", self.image_id
        yield "width", self.width
        yield "height", self.height
        yield "timestamp_added", self.timestamp_added
        yield "raw_s3_bucket", self.raw_s3_bucket
        yield "raw_s3_key", self.raw_s3_key
        yield "sha256", self.sha256
        yield "cdn_s3_bucket", self.cdn_s3_bucket
        yield "cdn_s3_key", self.cdn_s3_key
        yield "cdn_webp_s3_key", self.cdn_webp_s3_key
        yield "cdn_avif_s3_key", self.cdn_avif_s3_key
        yield "image_type", self.image_type

    def to_dict(self) -> dict:
        """Return a dictionary representation of the Image."""
        return {k: v for k, v in self}

    def __eq__(self, other) -> bool:
        """Determines whether two Image objects are equal.

        Args:
            other (Image): The other Image object to compare.

        Returns:
            bool: True if the Image objects are equal, False otherwise.

        Note:
            If other is not an instance of Image, False is returned.
        """
        if not isinstance(other, Image):
            return False
        return (
            self.image_id == other.image_id
            and self.width == other.width
            and self.height == other.height
            and self.timestamp_added == other.timestamp_added
            and self.raw_s3_bucket == other.raw_s3_bucket
            and self.raw_s3_key == other.raw_s3_key
            and self.sha256 == other.sha256
            and self.cdn_s3_bucket == other.cdn_s3_bucket
            and self.cdn_s3_key == other.cdn_s3_key
            and self.cdn_webp_s3_key == other.cdn_webp_s3_key
            and self.cdn_avif_s3_key == other.cdn_avif_s3_key
            and self.image_type == other.image_type
        )

    def __hash__(self) -> int:
        """Returns the hash value of the Image object.

        Returns:
            int: The hash value of the Image object.
        """
        return hash(
            (
                self.image_id,
                self.width,
                self.height,
                self.timestamp_added,
                self.raw_s3_bucket,
                self.raw_s3_key,
                self.sha256,
                self.cdn_s3_bucket,
                self.cdn_s3_key,
                self.cdn_webp_s3_key,
                self.cdn_avif_s3_key,
                self.image_type,
            )
        )


def item_to_image(item: dict) -> Image:
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
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}\nadditional keys: {additional_keys}"
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
            timestamp_added=datetime.fromisoformat(item["timestamp_added"]["S"]),
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
        raise ValueError(f"Error converting item to Image: {e}")
