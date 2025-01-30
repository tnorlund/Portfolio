from math import atan2
from typing import Generator, Tuple
from dynamo.entities.util import (
    assert_valid_uuid,
    assert_valid_bounding_box,
    assert_valid_point,
    _format_float,
    histogram,
)


class ReceiptWord:
    def __init__(
        self,
        receipt_id: int,
        image_id: str,
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
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if receipt_id <= 0:
            raise ValueError("receipt_id must be positive")
        self.receipt_id = receipt_id

        assert_valid_uuid(image_id)
        self.image_id = image_id

        if not isinstance(line_id, int):
            raise ValueError("line_id must be an integer")
        if line_id < 0:
            raise ValueError("line_id must be positive")
        self.line_id = line_id

        if not isinstance(id, int):
            raise ValueError("id must be an integer")
        if id < 0:
            raise ValueError("id must be positive")
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
            raise ValueError("angle_degrees must be a float or int")
        self.angle_degrees = angle_degrees

        if not isinstance(angle_radians, (float, int)):
            raise ValueError("angle_radians must be a float or int")
        self.angle_radians = angle_radians

        if isinstance(confidence, int):
            confidence = float(confidence)
        if not isinstance(confidence, float):
            raise ValueError("confidence must be a float")
        if confidence <= 0.0 or confidence > 1.0:
            raise ValueError("confidence must be between 0 and 1")
        self.confidence = confidence

        if tags is not None and not isinstance(tags, list):
            raise ValueError("tags must be a list")
        self.tags = tags if tags is not None else []

        self.histogram = histogram(self.text)
        self.num_chars = len(self.text)

    def key(self) -> dict:
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": f"RECEIPT#{self.receipt_id:05d}#LINE#{self.line_id:05d}#WORD#{self.id:05d}"
            },
        }

    def gsi2_key(self) -> dict:
        return {
            "GSI2PK": {"S": f"RECEIPT"},
            "GSI2SK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}#LINE#{self.line_id:05d}#WORD#{self.id:05d}"
            },
        }

    def to_item(self) -> dict:
        item = {
            **self.key(),
            **self.gsi2_key(),
            "TYPE": {"S": "RECEIPT_WORD"},
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
            "histogram": {"M": {k: {"N": str(v)} for k, v in self.histogram.items()}},
            "num_chars": {"N": str(self.num_chars)},
        }
        if self.tags:
            item["tags"] = {"SS": self.tags}
        return item

    def __repr__(self) -> str:
        return f"ReceiptWord(id={self.id}, text='{self.text}')"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ReceiptWord):
            return False
        return (
            self.receipt_id == other.receipt_id
            and self.image_id == other.image_id
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
            and self.tags == other.tags
            and self.confidence == other.confidence
        )

    def __iter__(self) -> Generator[Tuple[str, str], None, None]:
        yield "image_id", self.image_id
        yield "line_id", self.line_id
        yield "receipt_id", self.receipt_id
        yield "id", self.id
        yield "text", self.text
        yield "bounding_box", self.bounding_box
        yield "top_right", self.top_right
        yield "top_left", self.top_left
        yield "bottom_right", self.bottom_right
        yield "bottom_left", self.bottom_left
        yield "angle_degrees", self.angle_degrees
        yield "angle_radians", self.angle_radians
        yield "tags", self.tags
        yield "confidence", self.confidence
        yield "histogram", self.histogram
        yield "num_chars", self.num_chars

    def calculate_centroid(self) -> Tuple[float, float]:
        """Calculates the centroid of the line

        Returns:
            Tuple[float, float]: The x and y coordinates of the centroid
        """
        x = (
            self.top_right["x"]
            + self.top_left["x"]
            + self.bottom_right["x"]
            + self.bottom_left["x"]
        ) / 4
        y = (
            self.top_right["y"]
            + self.top_left["y"]
            + self.bottom_right["y"]
            + self.bottom_left["y"]
        ) / 4
        return x, y

    def distance_and_angle_from_ReceiptWord(
        self, other: "ReceiptWord"
    ) -> Tuple[float, float]:
        """Calculates the distance and the angle between the two words

        Args:
            other (ReceiptWord): The other word

        Returns:
            Tuple[float, float]: The distance and angle between the two words
        """
        x1, y1 = self.calculate_centroid()
        x2, y2 = other.calculate_centroid()
        distance = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        angle = atan2(y2 - y1, x2 - x1)
        return distance, angle


def itemToReceiptWord(item: dict) -> ReceiptWord:
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
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        raise ValueError(f"Item is missing required keys: {missing_keys}")
    try:
        return ReceiptWord(
            receipt_id=int(item["SK"]["S"].split("#")[1]),
            image_id=item["PK"]["S"].split("#")[1],
            line_id=int(item["SK"]["S"].split("#")[3]),
            id=int(item["SK"]["S"].split("#")[5]),
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
    except (KeyError, ValueError) as e:
        raise ValueError(f"Error converting item to ReceiptWord") from e
