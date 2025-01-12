from typing import Generator, Tuple
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP


def assert_valid_point(point):
    """
    Assert that the point is valid.
    """
    if not isinstance(point, dict):
        raise ValueError("point must be a dictionary")
    for key in ["x", "y"]:
        if key not in point:
            raise ValueError(f"point must contain the key '{key}'")
        if not isinstance(point[key], (int, float)):
            raise ValueError(f"point['{key}'] must be a number")
    return point


def _format_float(
    value: float, decimal_places: int = 10, total_length: int = 20
) -> str:
    # Convert float → string → Decimal to avoid float binary representation issues
    d_value = Decimal(str(value))

    # Create a "quantizer" for the desired number of decimal digits
    # e.g. decimal_places=10 → quantizer = Decimal('1.0000000000')
    quantizer = Decimal("1." + "0" * decimal_places)

    # Round using the chosen rounding mode (e.g. HALF_UP)
    d_rounded = d_value.quantize(quantizer, rounding=ROUND_HALF_UP)

    # Format as a string with exactly `decimal_places` decimals
    formatted = f"{d_rounded:.{decimal_places}f}"

    # Optional: Pad to `total_length` characters
    # If you want leading zeros:
    if len(formatted) < total_length:
        formatted = formatted.zfill(total_length)

    # If instead you wanted trailing zeros, you could do:
    # formatted = formatted.ljust(total_length, '0')

    return formatted


class Receipt:
    def __init__(
        self,
        image_id: int,
        id: int,
        width: int,
        height: int,
        timestamp_added: datetime,
        s3_bucket: str,
        s3_key: str,
        top_left: dict,
        top_right: dict,
        bottom_left: dict,
        bottom_right: dict,
        sha256: str = None,
    ):
        """Constructs a new Receipt object for DynamoDB

        Args:
            image_id (int): Number identifying the image
            id (int): Number identifying the receipt
            width (int): The width of the receipt in pixels
            height (int): The height of the receipt in pixels
            timestamp_added (datetime): The timestamp the receipt was added
            s3_bucket (str): The S3 bucket where the receipt is stored
            s3_key (str): The S3 key where the receipt is stored
            top_left (dict): The top left corner of the bounding box
            top_right (dict): The top right corner of the bounding box
            bottom_left (dict): The bottom left corner of the bounding box
            bottom_right (dict): The bottom right corner of the bounding box
            sha256 (str): The SHA256 hash of the receipt

        Attributes:
        """
        # Ensure the Image ID is a positive integer
        if image_id <= 0 or not isinstance(image_id, int):
            raise ValueError("image_id must be a positive integer")
        self.image_id = image_id
        # Ensure the ID is a positive integer
        if id <= 0 or not isinstance(id, int):
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
        assert_valid_point(top_right)
        self.topRight = top_right
        assert_valid_point(top_left)
        self.topLeft = top_left
        assert_valid_point(bottom_left)
        self.bottomLeft = bottom_left
        assert_valid_point(bottom_right)
        self.bottomRight = bottom_right
        if sha256 and not isinstance(sha256, str):
            raise ValueError("sha256 must be a string")
        self.sha256 = sha256

    def key(self) -> dict:
        """Generates the primary key for the line

        Returns:
            dict: The primary key for the line
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id:05d}"},
            "SK": {"S": f"RECEIPT#{self.id:05d}"},
        }

    def gsi1_key(self) -> dict:
        """Generates the GSI1 key for the receipt

        Returns:
            dict: The GSI1 key for the receipt
        """
        return {
            "GSI1PK": {"S": "IMAGE"},
            "GSI1SK": {"S": f"IMAGE#{self.image_id:05d}#RECEIPT#{self.id:05d}"},
        }

    def to_item(self) -> dict:
        """Converts the Receipt object to a DynamoDB item

        Returns:
            dict: The Receipt object as a DynamoDB item
        """
        return {
            **self.key(),
            **self.gsi1_key(),
            "TYPE": {"S": "RECEIPT"},
            "width": {"N": str(self.width)},
            "height": {"N": str(self.height)},
            "timestamp_added": {"S": self.timestamp_added},
            "s3_bucket": {"S": self.s3_bucket},
            "s3_key": {"S": self.s3_key},
            "top_left": {
                "M": {
                    "x": {"N": _format_float(self.topLeft["x"], 18, 20)},
                    "y": {"N": _format_float(self.topLeft["y"], 18, 20)},
                }
            },
            "top_right": {
                "M": {
                    "x": {"N": _format_float(self.topRight["x"], 18, 20)},
                    "y": {"N": _format_float(self.topRight["y"], 18, 20)},
                }
            },
            "bottom_left": {
                "M": {
                    "x": {"N": _format_float(self.bottomLeft["x"], 18, 20)},
                    "y": {"N": _format_float(self.bottomLeft["y"], 18, 20)},
                }
            },
            "bottom_right": {
                "M": {
                    "x": {"N": _format_float(self.bottomRight["x"], 18, 20)},
                    "y": {"N": _format_float(self.bottomRight["y"], 18, 20)},
                }
            },
            "sha256": {"S": self.sha256} if self.sha256 else {"NULL": True},
        }

    def __repr__(self) -> str:
        """Returns a string representation of the Receipt object

        Returns:
            str: The string representation of the Receipt object
        """
        return f"Receipt(id={int(self.id)}, image_id={int(self.image_id)} s3_key={self.s3_key})"

    def __iter__(self) -> Generator[Tuple[str, int], None, None]:
        """Returns an iterator over the Receipt object

        Returns:
            dict: The iterator over the Receipt object
        """
        yield "id", int(self.id)
        yield "width", self.width
        yield "height", self.height
        yield "timestamp_added", self.timestamp_added
        yield "s3_bucket", self.s3_bucket
        yield "s3_key", self.s3_key
        yield "topLeft", self.topLeft
        yield "topRight", self.topRight
        yield "bottomLeft", self.bottomLeft
        yield "bottomRight", self.bottomRight
        yield "sha256", self.sha256

    def __eq__(self, other) -> bool:
        """Checks if two Receipt objects are equal

        Args:
            other (Receipt): The other Receipt object to compare

        Returns:
            bool: True if the Receipt objects are equal, False otherwise
        """
        if not isinstance(other, Receipt):
            return NotImplemented
        return (
            int(self.id) == int(other.id)
            and self.image_id == other.image_id
            and self.width == other.width
            and self.height == other.height
            and self.timestamp_added == other.timestamp_added
            and self.s3_bucket == other.s3_bucket
            and self.s3_key == other.s3_key
            and self.topLeft == other.topLeft
            and self.topRight == other.topRight
            and self.bottomLeft == other.bottomLeft
            and self.bottomRight == other.bottomRight
            and self.sha256 == other.sha256
        )


def itemToReceipt(item: dict) -> Receipt:
    """Converts a DynamoDB item to a Receipt object

    Args:
        item (dict): The DynamoDB item to convert

    Returns:
        Receipt: The Receipt object
    """
    required_keys = {
        "PK",
        "SK",
        "width",
        "height",
        "timestamp_added",
        "s3_bucket",
        "s3_key",
        "top_left",
        "top_right",
        "bottom_left",
        "bottom_right",
    }
    if not required_keys.issubset(item.keys()):
        raise ValueError("Invalid item format")
    try:
        return Receipt(
            image_id=int(item["PK"]["S"].split("#")[1]),
            id=int(item["SK"]["S"].split("#")[1]),
            width=int(item["width"]["N"]),
            height=int(item["height"]["N"]),
            timestamp_added=item["timestamp_added"]["S"],
            s3_bucket=item["s3_bucket"]["S"],
            s3_key=item["s3_key"]["S"],
            top_left={
                key: float(value["N"]) for key, value in item["top_left"]["M"].items()
            },
            top_right={
                key: float(value["N"]) for key, value in item["top_right"]["M"].items()
            },
            bottom_left={
                key: float(value["N"]) for key, value in item["bottom_left"]["M"].items()
            },
            bottom_right={
                key: float(value["N"])
                for key, value in item["bottom_right"]["M"].items()
            },
            sha256=item["sha256"]["S"] if "sha256" in item else None,
        )
    except Exception as e:
        raise ValueError("Invalid item format") from e
