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

class Word:
    def __init__(
        self,
        image_id: int,
        line_id: int,
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
        tags: list[str] = None
    ):
        if image_id <= 0 or not isinstance(image_id, int):
            raise ValueError("image_id must be a positive integer")
        self.image_id = image_id
        if line_id <= 0 or not isinstance(line_id, int):
            raise ValueError("line_id must be a positive integer")
        self.line_id = line_id
        if id <= 0 or not isinstance(id, int):
            raise ValueError("id must be a positive integer")
        self.id = id
        if not isinstance(text, str):
            raise ValueError("text must be a string")
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
        # Ensure the angleDegree is a float
        if not isinstance(angleDegrees, float):
            raise ValueError("angleDegrees must be a float")
        self.angleDegrees = angleDegrees
        # Ensure the angleRadians is a float
        if not isinstance(angleRadians, float):
            raise ValueError("angleRadians must be a float")
        self.angleRadians = angleRadians
        if confidence <= 0 or confidence > 1:
            raise ValueError("confidence must be a float between 0 and 1")
        self.confidence = confidence
        if tags is not None and not isinstance(tags, list):
            raise ValueError("tags must be a list")
        self.tags = tags if tags is not None else []
    
    def key(self) -> dict:
        return {
            "PK": {"S": f"IMAGE#{self.image_id:05d}"},
            "SK": {"S": f"LINE#{self.line_id:05d}#WORD#{self.id:05d}"}
        }
    
    def to_item(self) -> dict:
        item = {
            **self.key(),
            "Type": {"S": "WORD"},
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
        if self.tags:
            item["Tags"] = {"SS": self.tags}
        return item

    def __repr__(self):
        """Returns a string representation of the Word object
        
        Returns:
            str: The string representation of the Word object
        """
        return f"Word(id={int(self.id)}, text='{self.text}')"
    
    def __iter__(self) -> Generator[Tuple[str, str], None, None]:
        yield "image_id", self.image_id
        yield "line_id", self.line_id
        yield "id", self.id
        yield "text", self.text
        yield "tags", self.tags
        yield "boundingBox", self.boundingBox
        yield "topRight", self.topRight
        yield "topLeft", self.topLeft
        yield "bottomRight", self.bottomRight
        yield "bottomLeft", self.bottomLeft
        yield "angleDegrees", self.angleDegrees
        yield "angleRadians", self.angleRadians
        yield "tags", self.tags
        yield "confidence", self.confidence

    def __eq__(self, other: object) -> bool:
        """Compares two Word objects
        
        Args:
            other (object): The object to compare
        
        Returns:
            bool: True if the objects are equal, False otherwise
        """
        if not isinstance(other, Word):
            return False
        return (
            self.image_id == other.image_id
            and self.line_id == other.line_id
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
            and self.tags == other.tags
        )
        
    
def itemToWord(item: dict) -> Word:
    """Converts a DynamoDB item to a Word object
    
    Args:
        item (dict): The DynamoDB item to convert
    
    Returns:
        Word: The Word object created from the item
    """
    return Word(
        int(item["PK"]["S"].split("#")[1]),
        int(item["SK"]["S"].split("#")[1]),
        int(item["SK"]["S"].split("#")[3]),
        item["Text"]["S"],
        map_to_dict(item["BoundingBox"]["M"]),
        map_to_dict(item["TopRight"]["M"]),
        map_to_dict(item["TopLeft"]["M"]),
        map_to_dict(item["BottomRight"]["M"]),
        map_to_dict(item["BottomLeft"]["M"]),
        float(item["AngleDegrees"]["N"]),
        float(item["AngleRadians"]["N"]),
        float(item["Confidence"]["N"]),
        item.get("Tags", {}).get("SS", [])
    )
