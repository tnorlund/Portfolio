from typing import Generator, Tuple
from dynamo.entities.util import (
    assert_valid_bounding_box,
    assert_valid_point,
    _format_float,
    map_to_dict
)


class ReceiptLetter:
    def __init__(
        self,
        receipt_id: int,
        image_id: int,
        line_id: int,
        word_id: int,
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
    ):
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        # Ensure the Receipt ID is a positive integer
        if receipt_id <= 0:
            raise ValueError("receipt_id must be positive")
        self.receipt_id = receipt_id
        # Ensure the Image ID is a positive integer
        if image_id <= 0 or not isinstance(image_id, int):
            raise ValueError("image_id must be a positive integer")
        self.image_id = image_id
        if line_id <= 0 or not isinstance(line_id, int):
            raise ValueError("line_id must be a positive integer")
        self.line_id = line_id
        if word_id <= 0 or not isinstance(word_id, int):
            raise ValueError("line_id must be a positive integer")
        self.word_id = word_id
        # Ensure the ID is a positive integer
        if id <= 0 or not isinstance(id, int):
            raise ValueError("id must be a positive integer")
        self.id = id
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        self.text = text
        assert_valid_bounding_box(bounding_box)
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
            raise ValueError("angleRadians must be a float or int got: ", angle_radians)
        self.angle_radians = angle_radians
        # Ensure the confidence is a float between 0 and 1
        if confidence <= 0 or confidence > 1:
            raise ValueError("confidence must be a float between 0 and 1")
        self.confidence = confidence

    def key(self) -> dict:
        return {
            "PK": {"S": f"IMAGE#{self.image_id:05d}"},
            "SK": {
                "S": f"RECEIPT#{self.receipt_id:05d}#LINE#{self.line_id:05d}#WORD#{self.word_id:05d}#LETTER#{self.id:05d}"
            },
        }

    def to_item(self) -> dict:
        return {
            **self.key(),
            "TYPE": {"S": "RECEIPT_LETTER"},
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

    def __eq__(self, other):
        if not isinstance(other, ReceiptLetter):
            return False
        return (
            self.receipt_id == other.receipt_id
            and self.image_id == other.image_id
            and self.line_id == other.line_id
            and self.word_id == other.word_id
            and self.id == other.id
            and self.text == other.text
            and self.bounding_box == other.bounding_box
            and self.top_right == other.top_right
            and self.top_left == other.top_left
            and self.bottom_right == other.bottom_right
            and self.bottom_left == other.bottom_left
            and self.angle_degrees == other.angle_degrees
            and self.angle_radians == other.angle_radians
        )

    def __iter__(self) -> Generator[Tuple[str, str], None, None]:
        yield "image_id", self.image_id
        yield "line_id", self.line_id
        yield "receipt_id", self.receipt_id
        yield "word_id", self.word_id
        yield "id", self.id
        yield "text", self.text
        yield "bounding_box", self.bounding_box
        yield "top_right", self.top_right
        yield "top_left", self.top_left
        yield "bottom_right", self.bottom_right
        yield "bottom_left", self.bottom_left
        yield "angle_degrees", self.angle_degrees
        yield "angle_radians", self.angle_radians
        yield "confidence", self.confidence


def itemToReceiptLetter(item: dict) -> ReceiptLetter:
    required_keys = {
        "PK",
        "SK",
        "text",
        "bounding_box",
        "top_right",
        "top_left",
        "bottom_right",
        "bottom_left",
        "angle_degrees",
        "angle_radians",
        "confidence",
    }
    if not required_keys.issubset(item):
        missing_keys = required_keys - set(item)
        raise ValueError(f"Item is missing required keys: {missing_keys}")
    try:
        return ReceiptLetter(
            receipt_id=int(item["SK"]["S"].split("#")[1]),
            image_id=int(item["PK"]["S"].split("#")[1]),
            line_id=int(item["SK"]["S"].split("#")[3]),
            word_id=int(item["SK"]["S"].split("#")[5]),
            id=int(item["SK"]["S"].split("#")[7]),
            text=item["text"]["S"],
            bounding_box=map_to_dict(item["bounding_box"]["M"]),
            top_right=map_to_dict(item["top_right"]["M"]),
            top_left=map_to_dict(item["top_left"]["M"]),
            bottom_right=map_to_dict(item["bottom_right"]["M"]),
            bottom_left=map_to_dict(item["bottom_left"]["M"]),
            angle_degrees=float(item["angle_degrees"]["N"]),
            angle_radians=float(item["angle_radians"]["N"]),
            confidence=float(item["confidence"]["N"]),
        )
    except (KeyError, ValueError) as e:
        raise ValueError(f"Error converting item to ReceiptLetter") from e
