from typing import Generator, Tuple
from decimal import Decimal, ROUND_HALF_UP


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


class Line:
    def __init__(
        self,
        image_id: int,
        id: int,
        text: str,
        x: float,
        y: float,
        width: float,
        height: float,
        angle: float,
        confidence: float,
    ):
        """Constructs a new Line object for DynamoDB

        Args:
            image_id (int): Number identifying the image
            id (int): Number identifying the line
            text (str): The text of the line
            x (float): The x-coordinate of the starting point. This is at most 20 characters long.
            y (float): The y-coordinate of the starting point. This is at most 20 characters long.
            width (float): The width of the line. This is at most 20 characters long.
            height (float): The height of the line. This is at most 20 characters long.
            angle (float): The angle of the line. This is at most 10 characters long.
            confidence (float): The confidence of the line

        Attributes:
            image_id (int): Number identifying the image
            id (int): Number identifying the line
            text (str): The text of the line
            x (float): The x-coordinate of the starting point. This is at most 20 characters long.
            y (float): The y-coordinate of the starting point. This is at most 20 characters long.
            width (float): The width of the line. This is at most 20 characters long.
            height (float): The height of the line. This is at most 20 characters long.
            angle (float): The angle of the line. This is at most 10 characters long.
            confidence (float): The confidence of the line. This is exactly 2 characters long.

        Raises:
            ValueError: When the Image ID is not a positive integer
            ValueError: When the ID is not a positive integer
            ValueError: When text is not a string
            ValueError: When X is not a positive float
            ValueError: When Y is not a positive float
            ValueError: When width is not a positive float
            ValueError: When height is not a positive float
            ValueError: When angle is not a positive float
            ValueError: When confidence is not a float between 0 and 1
        """
        # Ensure the Image ID is a positive integer
        if image_id <= 0 or not isinstance(image_id, int):
            raise ValueError("image_id must be a positive integer")
        self.image_id = image_id
        # Ensure the ID is a positive integer
        if id <= 0 or not isinstance(id, int):
            raise ValueError("id must be a positive integer")
        self.id = id
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        self.text = text
        if x <= 0 or not isinstance(x, float):
            raise ValueError("x must be a positive float")
        self.x = x
        if y <= 0 or not isinstance(y, float):
            raise ValueError("y must be a positive float")
        self.y = y
        if width <= 0 or not isinstance(width, float):
            raise ValueError("width must be a positive float")
        self.width = width
        if height <= 0 or not isinstance(height, float):
            raise ValueError("height must be a positive float")
        self.height = height
        if angle <= 0 or not isinstance(angle, float):
            raise ValueError("angle must be a positive float")
        self.angle = angle
        # Ensure the confidence is a float between 0 and 1
        if confidence <= 0 or confidence > 1:
            raise ValueError("confidence must be a float between 0 and 1")
        self.confidence = confidence

    def key(self) -> dict:
        """Generates the primary key for the line

        Returns:
            dict: The primary key for the line
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id:05d}"},
            "SK": {"S": f"LINE#{self.id:05d}"},
        }

    def to_item(self) -> dict:
        """Converts the Line object to a DynamoDB item

        Returns:
            dict: The Line object as a DynamoDB item
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id:05d}"},
            "SK": {"S": f"LINE#{self.id:05d}"},
            "Type": {"S": "LINE"},
            "X": {"N": _format_float(self.x, 20, 22)},
            "Y": {"N": _format_float(self.y, 20, 22)},
            "Width": {"N": _format_float(self.width, 20, 22)},
            "Height": {"N": _format_float(self.height, 20, 22)},
            "Angle": {"N": _format_float(self.angle, 10, 12)},
            "Confidence": {"N": _format_float(self.confidence, 2, 2)},
        }

    def __repr__(self) -> str:
        """Returns a string representation of the Line object

        Returns:
            str: The string representation of the Line object
        """
        return f"Line(id={self.id}, text='{self.text}')"

    def __iter__(self) -> Generator[Tuple[str, int], None, None]:
        """Returns an iterator over the Line object

        Returns:
            dict: The iterator over the Line object
        """
        yield "id", self.id
        yield "text", self.text
        yield "x", self.x
        yield "y", self.y
        yield "width", self.width
        yield "height", self.height
        yield "angle", self.angle
        yield "confidence", self.confidence

    def __eq__(self, other: object) -> bool:
        """Compares two Line objects

        Args:
            other (object): The object to compare

        Returns:
            bool: True if the objects are equal, False otherwise
        """
        if not isinstance(other, Line):
            return False
        return (
            self.image_id == other.image_id
            and self.id == other.id
            and self.text == other.text
            and self.x == other.x
            and self.y == other.y
            and self.width == other.width
            and self.height == other.height
            and self.angle == other.angle
            and self.confidence == other.confidence
        )


def itemToLine(item: dict) -> Line:
    """Converts a DynamoDB item to a Line object

    Args:
        item (dict): The DynamoDB item to convert

    Returns:
        Line: The Line object represented by the DynamoDB item

    """
    return Line(
        int(item["PK"]["S"][6:]),
        int(item["SK"]["S"][6:]),
        item.get("Text", {}).get("S", ""),
        float(item.get("X", {}).get("N", 0)),
        float(item.get("Y", {}).get("N", 0)),
        float(item.get("Width", {}).get("N", 0)),
        float(item.get("Height", {}).get("N", 0)),
        float(item.get("Angle", {}).get("N", 0)),
        float(item.get("Confidence", {}).get("N", 0)),
    )
