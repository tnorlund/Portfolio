from math import atan2
from typing import Generator, Tuple
from dynamo.entities.util import (
    assert_valid_uuid,
    assert_valid_bounding_box,
    assert_valid_point,
    _format_float,
    compute_histogram,
)


class ReceiptWord:
    """
    Represents a receipt word and its associated metadata stored in a DynamoDB table.

    This class encapsulates receipt word-related information such as the receipt identifier,
    image UUID, line identifier, word identifier, text content, geometric properties, rotation angles,
    detection confidence, and optional tags. It is designed to support operations such as generating
    DynamoDB keys (including secondary indexes) and converting the receipt word to a DynamoDB item.

    Attributes:
        receipt_id (int): Identifier for the receipt.
        image_id (str): UUID identifying the image to which the receipt word belongs.
        line_id (int): Identifier for the receipt line.
        id (int): Identifier for the receipt word.
        text (str): The text content of the receipt word.
        bounding_box (dict): The bounding box of the receipt word with keys 'x', 'y', 'width', and 'height'.
        top_right (dict): The top-right corner coordinates with keys 'x' and 'y'.
        top_left (dict): The top-left corner coordinates with keys 'x' and 'y'.
        bottom_right (dict): The bottom-right corner coordinates with keys 'x' and 'y'.
        bottom_left (dict): The bottom-left corner coordinates with keys 'x' and 'y'.
        angle_degrees (float): The angle of the receipt word in degrees.
        angle_radians (float): The angle of the receipt word in radians.
        confidence (float): The confidence level of the receipt word (between 0 and 1).
        tags (list[str]): Optional tags associated with the receipt word.
        histogram (dict): A histogram representing character frequencies in the text.
        num_chars (int): The number of characters in the receipt word.
    """

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
        histogram: dict = None,
        num_chars: int = None,
    ):
        """
        Initializes a new ReceiptWord object for DynamoDB.

        Args:
            receipt_id (int): Identifier for the receipt.
            image_id (str): UUID identifying the image to which the receipt word belongs.
            line_id (int): Identifier for the receipt line.
            id (int): Identifier for the receipt word.
            text (str): The text content of the receipt word.
            bounding_box (dict): The bounding box of the receipt word with keys 'x', 'y', 'width', and 'height'.
            top_right (dict): The top-right corner coordinates with keys 'x' and 'y'.
            top_left (dict): The top-left corner coordinates with keys 'x' and 'y'.
            bottom_right (dict): The bottom-right corner coordinates with keys 'x' and 'y'.
            bottom_left (dict): The bottom-left corner coordinates with keys 'x' and 'y'.
            angle_degrees (float): The angle of the receipt word in degrees.
            angle_radians (float): The angle of the receipt word in radians.
            confidence (float): The confidence level of the receipt word (between 0 and 1).
            tags (list[str], optional): A list of tags associated with the receipt word.
            histogram (dict, optional): A histogram representing character frequencies in the text.
            num_chars (int, optional): The number of characters in the receipt word.

        Raises:
            ValueError: If any parameter is of an invalid type or has an invalid value.
        """
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

        self.histogram = (
            compute_histogram(self.text) if histogram is None else histogram
        )
        self.num_chars = len(text) if num_chars is None else num_chars

    def key(self) -> dict:
        """
        Generates the primary key for the receipt word.

        Returns:
            dict: The primary key for the receipt word.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"LINE#{self.line_id:05d}#"
                    f"WORD#{self.id:05d}"
                )
            },
        }

    def gsi2_key(self) -> dict:
        """
        Generates the secondary index key for the receipt word.

        Returns:
            dict: The secondary index key for the receipt word.
        """
        return {
            "GSI2PK": {"S": "RECEIPT"},
            "GSI2SK": {
                "S": (
                    f"IMAGE#{self.image_id}#"
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"LINE#{self.line_id:05d}#"
                    f"WORD#{self.id:05d}"
                )
            },
        }

    def to_item(self) -> dict:
        """
        Converts the ReceiptWord object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the ReceiptWord object as a DynamoDB item.
        """
        item = {
            **self.key(),
            **self.gsi2_key(),
            "TYPE": {"S": "RECEIPT_WORD"},
            "text": {"S": self.text},
            "bounding_box": {
                "M": {
                    "x": {"N": _format_float(self.bounding_box["x"], 20, 22)},
                    "y": {"N": _format_float(self.bounding_box["y"], 20, 22)},
                    "width": {"N": _format_float(self.bounding_box["width"], 20, 22)},
                    "height": {"N": _format_float(self.bounding_box["height"], 20, 22)},
                }
            },
            "top_right": {
                "M": {
                    "x": {"N": _format_float(self.top_right["x"], 20, 22)},
                    "y": {"N": _format_float(self.top_right["y"], 20, 22)},
                }
            },
            "top_left": {
                "M": {
                    "x": {"N": _format_float(self.top_left["x"], 20, 22)},
                    "y": {"N": _format_float(self.top_left["y"], 20, 22)},
                }
            },
            "bottom_right": {
                "M": {
                    "x": {"N": _format_float(self.bottom_right["x"], 20, 22)},
                    "y": {"N": _format_float(self.bottom_right["y"], 20, 22)},
                }
            },
            "bottom_left": {
                "M": {
                    "x": {"N": _format_float(self.bottom_left["x"], 20, 22)},
                    "y": {"N": _format_float(self.bottom_left["y"], 20, 22)},
                }
            },
            "angle_degrees": {"N": _format_float(self.angle_degrees, 18, 20)},
            "angle_radians": {"N": _format_float(self.angle_radians, 18, 20)},
            "confidence": {"N": _format_float(self.confidence, 2, 2)},
            "histogram": {"M": {k: {"N": str(v)} for k, v in self.histogram.items()}},
            "num_chars": {"N": str(self.num_chars)},
        }
        if self.tags:
            item["tags"] = {"SS": self.tags}
        return item

    def __repr__(self) -> str:
        """
        Returns a string representation of the ReceiptWord object.

        Returns:
            str: A string representation of the ReceiptWord object.
        """

        return (
            f"ReceiptWord("
            f"receipt_id={self.receipt_id}, "
            f"image_id='{self.image_id}', "
            f"line_id={self.line_id}, "
            f"id={self.id}, "
            f"text='{self.text}', "
            f"bounding_box={self.bounding_box}, "
            f"top_right={self.top_right}, "
            f"top_left={self.top_left}, "
            f"bottom_right={self.bottom_right}, "
            f"bottom_left={self.bottom_left}, "
            f"angle_degrees={self.angle_degrees}, "
            f"angle_radians={self.angle_radians}, "
            f"confidence={self.confidence}"
            f")"
        )

    def __eq__(self, other: object) -> bool:
        """
        Determines whether two ReceiptWord objects are equal.

        Args:
            other (object): The object to compare.

        Returns:
            bool: True if the ReceiptWord objects are equal, False otherwise.
        """
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

    def __iter__(self) -> Generator[Tuple[str, any], None, None]:
        """
        Returns an iterator over the ReceiptWord object's attributes.

        Yields:
            Tuple[str, any]: A tuple containing the attribute name and its value.
        """
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
        """
        Calculates the centroid of the receipt word.

        Returns:
            Tuple[float, float]: The x and y coordinates of the centroid.
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
        """
        Calculates the distance and the angle between this receipt word and another receipt word.

        Args:
            other (ReceiptWord): The other receipt word.

        Returns:
            Tuple[float, float]: The distance and angle between the two receipt words.
        """
        x1, y1 = self.calculate_centroid()
        x2, y2 = other.calculate_centroid()
        distance = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        angle = atan2(y2 - y1, x2 - x1)
        return distance, angle


def itemToReceiptWord(item: dict) -> ReceiptWord:
    """
    Converts a DynamoDB item to a ReceiptWord object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptWord: The ReceiptWord object represented by the DynamoDB item.

    Raises:
        ValueError: When the item format is invalid or required keys are missing.
    """
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
        missing_keys = required_keys - set(item.keys())
        raise ValueError(f"Item is missing required keys: {missing_keys}")
    try:
        return ReceiptWord(
            receipt_id=int(item["SK"]["S"].split("#")[1]),
            image_id=item["PK"]["S"].split("#")[1],
            line_id=int(item["SK"]["S"].split("#")[3]),
            id=int(item["SK"]["S"].split("#")[5]),
            text=item["text"]["S"],
            bounding_box={
                key: float(value["N"])
                for key, value in item["bounding_box"]["M"].items()
            },
            top_right={
                key: float(value["N"]) for key, value in item["top_right"]["M"].items()
            },
            top_left={
                key: float(value["N"]) for key, value in item["top_left"]["M"].items()
            },
            bottom_right={
                key: float(value["N"])
                for key, value in item["bottom_right"]["M"].items()
            },
            bottom_left={
                key: float(value["N"])
                for key, value in item["bottom_left"]["M"].items()
            },
            angle_degrees=float(item["angle_degrees"]["N"]),
            angle_radians=float(item["angle_radians"]["N"]),
            confidence=float(item["confidence"]["N"]),
            tags=item.get("tags", {}).get("SS", []),
        )
    except (KeyError, ValueError) as e:
        raise ValueError("Error converting item to ReceiptWord") from e
