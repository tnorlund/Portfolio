from typing import Generator, Tuple
from datetime import datetime
from dynamo.entities.util import (
    assert_valid_uuid
)

class Image:
    def __init__(
        self,
        id: str,
        width: int,
        height: int,
        timestamp_added: datetime,
        raw_s3_bucket: str,
        raw_s3_key: str,
        sha256: str = None,
        cdn_s3_bucket: str = None,
        cdn_s3_key: str = None,
    ):
        """Constructs a new Image object for DynamoDB

        Args:
            id (str): UUID identifying the image
            width (int): The width of the image in pixels
            height (int): The height of the image in pixels
            timestamp_added (datetime): The timestamp the image was added
            raw_s3_bucket (str): The S3 bucket where the image is initially stored
            raw_s3_key (str): The S3 key where the image is initially stored
            sha256 (str): The SHA256 hash of the image
            cdn_s3_bucket (str): The S3 bucket where the image is stored in the CDN
            cdn_s3_key (str): The S3 key where the image is stored in the CDN

        Attributes:
            id (str): UUID identifying the image
            width (int): The width of the image in pixels
            height (int): The height of the image in pixels
            timestamp_added (datetime): The timestamp the image was added
            raw_s3_bucket (str): The S3 bucket where the image is initially stored
            raw_s3_key (str): The S3 key where the image is initially stored
            sha256 (str): The SHA256 hash of the image
            cdn_s3_bucket (str): The S3 bucket where the image is stored in the CDN
            cdn_s3_key (str): The S3 key where the image is stored in the CDN

        Raises:
            ValueError: When the ID is not a positive integer
            ValueError: When the width or height are not positive integers
            ValueError: When the timestamp_added is not a datetime object or a string
        """
        assert_valid_uuid(id)
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
            raise ValueError("timestamp_added must be a string or datetime")
        
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

    def key(self) -> dict:
        """Generates the primary key for the image

        Returns:
            dict: The primary key for the image
        """
        return {"PK": {"S": f"IMAGE#{self.id}"}, "SK": {"S": "IMAGE"}}

    def gsi1_key(self) -> dict:
        """Generates the GSI1 key for the image

        Returns:
            dict: The GSI1 key for the image
        """
        return {"GSI1PK": {"S": "IMAGE"}, "GSI1SK": {"S": f"IMAGE#{self.id}"}}

    def to_item(self) -> dict:
        """Converts the Image object to a DynamoDB item

        Returns:
            dict: The DynamoDB item representation of the Image
        """
        return {
            **self.key(),
            **self.gsi1_key(),
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
            "cdn_s3_key": {"S": self.cdn_s3_key} if self.cdn_s3_key else {"NULL": True},
        }

    def __repr__(self) -> str:
        """Returns a string representation of the Image object

        Returns:
            str: The string representation of the Image object
        """
        return f"Image(id='{self.id}')"

    def __iter__(self) -> Generator[Tuple[str, int], None, None]:
        """Returns an iterator over the Image object

        Returns:
            dict: The iterator over the Image object
        """
        yield "id", self.id
        yield "width", self.width
        yield "height", self.height
        yield "timestamp_added", self.timestamp_added
        yield "raw_s3_bucket", self.raw_s3_bucket
        yield "raw_s3_key", self.raw_s3_key
        yield "cdn_s3_bucket", self.cdn_s3_bucket
        yield "cdn_s3_key", self.cdn_s3_key
        yield "sha256", self.sha256

    def __eq__(self, other) -> bool:
        """Checks if two Image objects are equal

        Args:
            other (Image): The other Image object to compare

        Returns:
            bool: True if the Image objects are equal, False otherwise
        """
        if not isinstance(other, Image):
            return False
        return (
            self.id == other.id
            and self.width == other.width
            and self.height == other.height
            and self.timestamp_added == other.timestamp_added
            and self.raw_s3_bucket == other.raw_s3_bucket
            and self.raw_s3_key == other.raw_s3_key
            and self.sha256 == other.sha256
            and self.cdn_s3_bucket == other.cdn_s3_bucket
            and self.cdn_s3_key == other.cdn_s3_key
        )


def itemToImage(item: dict) -> Image:
    """Converts a DynamoDB item to an Image object

    Args:
        item (dict): The DynamoDB item to convert

    Returns:
        Image: The Image object represented by the DynamoDB item

    Raises:
        ValueError: When the item format is invalid
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
        return Image(
            id=item["PK"]["S"].split("#")[1],
            width=int(item["width"]["N"]),
            height=int(item["height"]["N"]),
            timestamp_added=datetime.fromisoformat(item["timestamp_added"]["S"]),
            raw_s3_bucket=item["raw_s3_bucket"]["S"],
            raw_s3_key=item["raw_s3_key"]["S"],
            sha256=sha256 if sha256 else None,
            cdn_s3_bucket=cdn_s3_bucket if cdn_s3_bucket else None,
            cdn_s3_key=cdn_s3_key if cdn_s3_key else None,
        )
    except KeyError as e:
        raise ValueError(f"Error converting item to Image: {e}")
