from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.util import (
    _format_float,
    _repr_str,
    assert_valid_point,
    assert_valid_uuid,
)


@dataclass(eq=True, unsafe_hash=False)
class Receipt(DynamoDBEntity):
    """
    Represents a receipt associated with an image in a DynamoDB table.

    This class encapsulates receipt data and related metadata, including
    dimensions, timestamps, and S3 storage details. It provides methods for
    generating primary and secondary (GSI) keys for DynamoDB operations,
    converting the receipt to a DynamoDB item, and iterating over its
    attributes.

    Attributes:
        image_id (str): UUID identifying the associated image.
        id (int): Unique number identifying the receipt.
        width (int): Width of the receipt in pixels.
        height (int): Height of the receipt in pixels.
        timestamp_added (str): ISO formatted timestamp when the receipt
            was added.
        raw_s3_bucket (str): S3 bucket name where the raw receipt is stored.
        raw_s3_key (str): S3 key corresponding to the raw receipt in S3.
        top_left (dict): Coordinates of the top-left corner of the
            receipt's bounding box.
        top_right (dict): Coordinates of the top-right corner of the
            receipt's bounding box.
        bottom_left (dict): Coordinates of the bottom-left corner of the
            receipt's bounding box.
        bottom_right (dict): Coordinates of the bottom-right corner of the
            receipt's bounding box.
        sha256 (str, optional): SHA256 hash of the receipt image,
            if available.
        cdn_s3_bucket (str, optional): S3 bucket name for the CDN-hosted
            receipt image, if available.
        cdn_s3_key (str, optional): S3 key for the CDN-hosted receipt
            image, if available.
        cdn_webp_s3_key (str, optional): S3 key for the WebP version of the
            CDN-hosted receipt image.
        cdn_avif_s3_key (str, optional): S3 key for the AVIF version of the
            CDN-hosted receipt image.
    """

    image_id: str
    receipt_id: int
    width: int
    height: int
    timestamp_added: str | datetime
    raw_s3_bucket: str
    raw_s3_key: str
    top_left: Dict[str, Any]
    top_right: Dict[str, Any]
    bottom_left: Dict[str, Any]
    bottom_right: Dict[str, Any]
    sha256: Optional[str] = None
    cdn_s3_bucket: Optional[str] = None
    cdn_s3_key: Optional[str] = None
    cdn_webp_s3_key: Optional[str] = None
    cdn_avif_s3_key: Optional[str] = None
    # Thumbnail versions
    cdn_thumbnail_s3_key: Optional[str] = None
    cdn_thumbnail_webp_s3_key: Optional[str] = None
    cdn_thumbnail_avif_s3_key: Optional[str] = None
    # Small versions
    cdn_small_s3_key: Optional[str] = None
    cdn_small_webp_s3_key: Optional[str] = None
    cdn_small_avif_s3_key: Optional[str] = None
    # Medium versions
    cdn_medium_s3_key: Optional[str] = None
    cdn_medium_webp_s3_key: Optional[str] = None
    cdn_medium_avif_s3_key: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        assert_valid_uuid(self.image_id)

        if not isinstance(self.receipt_id, int):
            raise ValueError("id must be an integer")
        if self.receipt_id <= 0:
            raise ValueError("id must be positive")

        if (
            self.width <= 0
            or self.height <= 0
            or not isinstance(self.width, int)
            or not isinstance(self.height, int)
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

        assert_valid_point(self.top_right)
        assert_valid_point(self.top_left)
        assert_valid_point(self.bottom_left)
        assert_valid_point(self.bottom_right)

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

        # Validate thumbnail fields
        if self.cdn_thumbnail_s3_key and not isinstance(
            self.cdn_thumbnail_s3_key, str
        ):
            raise ValueError("cdn_thumbnail_s3_key must be a string")

        if self.cdn_thumbnail_webp_s3_key and not isinstance(
            self.cdn_thumbnail_webp_s3_key, str
        ):
            raise ValueError("cdn_thumbnail_webp_s3_key must be a string")

        if self.cdn_thumbnail_avif_s3_key and not isinstance(
            self.cdn_thumbnail_avif_s3_key, str
        ):
            raise ValueError("cdn_thumbnail_avif_s3_key must be a string")

        # Validate small fields
        if self.cdn_small_s3_key and not isinstance(
            self.cdn_small_s3_key, str
        ):
            raise ValueError("cdn_small_s3_key must be a string")

        if self.cdn_small_webp_s3_key and not isinstance(
            self.cdn_small_webp_s3_key, str
        ):
            raise ValueError("cdn_small_webp_s3_key must be a string")

        if self.cdn_small_avif_s3_key and not isinstance(
            self.cdn_small_avif_s3_key, str
        ):
            raise ValueError("cdn_small_avif_s3_key must be a string")

        # Validate medium fields
        if self.cdn_medium_s3_key and not isinstance(
            self.cdn_medium_s3_key, str
        ):
            raise ValueError("cdn_medium_s3_key must be a string")

        if self.cdn_medium_webp_s3_key and not isinstance(
            self.cdn_medium_webp_s3_key, str
        ):
            raise ValueError("cdn_medium_webp_s3_key must be a string")

        if self.cdn_medium_avif_s3_key and not isinstance(
            self.cdn_medium_avif_s3_key, str
        ):
            raise ValueError("cdn_medium_avif_s3_key must be a string")

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the receipt.

        Returns:
            dict: The primary key for the receipt.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"RECEIPT#{self.receipt_id:05d}"},
        }

    def gsi1_key(self) -> Dict[str, Any]:
        """Generates the GSI1 key for the receipt.

        Returns:
            dict: The GSI1 key for the receipt.
        """
        return {
            "GSI1PK": {"S": f"IMAGE#{self.image_id}"},
            "GSI1SK": {"S": f"RECEIPT#{self.receipt_id:05d}"},
        }

    def gsi2_key(self) -> Dict[str, Any]:
        """Generates the GSI2 key for the receipt.

        Returns:
            dict: The GSI2 key for the receipt.
        """
        return {
            "GSI2PK": {"S": "RECEIPT"},
            "GSI2SK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}"
            },
        }

    def gsi3_key(self) -> Dict[str, Any]:
        """Generates the GSI3 key for the receipt.

        Returns:
            dict: The GSI3 key for the receipt.
        """
        return {
            "GSI3PK": {"S": f"IMAGE#{self.image_id}"},
            "GSI3SK": {"S": f"RECEIPT#{self.receipt_id:05d}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the Receipt object to a DynamoDB item.

        Returns:
            dict: Dictionary representing the receipt as a DynamoDB item.
        """
        return {
            **self.key,
            **self.gsi1_key(),
            **self.gsi2_key(),
            **self.gsi3_key(),
            "TYPE": {"S": "RECEIPT"},
            "width": {"N": str(self.width)},
            "height": {"N": str(self.height)},
            "timestamp_added": {"S": self.timestamp_added},
            "raw_s3_bucket": {"S": self.raw_s3_bucket},
            "raw_s3_key": {"S": self.raw_s3_key},
            "top_left": {
                "M": {
                    "x": {"N": _format_float(self.top_left["x"], 18, 20)},
                    "y": {"N": _format_float(self.top_left["y"], 18, 20)},
                }
            },
            "top_right": {
                "M": {
                    "x": {"N": _format_float(self.top_right["x"], 18, 20)},
                    "y": {"N": _format_float(self.top_right["y"], 18, 20)},
                }
            },
            "bottom_left": {
                "M": {
                    "x": {"N": _format_float(self.bottom_left["x"], 18, 20)},
                    "y": {"N": _format_float(self.bottom_left["y"], 18, 20)},
                }
            },
            "bottom_right": {
                "M": {
                    "x": {"N": _format_float(self.bottom_right["x"], 18, 20)},
                    "y": {"N": _format_float(self.bottom_right["y"], 18, 20)},
                }
            },
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
            # Thumbnail versions
            "cdn_thumbnail_s3_key": (
                {"S": self.cdn_thumbnail_s3_key}
                if self.cdn_thumbnail_s3_key
                else {"NULL": True}
            ),
            "cdn_thumbnail_webp_s3_key": (
                {"S": self.cdn_thumbnail_webp_s3_key}
                if self.cdn_thumbnail_webp_s3_key
                else {"NULL": True}
            ),
            "cdn_thumbnail_avif_s3_key": (
                {"S": self.cdn_thumbnail_avif_s3_key}
                if self.cdn_thumbnail_avif_s3_key
                else {"NULL": True}
            ),
            # Small versions
            "cdn_small_s3_key": (
                {"S": self.cdn_small_s3_key}
                if self.cdn_small_s3_key
                else {"NULL": True}
            ),
            "cdn_small_webp_s3_key": (
                {"S": self.cdn_small_webp_s3_key}
                if self.cdn_small_webp_s3_key
                else {"NULL": True}
            ),
            "cdn_small_avif_s3_key": (
                {"S": self.cdn_small_avif_s3_key}
                if self.cdn_small_avif_s3_key
                else {"NULL": True}
            ),
            # Medium versions
            "cdn_medium_s3_key": (
                {"S": self.cdn_medium_s3_key}
                if self.cdn_medium_s3_key
                else {"NULL": True}
            ),
            "cdn_medium_webp_s3_key": (
                {"S": self.cdn_medium_webp_s3_key}
                if self.cdn_medium_webp_s3_key
                else {"NULL": True}
            ),
            "cdn_medium_avif_s3_key": (
                {"S": self.cdn_medium_avif_s3_key}
                if self.cdn_medium_avif_s3_key
                else {"NULL": True}
            ),
        }

    def __repr__(self) -> str:
        """Returns a string representation of the Receipt object.

        Returns:
            str: A string representation of the Receipt object.
        """
        return (
            "Receipt("
            f"image_id={_repr_str(self.image_id)}, "
            f"receipt_id={self.receipt_id}, "
            f"width={self.width}, "
            f"height={self.height}, "
            f"timestamp_added={_repr_str(self.timestamp_added)}, "
            f"raw_s3_bucket={_repr_str(self.raw_s3_bucket)}, "
            f"raw_s3_key={_repr_str(self.raw_s3_key)}, "
            f"top_left={self.top_left}, "
            f"top_right={self.top_right}, "
            f"bottom_left={self.bottom_left}, "
            f"bottom_right={self.bottom_right}, "
            f"sha256={_repr_str(self.sha256)}, "
            f"cdn_s3_bucket={_repr_str(self.cdn_s3_bucket)}, "
            f"cdn_s3_key={_repr_str(self.cdn_s3_key)}, "
            f"cdn_webp_s3_key={_repr_str(self.cdn_webp_s3_key)}, "
            f"cdn_avif_s3_key={_repr_str(self.cdn_avif_s3_key)}, "
            f"cdn_thumbnail_s3_key={_repr_str(self.cdn_thumbnail_s3_key)}, "
            f"cdn_thumbnail_webp_s3_key="
            f"{_repr_str(self.cdn_thumbnail_webp_s3_key)}, "
            f"cdn_thumbnail_avif_s3_key="
            f"{_repr_str(self.cdn_thumbnail_avif_s3_key)}, "
            f"cdn_small_s3_key={_repr_str(self.cdn_small_s3_key)}, "
            f"cdn_small_webp_s3_key={_repr_str(self.cdn_small_webp_s3_key)}, "
            f"cdn_small_avif_s3_key={_repr_str(self.cdn_small_avif_s3_key)}, "
            f"cdn_medium_s3_key={_repr_str(self.cdn_medium_s3_key)}, "
            f"cdn_medium_webp_s3_key="
            f"{_repr_str(self.cdn_medium_webp_s3_key)}, "
            f"cdn_medium_avif_s3_key={_repr_str(self.cdn_medium_avif_s3_key)}"
            ")"
        )

    def __hash__(self) -> int:
        """Returns the hash value of the Receipt object."""
        return hash(
            (
                self.receipt_id,
                self.image_id,
                self.width,
                self.height,
                self.timestamp_added,
                self.raw_s3_bucket,
                self.raw_s3_key,
                tuple(self.top_right.items()),
                tuple(self.top_left.items()),
                tuple(self.bottom_right.items()),
                tuple(self.bottom_left.items()),
                self.sha256,
                self.cdn_s3_bucket,
                self.cdn_s3_key,
                self.cdn_webp_s3_key,
                self.cdn_avif_s3_key,
                # Thumbnail versions
                self.cdn_thumbnail_s3_key,
                self.cdn_thumbnail_webp_s3_key,
                self.cdn_thumbnail_avif_s3_key,
                # Small versions
                self.cdn_small_s3_key,
                self.cdn_small_webp_s3_key,
                self.cdn_small_avif_s3_key,
                # Medium versions
                self.cdn_medium_s3_key,
                self.cdn_medium_webp_s3_key,
                self.cdn_medium_avif_s3_key,
            )
        )


def item_to_receipt(item: Dict[str, Any]) -> Receipt:
    """Converts a DynamoDB item to a Receipt object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        Receipt: The Receipt object.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {
        "PK",
        "SK",
        "width",
        "height",
        "timestamp_added",
        "raw_s3_bucket",
        "raw_s3_key",
        "top_left",
        "top_right",
        "bottom_left",
        "bottom_right",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            "Invalid item format\n"
            f"missing keys: {missing_keys}\n"
            f"additional keys: {additional_keys}"
        )
    try:
        return Receipt(
            image_id=item["PK"]["S"].split("#")[1],
            receipt_id=int(item["SK"]["S"].split("#")[1]),
            width=int(item["width"]["N"]),
            height=int(item["height"]["N"]),
            timestamp_added=item["timestamp_added"]["S"],
            raw_s3_bucket=item["raw_s3_bucket"]["S"],
            raw_s3_key=item["raw_s3_key"]["S"],
            top_left={
                key: float(value["N"])
                for key, value in item["top_left"]["M"].items()
            },
            top_right={
                key: float(value["N"])
                for key, value in item["top_right"]["M"].items()
            },
            bottom_left={
                key: float(value["N"])
                for key, value in item["bottom_left"]["M"].items()
            },
            bottom_right={
                key: float(value["N"])
                for key, value in item["bottom_right"]["M"].items()
            },
            sha256=(
                item["sha256"]["S"]
                if "sha256" in item and "S" in item["sha256"]
                else None
            ),
            cdn_s3_bucket=(
                item["cdn_s3_bucket"]["S"]
                if "cdn_s3_bucket" in item and "S" in item["cdn_s3_bucket"]
                else None
            ),
            cdn_s3_key=(
                item["cdn_s3_key"]["S"]
                if "cdn_s3_key" in item and "S" in item["cdn_s3_key"]
                else None
            ),
            cdn_webp_s3_key=(
                item["cdn_webp_s3_key"]["S"]
                if (
                    "cdn_webp_s3_key" in item
                    and "S" in item["cdn_webp_s3_key"]
                )
                else None
            ),
            cdn_avif_s3_key=(
                item["cdn_avif_s3_key"]["S"]
                if (
                    "cdn_avif_s3_key" in item
                    and "S" in item["cdn_avif_s3_key"]
                )
                else None
            ),
            # Thumbnail versions
            cdn_thumbnail_s3_key=(
                item["cdn_thumbnail_s3_key"]["S"]
                if "cdn_thumbnail_s3_key" in item
                and "S" in item["cdn_thumbnail_s3_key"]
                else None
            ),
            cdn_thumbnail_webp_s3_key=(
                item["cdn_thumbnail_webp_s3_key"]["S"]
                if "cdn_thumbnail_webp_s3_key" in item
                and "S" in item["cdn_thumbnail_webp_s3_key"]
                else None
            ),
            cdn_thumbnail_avif_s3_key=(
                item["cdn_thumbnail_avif_s3_key"]["S"]
                if "cdn_thumbnail_avif_s3_key" in item
                and "S" in item["cdn_thumbnail_avif_s3_key"]
                else None
            ),
            # Small versions
            cdn_small_s3_key=(
                item["cdn_small_s3_key"]["S"]
                if "cdn_small_s3_key" in item
                and "S" in item["cdn_small_s3_key"]
                else None
            ),
            cdn_small_webp_s3_key=(
                item["cdn_small_webp_s3_key"]["S"]
                if "cdn_small_webp_s3_key" in item
                and "S" in item["cdn_small_webp_s3_key"]
                else None
            ),
            cdn_small_avif_s3_key=(
                item["cdn_small_avif_s3_key"]["S"]
                if "cdn_small_avif_s3_key" in item
                and "S" in item["cdn_small_avif_s3_key"]
                else None
            ),
            # Medium versions
            cdn_medium_s3_key=(
                item["cdn_medium_s3_key"]["S"]
                if "cdn_medium_s3_key" in item
                and "S" in item["cdn_medium_s3_key"]
                else None
            ),
            cdn_medium_webp_s3_key=(
                item["cdn_medium_webp_s3_key"]["S"]
                if "cdn_medium_webp_s3_key" in item
                and "S" in item["cdn_medium_webp_s3_key"]
                else None
            ),
            cdn_medium_avif_s3_key=(
                item["cdn_medium_avif_s3_key"]["S"]
                if "cdn_medium_avif_s3_key" in item
                and "S" in item["cdn_medium_avif_s3_key"]
                else None
            ),
        )
    except Exception as e:
        raise ValueError(f"Error converting item to Receipt: {e}")
