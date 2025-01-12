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
        bounding_box: dict,
        top_right: dict,
        top_left: dict,
        bottom_right: dict,
        bottom_left: dict,
        angle_degrees: float,
        angle_radians: float,
        confidence: float,
        tags: list[str] = None,
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
        assert_valid_boundingBox(bounding_box)
        self.bounding_box = bounding_box
        assert_valid_point(top_right)
        self.top_right = top_right
        assert_valid_point(top_left)
        self.top_left = top_left
        assert_valid_point(bottom_right)
        self.bottom_right = bottom_right
        assert_valid_point(bottom_left)
        self.bottom_left = bottom_left
        if not isinstance(angle_degrees, (float, int)):
            raise ValueError(
                f"angle_degrees must be a float or int got: {angle_degrees}"
            )
        self.angle_degrees = angle_degrees
        if not isinstance(angle_radians, (float, int)):
            raise ValueError(
                "angle_radians must be a float or int got: ", angle_radians
            )
        self.angle_radians = angle_radians
        if confidence <= 0 or confidence > 1:
            raise ValueError("confidence must be a float between 0 and 1")
        self.confidence = confidence
        if tags is not None and not isinstance(tags, list):
            raise ValueError("tags must be a list")
        self.tags = tags if tags is not None else []

    def key(self) -> dict:
        return {
            "PK": {"S": f"IMAGE#{self.image_id:05d}"},
            "SK": {"S": f"LINE#{self.line_id:05d}#WORD#{self.id:05d}"},
        }

    def to_item(self) -> dict:
        item = {
            **self.key(),
            "TYPE": {"S": "WORD"},
            "text": {"S": self.text},
            "bounding_box": {
                "M": {
                    "x": {"N": _format_float(self.bounding_box["x"], 18, 20)},
                    "y": {"N": _format_float(self.bounding_box["y"], 18, 20)},
                    "width": {"N": _format_float(self.bounding_box["width"], 18, 20)},
                    "height": {"N": _format_float(self.bounding_box["height"], 18, 20)},
                }
            },
            "top_right": {
                "M": {
                    "x": {"N": _format_float(self.top_right["x"], 18, 20)},
                    "y": {"N": _format_float(self.top_right["y"], 18, 20)},
                }
            },
            "top_left": {
                "M": {
                    "x": {"N": _format_float(self.top_left["x"], 18, 20)},
                    "y": {"N": _format_float(self.top_left["y"], 18, 20)},
                }
            },
            "bottom_right": {
                "M": {
                    "x": {"N": _format_float(self.bottom_right["x"], 18, 20)},
                    "y": {"N": _format_float(self.bottom_right["y"], 18, 20)},
                }
            },
            "bottom_left": {
                "M": {
                    "x": {"N": _format_float(self.bottom_left["x"], 18, 20)},
                    "y": {"N": _format_float(self.bottom_left["y"], 18, 20)},
                }
            },
            "angle_degrees": {"N": _format_float(self.angle_degrees, 10, 12)},
            "angle_radians": {"N": _format_float(self.angle_radians, 10, 12)},
            "confidence": {"N": _format_float(self.confidence, 2, 2)},
        }
        if self.tags:
            item["tags"] = {"SS": self.tags}
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
        yield "bounding_box", self.bounding_box
        yield "top_right", self.top_right
        yield "top_left", self.top_left
        yield "bottom_right", self.bottom_right
        yield "bottom_left", self.bottom_left
        yield "angle_degrees", self.angle_degrees
        yield "angle_radians", self.angle_radians
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
            and self.bounding_box == other.bounding_box
            and self.top_right == other.top_right
            and self.top_left == other.top_left
            and self.bottom_right == other.bottom_right
            and self.bottom_left == other.bottom_left
            and self.angle_degrees == other.angle_degrees
            and self.angle_radians == other.angle_radians
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
        image_id=int(item["PK"]["S"][6:]),
        line_id=int(item["SK"]["S"].split("#")[1]),
        id=int(item["SK"]["S"].split("#")[3]),
        text=item["text"]["S"],
        bounding_box={
            key: float(value["N"]) for key, value in item["bounding_box"]["M"].items()
        },
        top_right={
            key: float(value["N"]) for key, value in item["top_right"]["M"].items()
        },
        top_left={
            key: float(value["N"]) for key, value in item["top_left"]["M"].items()
        },
        bottom_right={
            key: float(value["N"]) for key, value in item["bottom_right"]["M"].items()
        },
        bottom_left={
            key: float(value["N"]) for key, value in item["bottom_left"]["M"].items()
        },
        angle_degrees=float(item["angle_degrees"]["N"]),
        angle_radians=float(item["angle_radians"]["N"]),
        confidence=float(item["confidence"]["N"]),
        tags=item.get("tags", {}).get("SS", []),
    )
