from math import atan2, pi
from typing import Any, Dict, Generator, Tuple

from receipt_dynamo.entities.util import (
    _format_float,
    _repr_str,
    assert_valid_bounding_box,
    assert_valid_point,
    assert_valid_uuid,
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
        letter_id (int): Identifier for the receipt letter.
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
        letter_id: int,
        text: str,
        bounding_box: Dict[str, Any],
        top_right: Dict[str, Any],
        top_left: Dict[str, Any],
        bottom_right: Dict[str, Any],
        bottom_left: Dict[str, Any],
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
            letter_id (int): Identifier for the receipt letter.
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
        self.receipt_id: int = receipt_id

        assert_valid_uuid(image_id)
        self.image_id = image_id

        if not isinstance(line_id, int):
            raise ValueError("line_id must be an integer")
        if line_id < 0:
            raise ValueError("line_id must be positive")
        self.line_id: int = line_id

        if not isinstance(word_id, int):
            raise ValueError("word_id must be an integer")
        if word_id < 0:
            raise ValueError("word_id must be positive")
        self.word_id: int = word_id

        if not isinstance(letter_id, int):
            raise ValueError("letter_id must be an integer")
        if letter_id < 0:
            raise ValueError("letter_id must be positive")
        self.letter_id: int = letter_id

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
        self.angle_degrees: float = float(angle_degrees)

        if not isinstance(angle_radians, (float, int)):
            raise ValueError("angle_radians must be a float or int")
        self.angle_radians: float = float(angle_radians)

        if isinstance(confidence, int):
            confidence = float(confidence)
        if not isinstance(confidence, float):
            raise ValueError("confidence must be a float")
        if confidence <= 0.0 or confidence > 1.0:
            raise ValueError("confidence must be between 0 and 1")
        self.confidence = confidence

    @property
    def key(self) -> Dict[str, Any]:
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
                    f"LETTER#{self.letter_id:05d}"
                )
            },
        }

    def to_item(self) -> Dict[str, Any]:
        """
        Converts the ReceiptLetter object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the ReceiptLetter object as a DynamoDB item.
        """
        return {
            **self.key,
            "TYPE": {"S": "RECEIPT_LETTER"},
            "text": {"S": self.text},
            "bounding_box": {
                "M": {
                    "x": {"N": _format_float(self.bounding_box["x"], 20, 22)},
                    "y": {"N": _format_float(self.bounding_box["y"], 20, 22)},
                    "width": {
                        "N": _format_float(self.bounding_box["width"], 20, 22)
                    },
                    "height": {
                        "N": _format_float(self.bounding_box["height"], 20, 22)
                    },
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
            and self.letter_id == other.letter_id
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

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """
        Returns an iterator over the ReceiptLetter object's attributes.

        Yields:
            Tuple[str, any]: A tuple containing the attribute name and its value.
        """
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "line_id", self.line_id
        yield "word_id", self.word_id
        yield "letter_id", self.letter_id
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
        return (
            f"ReceiptLetter("
            f"receipt_id={self.receipt_id}, "
            f"image_id={_repr_str(self.image_id)}, "
            f"line_id={self.line_id}, "
            f"word_id={self.word_id}, "
            f"letter_id={self.letter_id}, "
            f"text={_repr_str(self.text)}, "
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

    def __hash__(self) -> int:
        """
        Generates a hash value for the ReceiptLetter object.

        Returns:
            int: The hash value for the ReceiptLetter object.
        """
        return hash(
            (
                self.receipt_id,
                self.image_id,
                self.line_id,
                self.word_id,
                self.letter_id,
                self.text,
                tuple(self.bounding_box.items()),
                tuple(self.top_right.items()),
                tuple(self.top_left.items()),
                tuple(self.bottom_right.items()),
                tuple(self.bottom_left.items()),
                self.angle_degrees,
                self.angle_radians,
                self.confidence,
            )
        )

    def warp_transform(
        self,
        a: float,
        b: float,
        c: float,
        d: float,
        e: float,
        f: float,
        g: float,
        h: float,
        src_width: int,
        src_height: int,
        dst_width: int,
        dst_height: int,
        flip_y: bool = False,
    ):
        """
        Inverse perspective transform from 'new' space back to 'old' space.

        Args:
            a, b, c, d, e, f, g, h (float): The perspective coefficients that mapped
                the original image -> new image.  We will invert them here
                so we can map new coords -> old coords.
            src_width (int): The original (old) image width in pixels.
            src_height (int): The original (old) image height in pixels.
            dst_width (int): The new (warped) image width in pixels.
            dst_height (int): The new (warped) image height in pixels.
            flip_y (bool): If True, we treat the new coordinate system as flipped in Y
                (e.g. some OCR engines treat top=0).  Mirrors the logic in
                warp_affine_normalized_forward(...).
        """
        # For each corner in the new space, we want to find (x_old_px, y_old_px).
        # The forward perspective mapping was:
        #   x_new = (a*x_old + b*y_old + c) / (1 + g*x_old + h*y_old)
        #   y_new = (d*x_old + e*y_old + f) / (1 + g*x_old + h*y_old)
        #
        # We invert it by treating (x_new, y_new) as known, and solving
        # for (x_old, y_old).  The code below does that in a 2×2 linear system.

        corners = [
            self.top_left,
            self.top_right,
            self.bottom_left,
            self.bottom_right,
        ]

        for corner in corners:
            # 1) Convert normalized new coords -> pixel coords in the 'new'
            # (warped) image
            x_new_px = corner["x"] * dst_width
            y_new_px = corner["y"] * dst_height

            if flip_y:
                # If the new system’s Y=0 was at the top, then from the perspective
                # of a typical "bottom=0" system, we flip:
                y_new_px = dst_height - y_new_px

            # 2) Solve the perspective equations for old pixel coords (X_old, Y_old).
            # We have the system:
            #   x_new_px = (a*X_old + b*Y_old + c) / (1 + g*X_old + h*Y_old)
            #   y_new_px = (d*X_old + e*Y_old + f) / (1 + g*X_old + h*Y_old)
            #
            # Put it in the form:
            #    (g*x_new_px - a)*X_old + (h*x_new_px - b)*Y_old = c - x_new_px
            #    (g*y_new_px - d)*X_old + (h*y_new_px - e)*Y_old = f - y_new_px

            A11 = g * x_new_px - a
            A12 = h * x_new_px - b
            B1 = c - x_new_px

            A21 = g * y_new_px - d
            A22 = h * y_new_px - e
            B2 = f - y_new_px

            # Solve the 2×2 linear system via determinant
            det = A11 * A22 - A12 * A21
            if abs(det) < 1e-12:
                # Degenerate or singular.  You can raise an exception or skip.
                # For robust code, handle it gracefully:
                raise ValueError(
                    "Inverse perspective transform is singular for this corner."
                )

            X_old_px = (B1 * A22 - B2 * A12) / det
            Y_old_px = (A11 * B2 - A21 * B1) / det

            # 3) Convert old pixel coords -> old normalized coords in [0..1]
            corner["x"] = X_old_px / src_width
            corner["y"] = Y_old_px / src_height

            if flip_y:
                # If the old/original system also had Y=0 at top, do the final
                # flip:
                corner["y"] = 1.0 - corner["y"]

        # 4) Recompute bounding box + angle
        xs = [pt["x"] for pt in corners]
        ys = [pt["y"] for pt in corners]
        self.bounding_box["x"] = min(xs)
        self.bounding_box["y"] = min(ys)
        self.bounding_box["width"] = max(xs) - min(xs)
        self.bounding_box["height"] = max(ys) - min(ys)

        dx = self.top_right["x"] - self.top_left["x"]
        dy = self.top_right["y"] - self.top_left["y"]
        angle_radians = atan2(dy, dx)
        self.angle_radians = angle_radians
        self.angle_degrees = angle_radians * 180.0 / pi


def item_to_receipt_letter(item: Dict[str, Any]) -> ReceiptLetter:
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
            letter_id=int(item["SK"]["S"].split("#")[7]),
            text=item["text"]["S"],
            bounding_box={
                key: float(value["N"])
                for key, value in item["bounding_box"]["M"].items()
            },
            top_right={
                key: float(value["N"])
                for key, value in item["top_right"]["M"].items()
            },
            top_left={
                key: float(value["N"])
                for key, value in item["top_left"]["M"].items()
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
