from typing import Generator, Tuple
from decimal import Decimal, ROUND_HALF_UP


def assert_valid_boundingBox(boundingBox):
    """
    Assert that the bounding box is valid.
    """
    if not isinstance(boundingBox, dict):
        raise ValueError("boundingBox must be a dictionary")
    for key in ["x", "y", "width", "height"]:
        if key not in boundingBox:
            raise ValueError(f"boundingBox must contain the key '{key}'")
        if not isinstance(boundingBox[key], (int, float)):
            raise ValueError(f"boundingBox['{key}'] must be a number")
    return boundingBox


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


def map_to_dict(map):
    """
    Convert a DynamoDB map to a dictionary.
    """
    return {key: float(value["N"]) for key, value in map.items()}


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


class Letter:
    def __init__(
        self,
        image_id: int,
        line_id: int,
        word_id: int,
        id: int,
        text: str,
        boundingBox: dict,
        topRight: dict,
        topLeft: dict,
        bottomRight: dict,
        bottomLeft: dict,
        angleDegrees: float,
        angleRadians: float,
        confidence: float,
    ):
        if image_id <= 0 or not isinstance(image_id, int):
            raise ValueError("image_id must be a positive integer")
        self.image_id = image_id
        if line_id <= 0 or not isinstance(line_id, int):
            raise ValueError("line_id must be a positive integer")
        self.line_id = line_id
        if word_id <= 0 or not isinstance(word_id, int):
            raise ValueError("word_id must be a positive integer")
        self.word_id = word_id
        if id <= 0 or not isinstance(id, int):
            raise ValueError("id must be a positive integer")
        self.id = id
        if text is None or len(text) != 1 or not isinstance(text, str):
            raise ValueError("text must be exactly one character")
        self.text = text
        assert_valid_boundingBox(boundingBox)
        self.boundingBox = boundingBox
        assert_valid_point(topRight)
        self.topRight = topRight
        assert_valid_point(topLeft)
        self.topLeft = topLeft
        assert_valid_point(bottomRight)
        self.bottomRight = bottomRight
        assert_valid_point(bottomLeft)
        self.bottomLeft = bottomLeft
        if not isinstance(angleDegrees, (float, int)):
            raise ValueError(f"angleDegrees must be a float or int got: {angleDegrees}")
        self.angleDegrees = angleDegrees
        if not isinstance(angleRadians, (float, int)):
            raise ValueError("angleRadians must be a float or int got: ", angleRadians)
        self.angleRadians = angleRadians
        if confidence <= 0 or confidence > 1:
            raise ValueError("confidence must be a float between 0 and 1")
        self.confidence = confidence

    def key(self) -> dict:
        return {
            "PK": {"S": f"IMAGE#{self.image_id:05d}"},
            "SK": {
                "S": f"LINE#{self.line_id:05d}#WORD#{self.word_id:05d}#LETTER#{self.id:05d}"
            },
        }

    def to_item(self) -> dict:
        return {
            **self.key(),
            "Type": {"S": "LETTER"},
            "Text": {"S": self.text},
            "BoundingBox": {
                "M": {
                    "x": {"N": _format_float(self.boundingBox["x"], 18, 20)},
                    "y": {"N": _format_float(self.boundingBox["y"], 18, 20)},
                    "width": {"N": _format_float(self.boundingBox["width"], 18, 20)},
                    "height": {"N": _format_float(self.boundingBox["height"], 18, 20)},
                }
            },
            "TopRight": {
                "M": {
                    "x": {"N": _format_float(self.topRight["x"], 18, 20)},
                    "y": {"N": _format_float(self.topRight["y"], 18, 20)},
                }
            },
            "TopLeft": {
                "M": {
                    "x": {"N": _format_float(self.topLeft["x"], 18, 20)},
                    "y": {"N": _format_float(self.topLeft["y"], 18, 20)},
                }
            },
            "BottomRight": {
                "M": {
                    "x": {"N": _format_float(self.bottomRight["x"], 18, 20)},
                    "y": {"N": _format_float(self.bottomRight["y"], 18, 20)},
                }
            },
            "BottomLeft": {
                "M": {
                    "x": {"N": _format_float(self.bottomLeft["x"], 18, 20)},
                    "y": {"N": _format_float(self.bottomLeft["y"], 18, 20)},
                }
            },
            "AngleDegrees": {"N": _format_float(self.angleDegrees, 10, 12)},
            "AngleRadians": {"N": _format_float(self.angleRadians, 10, 12)},
            "Confidence": {"N": _format_float(self.confidence, 2, 2)},
        }

    def __repr__(self):
        """Returns a string representation of the Letter object

        Returns:
            str: The string representation of the Letter object
        """
        return f"Letter(id={self.id}, text='{self.text}')"

    def __iter__(self) -> Generator[Tuple[str, dict], None, None]:
        """Yields the Letter object as a series of key-value pairs"""
        yield "image_id", self.image_id
        yield "word_id", self.word_id
        yield "line_id", self.line_id
        yield "id", self.id
        yield "text", self.text
        yield "boundingBox", self.boundingBox
        yield "topRight", self.topRight
        yield "topLeft", self.topLeft
        yield "bottomRight", self.bottomRight
        yield "bottomLeft", self.bottomLeft
        yield "angleDegrees", self.angleDegrees
        yield "angleRadians", self.angleRadians
        yield "confidence", self.confidence

    def __eq__(self, other: object) -> bool:
        """Compares two Letter objects for equality

        Args:
            other (object): The object to compare

        Returns:
            bool: True if the objects are equal, False otherwise"""
        if not isinstance(other, Letter):
            return False
        return (
            self.image_id == other.image_id
            and self.line_id == other.line_id
            and self.word_id == other.word_id
            and self.id == other.id
            and self.text == other.text
            and self.boundingBox == other.boundingBox
            and self.topRight == other.topRight
            and self.topLeft == other.topLeft
            and self.bottomRight == other.bottomRight
            and self.bottomLeft == other.bottomLeft
            and self.angleDegrees == other.angleDegrees
            and self.angleRadians == other.angleRadians
            and self.confidence == other.confidence
        )


def itemToLetter(item: dict) -> Letter:
    return Letter(
        int(item["PK"]["S"].split("#")[1]),
        int(item["SK"]["S"].split("#")[1]),
        int(item["SK"]["S"].split("#")[3]),
        int(item["SK"]["S"].split("#")[5]),
        item["Text"]["S"],
        map_to_dict(item["BoundingBox"]["M"]),
        map_to_dict(item["TopRight"]["M"]),
        map_to_dict(item["TopLeft"]["M"]),
        map_to_dict(item["BottomRight"]["M"]),
        map_to_dict(item["BottomLeft"]["M"]),
        float(item["AngleDegrees"]["N"]),
        float(item["AngleRadians"]["N"]),
        float(item["Confidence"]["N"]),
    )
