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
        sha256: str = None
    ):
        """Constructs a new Image object for DynamoDB

        Args:
            id (int): Number identifying the image
            width (int): The width of the image in pixels
            height (int): The height of the image in pixels
            timestamp_added (datetime): The timestamp the image was added
            s3_bucket (str): The S3 bucket where the image is stored
            s3_key (str): The S3 key where the image is stored
            sha256 (str): The SHA256 hash of the image

        Attributes:
            id (int): Number identifying the image
            width (int): The width of the image in pixels
            height (int): The height of the image in pixels
            timestamp_added (datetime): The timestamp the image was added
            s3_bucket (str): The S3 bucket where the image is stored
            s3_key (str): The S3 key where the image is stored
            sha256 (str): The SHA256 hash of the image

        Raises:
            ValueError: When the ID is not a positive integer
            ValueError: When the width or height are not positive integers
            ValueError: When the timestamp_added is not a datetime object or a string
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
        if sha256 and not isinstance(sha256, str):
            raise ValueError("sha256 must be a string")
        self.sha256 = sha256

    def key(self) -> dict:
        """Generates the primary key for the image

        Returns:
            dict: The primary key for the image
        """
        return {"PK": {"S": f"IMAGE#{self.id:05d}"}, "SK": {"S": "IMAGE"}}
    
    def gsi1_key(self) -> dict:
        """Generates the GSI1 key for the image

        Returns:
            dict: The GSI1 key for the image
        """
        return {"GSI1PK": {"S": "IMAGE"}, "GSI1SK": {"S": f"IMAGE#{self.id:05d}"}}

    def to_item(self) -> dict:
        """Converts the Image object to a DynamoDB item

        Returns:
            dict: The DynamoDB item representation of the Image
        """
        return {
            **self.key(),
            **self.gsi1_key(),
            "Type": {"S": "IMAGE"},
            "Width": {"N": str(self.width)},
            "Height": {"N": str(self.height)},
            "TimestampAdded": {"S": self.timestamp_added},
            "S3Bucket": {"S": self.s3_bucket},
            "S3Key": {"S": self.s3_key},
            "SHA256": {"S": self.sha256} if self.sha256 else None
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
        yield "sha256", self.sha256

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
            and self.sha256 == other.sha256
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
    required_keys = {"PK", "SK", "GSI1PK", "GSI1SK", "Type", "Width", "Height", "TimestampAdded", "S3Bucket", "S3Key"}
    if not required_keys.issubset(item.keys()):
        raise ValueError("Invalid item format")
    try:
        return Image(
            id=int(item["PK"]["S"].split("#")[1]),
            width=int(item["Width"]["N"]),
            height=int(item["Height"]["N"]),
            timestamp_added=datetime.fromisoformat(item["TimestampAdded"]["S"]),
            s3_bucket=item["S3Bucket"]["S"],
            s3_key=item["S3Key"]["S"],
            sha256=item.get("SHA256", {}).get("S")
        )
    except KeyError:
        raise ValueError("Invalid item format")
