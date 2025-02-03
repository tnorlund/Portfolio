from typing import Generator, Tuple
from dynamo.entities.util import (
    assert_valid_uuid,
    assert_valid_bounding_box,
    assert_valid_point,
    _format_float,
)


class ReceiptLetter:
    """
    Represents a receipt letter and its associated metadata stored in a DynamoDB table.

    This class encapsulates receipt letter-related information such as the receipt identifier,
    image UUID, line identifier, word identifier, letter identifier, text content (exactly one character),
    geometric properties, rotation angles, and detection confidence. It is designed to support operations
    such as generating DynamoDB keys and converting the receipt letter to a DynamoDB item.

    Attributes:
        receipt_id (int): Identifier for the receipt.
        image_id (str): UUID identifying the image to which the receipt letter belongs.
        line_id (int): Identifier for the receipt line.
        word_id (int): Identifier for the receipt word that this letter belongs to.
        id (int): Identifier for the receipt letter.
        text (str): The text content of the receipt letter (must be exactly one character).
        bounding_box (dict): The bounding box of the receipt letter with keys 'x', 'y', 'width', and 'height'.
        top_right (dict): The top-right corner coordinates with keys 'x' and 'y'.
        top_left (dict): The top-left corner coordinates with keys 'x' and 'y'.
        bottom_right (dict): The bottom-right corner coordinates with keys 'x' and 'y'.
        bottom_left (dict): The bottom-left corner coordinates with keys 'x' and 'y'.
        angle_degrees (float): The angle of the receipt letter in degrees.
        angle_radians (float): The angle of the receipt letter in radians.
        confidence (float): The confidence level of the receipt letter (between 0 and 1).
    """

    def __init__(
        self,
        receipt_id: int,
        image_id: str,
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
        """
        Initializes a new ReceiptLetter object for DynamoDB.

        Args:
            receipt_id (int): Identifier for the receipt.
            image_id (str): UUID identifying the image to which the receipt letter belongs.
            line_id (int): Identifier for the receipt line.
            word_id (int): Identifier for the receipt word.
            id (int): Identifier for the receipt letter.
            text (str): The text content of the receipt letter. Must be exactly one character.
            bounding_box (dict): The bounding box of the receipt letter with keys 'x', 'y', 'width', and 'height'.
            top_right (dict): The top-right corner coordinates with keys 'x' and 'y'.
            top_left (dict): The top-left corner coordinates with keys 'x' and 'y'.
            bottom_right (dict): The bottom-right corner coordinates with keys 'x' and 'y'.
            bottom_left (dict): The bottom-left corner coordinates with keys 'x' and 'y'.
            angle_degrees (float): The angle of the receipt letter in degrees.
            angle_radians (float): The angle of the receipt letter in radians.
            confidence (float): The confidence level of the receipt letter (between 0 and 1).

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

        if not isinstance(word_id, int):
            raise ValueError("word_id must be an integer")
        if word_id < 0:
            raise ValueError("word_id must be positive")
        self.word_id = word_id

        if not isinstance(id, int):
            raise ValueError("id must be an integer")
        if id < 0:
            raise ValueError("id must be positive")
        self.id = id

        if not isinstance(text, str):
            raise ValueError("text must be a string")
        if len(text) != 1:
            raise ValueError("text must be exactly one character")
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

    def key(self) -> dict:
        """
        Generates the primary key for the receipt letter.

        Returns:
            dict: The primary key for the receipt letter.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"LINE#{self.line_id:05d}#"
                    f"WORD#{self.word_id:05d}#"
                    f"LETTER#{self.id:05d}"
                )
            },
        }

    def to_item(self) -> dict:
        """
        Converts the ReceiptLetter object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the ReceiptLetter object as a DynamoDB item.
        """
        return {
            **self.key(),
            "TYPE": {"S": "RECEIPT_LETTER"},
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
        }

    def __eq__(self, other: object) -> bool:
        """
        Determines whether two ReceiptLetter objects are equal.

        Args:
            other (object): The object to compare.

        Returns:
            bool: True if the ReceiptLetter objects are equal, False otherwise.
        """
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
            and self.confidence == other.confidence
        )

    def __iter__(self) -> Generator[Tuple[str, any], None, None]:
        """
        Returns an iterator over the ReceiptLetter object's attributes.

        Yields:
            Tuple[str, any]: A tuple containing the attribute name and its value.
        """
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "line_id", self.line_id
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

    def __repr__(self) -> str:
        """
        Returns a string representation of the ReceiptLetter object.

        Returns:
            str: A string representation of the ReceiptLetter object.
        """
        return f"ReceiptLetter(id={self.id}, text='{self.text}')"


def itemToReceiptLetter(item: dict) -> ReceiptLetter:
    """
    Converts a DynamoDB item to a ReceiptLetter object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptLetter: The ReceiptLetter object represented by the DynamoDB item.

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
    if not required_keys.issubset(item):
        missing_keys = required_keys - set(item)
        raise ValueError(f"Item is missing required keys: {missing_keys}")
    try:
        return ReceiptLetter(
            receipt_id=int(item["SK"]["S"].split("#")[1]),
            image_id=item["PK"]["S"].split("#")[1],
            line_id=int(item["SK"]["S"].split("#")[3]),
            word_id=int(item["SK"]["S"].split("#")[5]),
            id=int(item["SK"]["S"].split("#")[7]),
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
        )
    except (KeyError, ValueError) as e:
        raise ValueError(f"Error converting item to ReceiptLetter: {e}") from e
