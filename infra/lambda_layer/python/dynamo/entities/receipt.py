from typing import Any, Generator, Tuple
from datetime import datetime
from dynamo.entities.util import (
    assert_valid_uuid,
    assert_valid_point,
    _format_float,
)


class Receipt:
    def __init__(
        self,
        image_id: str,
        id: int,
        width: int,
        height: int,
        timestamp_added: datetime,
        raw_s3_bucket: str,
        raw_s3_key: str,
        top_left: dict,
        top_right: dict,
        bottom_left: dict,
        bottom_right: dict,
        sha256: str = None,
        cdn_s3_bucket: str = None,
        cdn_s3_key: str = None,
    ):
        """Initializes a new Receipt object for DynamoDB.

        Args:
            image_id (str): UUID identifying the associated image.
            id (int): Number identifying the receipt.
            width (int): The width of the receipt in pixels.
            height (int): The height of the receipt in pixels.
            timestamp_added (datetime): The timestamp when the receipt was added.
            raw_s3_bucket (str): The S3 bucket where the receipt is stored.
            raw_s3_key (str): The S3 key where the receipt is stored.
            top_left (dict): The top left corner of the bounding box.
            top_right (dict): The top right corner of the bounding box.
            bottom_left (dict): The bottom left corner of the bounding box.
            bottom_right (dict): The bottom right corner of the bounding box.
            sha256 (str): The SHA256 hash of the receipt.
            cdn_s3_bucket (str, optional): The S3 bucket for the CDN version of the receipt.
            cdn_s3_key (str, optional): The S3 key for the CDN version of the receipt.

        Attributes:
            image_id (str): UUID identifying the associated image.
            id (int): Number identifying the receipt.
            width (int): The width of the receipt in pixels.
            height (int): The height of the receipt in pixels.
            timestamp_added (datetime): The timestamp when the receipt was added.
            raw_s3_bucket (str): The S3 bucket where the receipt is stored.
            raw_s3_key (str): The S3 key where the receipt is stored.
            top_left (dict): The top left corner of the bounding box.
            top_right (dict): The top right corner of the bounding box.
            bottom_left (dict): The bottom left corner of the bounding box.
            bottom_right (dict): The bottom right corner of the bounding box.
            sha256 (str): The SHA256 hash of the receipt.
            cdn_s3_bucket (str): The S3 bucket for the CDN version of the receipt.
            cdn_s3_key (str): The S3 key for the CDN version of the receipt.

        Raises:
            ValueError: If image_id is not a valid UUID.
            ValueError: If id is not a positive integer.
            ValueError: If width or height is not a positive integer.
            ValueError: If timestamp_added is not a datetime object or a string.
            ValueError: If raw_s3_bucket, raw_s3_key, sha256, cdn_s3_bucket, or cdn_s3_key is not a string.
        """
        assert_valid_uuid(image_id)
        self.image_id = image_id

        if not isinstance(id, int):
            raise ValueError("id must be an integer")
        if id <= 0:
            raise ValueError("id must be positive")
        self.id = id

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

        assert_valid_point(top_right)
        self.top_right = top_right
        assert_valid_point(top_left)
        self.top_left = top_left
        assert_valid_point(bottom_left)
        self.bottom_left = bottom_left
        assert_valid_point(bottom_right)
        self.bottom_right = bottom_right

        if sha256 and not isinstance(sha256, str):
            raise ValueError("sha256 must be a string")
        self.sha256 = sha256

        if cdn_s3_bucket and not isinstance(cdn_s3_bucket, str):
            raise ValueError("cdn_s3_bucket must be a string")
        self.cdn_s3_bucket = cdn_s3_bucket
        if cdn_s3_key and not isinstance(cdn_s3_key, str):
            raise ValueError("cdn_s3_key must be a string")
        self.cdn_s3_key = cdn_s3_key

    def key(self) -> dict:
        """Generates the primary key for the receipt.

        Returns:
            dict: The primary key for the receipt.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"RECEIPT#{self.id:05d}"},
        }

    def gsi1_key(self) -> dict:
        """Generates the GSI1 key for the receipt.

        Returns:
            dict: The GSI1 key for the receipt.
        """
        return {
            "GSI1PK": {"S": "IMAGE"},
            "GSI1SK": {"S": f"IMAGE#{self.image_id}#RECEIPT#{self.id:05d}"},
        }

    def gsi2_key(self) -> dict:
        """Generates the GSI2 key for the receipt.

        Returns:
            dict: The GSI2 key for the receipt.
        """
        return {
            "GSI2PK": {"S": "RECEIPT"},
            "GSI2SK": {"S": f"IMAGE#{self.image_id}#RECEIPT#{self.id:05d}"},
        }

    def to_item(self) -> dict:
        """Converts the Receipt object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the Receipt object as a DynamoDB item.
        """
        return {
            **self.key(),
            **self.gsi1_key(),
            **self.gsi2_key(),
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
                {"S": self.cdn_s3_bucket} if self.cdn_s3_bucket else {"NULL": True}
            ),
            "cdn_s3_key": {"S": self.cdn_s3_key} if self.cdn_s3_key else {"NULL": True},
        }

    def __repr__(self) -> str:
        """Returns a string representation of the Receipt object.

        Returns:
            str: A string representation of the Receipt object.
        """
        return (
            "Receipt("
            f"image_id='{self.image_id}', "
            f"id={int(self.id)}, "
            f"width={self.width}, "
            f"height={self.height}, "
            f"timestamp_added={self.timestamp_added}, "
            f"raw_s3_bucket='{self.raw_s3_bucket}', "
            f"raw_s3_key='{self.raw_s3_key}', "
            f"top_left={self.top_left}, "
            f"top_right={self.top_right}, "
            f"bottom_left={self.bottom_left}, "
            f"bottom_right={self.bottom_right}, "
            f"sha256='{self.sha256}', "
            f"cdn_s3_bucket='{self.cdn_s3_bucket}', "
            f"cdn_s3_key='{self.cdn_s3_key}'"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the Receipt object's attributes.

        Returns:
            Generator[Tuple[str, Any], None, None]: An iterator over the Receipt object's attribute name/value pairs.
        """
        yield "id", int(self.id)
        yield "image_id", self.image_id
        yield "width", self.width
        yield "height", self.height
        yield "timestamp_added", self.timestamp_added
        yield "raw_s3_bucket", self.raw_s3_bucket
        yield "raw_s3_key", self.raw_s3_key
        yield "top_left", self.top_left
        yield "top_right", self.top_right
        yield "bottom_left", self.bottom_left
        yield "bottom_right", self.bottom_right
        yield "sha256", self.sha256
        yield "cdn_s3_bucket", self.cdn_s3_bucket
        yield "cdn_s3_key", self.cdn_s3_key

    def __eq__(self, other) -> bool:
        """Determines whether two Receipt objects are equal.

        Args:
            other (Receipt): The other Receipt object to compare.

        Returns:
            bool: True if the Receipt objects are equal, False otherwise.

        Note:
            If other is not an instance of Receipt, NotImplemented is returned.
        """
        if not isinstance(other, Receipt):
            return NotImplemented
        return (
            int(self.id) == int(other.id)
            and self.image_id == other.image_id
            and self.width == other.width
            and self.height == other.height
            and self.timestamp_added == other.timestamp_added
            and self.raw_s3_bucket == other.raw_s3_bucket
            and self.raw_s3_key == other.raw_s3_key
            and self.top_left == other.top_left
            and self.top_right == other.top_right
            and self.bottom_left == other.bottom_left
            and self.bottom_right == other.bottom_right
            and self.sha256 == other.sha256
            and self.cdn_s3_bucket == other.cdn_s3_bucket
            and self.cdn_s3_key == other.cdn_s3_key
        )


def itemToReceipt(item: dict) -> Receipt:
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
            f"Invalid item format\nmissing keys: {missing_keys}\nadditional keys: {additional_keys}"
        )
    try:
        return Receipt(
            image_id=item["PK"]["S"].split("#")[1],
            id=int(item["SK"]["S"].split("#")[1]),
            width=int(item["width"]["N"]),
            height=int(item["height"]["N"]),
            timestamp_added=item["timestamp_added"]["S"],
            raw_s3_bucket=item["raw_s3_bucket"]["S"],
            raw_s3_key=item["raw_s3_key"]["S"],
            top_left={
                key: float(value["N"]) for key, value in item["top_left"]["M"].items()
            },
            top_right={
                key: float(value["N"]) for key, value in item["top_right"]["M"].items()
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
        )
    except Exception as e:
        raise ValueError(f"Error converting item to Receipt: {e}")
