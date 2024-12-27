from typing import Generator, Tuple
from datetime import datetime


class Image:
    def __init__(
        self,
        id: int,
        width: int,
        height: int,
        timestamp_added: datetime,
        s3_bucket: str,
        s3_key: str,
    ):
        """Constructs a new Image object for DynamoDB

        Args:
            id (int): Number identifying the image
            width (int): The width of the image in pixels
            height (int): The height of the image in pixels

        Attributes:
            id (int): Number identifying the image
            width (int): The width of the image in pixels
            height (int): The height of the image in pixels

        Raises:
            ValueError: When the ID is not a positive integer
        """
        # Ensure the ID is a positive integer
        if id <= 0:
            raise ValueError("id must be a positive integer")
        self.id = id
        # Ensure the width and height are positive integers
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
        self.s3_bucket = s3_bucket
        self.s3_key = s3_key

    def key(self) -> dict:
        """Generates the primary key for the image

        Returns:
            dict: The primary key for the image
        """
        return {"PK": {"S": f"IMAGE#{self.id:05d}"}, "SK": {"S": "IMAGE"}}

    def to_item(self) -> dict:
        """Converts the Image object to a DynamoDB item

        Returns:
            dict: The DynamoDB item representation of the Image
        """
        return {
            **self.key(),
            "Type": {"S": "IMAGE"},
            "Width": {"N": str(self.width)},
            "Height": {"N": str(self.height)},
            "TimestampAdded": {"S": self.timestamp_added},
            "S3Bucket": {"S": self.s3_bucket},
            "S3Key": {"S": self.s3_key},
        }

    def __repr__(self) -> str:
        """Returns a string representation of the Image object

        Returns:
            str: The string representation of the Image object
        """
        return f"Image(id={int(self.id)}, s3_key={self.s3_key})"

    def __iter__(self) -> Generator[Tuple[str, int], None, None]:
        """Returns an iterator over the Image object

        Returns:
            dict: The iterator over the Image object
        """
        yield "id", int(self.id)
        yield "width", self.width
        yield "height", self.height
        yield "timestamp_added", self.timestamp_added
        yield "s3_bucket", self.s3_bucket
        yield "s3_key", self.s3_key

    def __eq__(self, other) -> bool:
        """Checks if two Image objects are equal

        Args:
            other (Image): The other Image object to compare

        Returns:
            bool: True if the Image objects are equal, False otherwise
        """
        if not isinstance(other, Image):
            return NotImplemented
        return (
            int(self.id) == int(other.id)
            and self.width == other.width
            and self.height == other.height
            and self.timestamp_added == other.timestamp_added
            and self.s3_bucket == other.s3_bucket
            and self.s3_key == other.s3_key
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
    if item.keys() != {"PK", "SK", "Type", "Width", "Height", "TimestampAdded", "S3Bucket", "S3Key"}:
        raise ValueError("Invalid item format")
    try:
        return Image(
            id=int(item["PK"]["S"].split("#")[1]),
            width=int(item["Width"]["N"]),
            height=int(item["Height"]["N"]),
            timestamp_added=datetime.fromisoformat(item["TimestampAdded"]["S"]),
            s3_bucket=item["S3Bucket"]["S"],
            s3_key=item["S3Key"]["S"],
        )
    except KeyError:
        raise ValueError("Invalid item format")
