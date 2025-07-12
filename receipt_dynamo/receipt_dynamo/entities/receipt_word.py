from dataclasses import dataclass
from math import atan2, degrees, pi, sqrt
from typing import Any, Dict, Generator, Optional, Tuple

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.geometry_base import GeometryMixin
from receipt_dynamo.entities.util import (
    _format_float,
    assert_valid_bounding_box,
    assert_valid_point,
    assert_valid_uuid,
)


@dataclass(eq=True, unsafe_hash=False)
class ReceiptWord(GeometryMixin, DynamoDBEntity):
    """
    Represents a receipt word and its associated metadata stored in a DynamoDB table.

    This class encapsulates receipt word-related information such as the receipt identifier,
    image UUID, line identifier, word identifier, text content, geometric properties, rotation angles,
    detection confidence, and character statistics. It is designed to support operations such as generating
    DynamoDB keys (including secondary indexes) and converting the receipt word to a DynamoDB item.

    Attributes:
        receipt_id (int): Identifier for the receipt.
        image_id (str): UUID identifying the image to which the receipt word belongs.
        line_id (int): Identifier for the receipt line.
        word_id (int): Identifier for the receipt word.
        text (str): The text content of the receipt word.
        bounding_box (dict): The bounding box of the receipt word with keys 'x', 'y', 'width', and 'height'.
        top_right (dict): The top-right corner coordinates with keys 'x' and 'y'.
        top_left (dict): The top-left corner coordinates with keys 'x' and 'y'.
        bottom_right (dict): The bottom-right corner coordinates with keys 'x' and 'y'.
        bottom_left (dict): The bottom-left corner coordinates with keys 'x' and 'y'.
        angle_degrees (float): The angle of the receipt word in degrees.
        angle_radians (float): The angle of the receipt word in radians.
        confidence (float): The confidence level of the receipt word (between 0 and 1).
        extracted_data (dict): The extracted data of the receipt word provided by Apple's NL API.
        embedding_status (str): The status of the embedding for the receipt word.
    """

    receipt_id: int
    image_id: str
    line_id: int
    word_id: int
    text: str
    bounding_box: Dict[str, Any]
    top_right: Dict[str, Any]
    top_left: Dict[str, Any]
    bottom_right: Dict[str, Any]
    bottom_left: Dict[str, Any]
    angle_degrees: float
    angle_radians: float
    confidence: float
    extracted_data: Optional[Dict[str, Any]] = None
    embedding_status: EmbeddingStatus | str = EmbeddingStatus.NONE
    is_noise: bool = False

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        if not isinstance(self.receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if self.receipt_id <= 0:
            raise ValueError("receipt_id must be positive")

        assert_valid_uuid(self.image_id)

        if not isinstance(self.line_id, int):
            raise ValueError("line_id must be an integer")
        if self.line_id < 0:
            raise ValueError("line_id must be positive")

        if not isinstance(self.word_id, int):
            raise ValueError("id must be an integer")
        if self.word_id < 0:
            raise ValueError("id must be positive")

        if not isinstance(self.text, str):
            raise ValueError("text must be a string")

        assert_valid_bounding_box(self.bounding_box)
        assert_valid_point(self.top_right)
        assert_valid_point(self.top_left)
        assert_valid_point(self.bottom_right)
        assert_valid_point(self.bottom_left)

        if not isinstance(self.angle_degrees, (float, int)):
            raise ValueError("angle_degrees must be a float or int")
        self.angle_degrees = float(self.angle_degrees)

        if not isinstance(self.angle_radians, (float, int)):
            raise ValueError("angle_radians must be a float or int")
        self.angle_radians = float(self.angle_radians)

        if isinstance(self.confidence, int):
            self.confidence = float(self.confidence)
        if not isinstance(self.confidence, float):
            raise ValueError("confidence must be a float")
        if self.confidence <= 0.0 or self.confidence > 1.0:
            raise ValueError("confidence must be between 0 and 1")

        if self.extracted_data is not None and not isinstance(
            self.extracted_data, dict
        ):
            raise ValueError("extracted_data must be a dict")

        # Normalize and validate embedding_status (allow enum or string)
        if isinstance(self.embedding_status, EmbeddingStatus):
            self.embedding_status = self.embedding_status.value
        elif isinstance(self.embedding_status, str):
            valid_values = [s.value for s in EmbeddingStatus]
            if self.embedding_status not in valid_values:
                raise ValueError(
                    f"embedding_status must be one of: {', '.join(valid_values)}\nGot: {self.embedding_status}"
                )
        else:
            raise ValueError(
                "embedding_status must be a string or EmbeddingStatus enum"
            )

        # Validate is_noise field
        if not isinstance(self.is_noise, bool):
            raise ValueError(
                f"is_noise must be a boolean, got {type(self.is_noise).__name__}"
            )

    @property
    def key(self) -> Dict[str, Any]:
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
                    f"WORD#{self.word_id:05d}"
                )
            },
        }

    def gsi1_key(self) -> Dict[str, Any]:
        """
        Generates the secondary index key for the receipt word.
        """
        return {
            "GSI1PK": {"S": f"EMBEDDING_STATUS#{self.embedding_status}"},
            "GSI1SK": {
                "S": (
                    f"IMAGE#{self.image_id}#"
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"LINE#{self.line_id:05d}#"
                    f"WORD#{self.word_id:05d}"
                )
            },
        }

    def gsi2_key(self) -> Dict[str, Any]:
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
                    f"WORD#{self.word_id:05d}"
                )
            },
        }

    def gsi3_key(self) -> Dict[str, Any]:
        """
        Generates the secondary index key for the receipt word.

        Returns:
            dict: The secondary index key for the receipt word.
        """
        return {
            "GSI3PK": {"S": f"IMAGE#{self.image_id}"},
            "GSI3SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"LINE#{self.line_id:05d}#"
                    f"WORD#{self.word_id:05d}"
                )
            },
        }

    def to_item(self) -> Dict[str, Any]:
        """
        Converts the ReceiptWord object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the ReceiptWord object as a DynamoDB item.
        """
        return {
            **self.key,
            **self.gsi1_key(),
            **self.gsi2_key(),
            **self.gsi3_key(),
            "TYPE": {"S": "RECEIPT_WORD"},
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
            "extracted_data": (
                {
                    "M": {
                        "type": {"S": self.extracted_data["type"]},
                        "value": {"S": self.extracted_data["value"]},
                    }
                }
                if self.extracted_data
                else {"NULL": True}
            ),
            "embedding_status": {"S": self.embedding_status},
            "is_noise": {"BOOL": self.is_noise},
        }

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
                # If the new system's Y=0 was at the top, then from the perspective
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
            f"word_id={self.word_id}, "
            f"text='{self.text}', "
            f"bounding_box={self.bounding_box}, "
            f"top_right={self.top_right}, "
            f"top_left={self.top_left}, "
            f"bottom_right={self.bottom_right}, "
            f"bottom_left={self.bottom_left}, "
            f"angle_degrees={self.angle_degrees}, "
            f"angle_radians={self.angle_radians}, "
            f"confidence={self.confidence}, "
            f"embedding_status='{self.embedding_status}', "
            f"is_noise={self.is_noise}"
            f")"
        )

    def distance_and_angle_from__receipt_word(
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

    def diff(self, other: "ReceiptWord") -> Dict[str, Any]:
        """
        Compare this ReceiptWord with another and return their differences.

        Args:
            other (ReceiptWord): The other ReceiptWord to compare with.

        Returns:
            dict: A dictionary containing the differences between the two ReceiptWord objects.
        """
        differences: Dict[str, Any] = {}
        for attr, value in sorted(self.__dict__.items()):
            other_value = getattr(other, attr)
            if other_value != value:
                if isinstance(value, dict) and isinstance(other_value, dict):
                    diff: Dict[str, Any] = {}
                    all_keys = set(value.keys()) | set(other_value.keys())
                    for k in all_keys:
                        if value.get(k) != other_value.get(k):
                            diff[k] = {
                                "self": value.get(k),
                                "other": other_value.get(k),
                            }
                    if diff:
                        differences[attr] = dict(sorted(diff.items()))
                else:
                    differences[attr] = {"self": value, "other": other_value}
        return differences

    def __hash__(self) -> int:
        """Returns the hash value of the ReceiptWord object."""
        return hash(
            (
                self.receipt_id,
                self.image_id,
                self.line_id,
                self.word_id,
                self.text,
                tuple(self.bounding_box.items()),
                tuple(self.top_right.items()),
                tuple(self.top_left.items()),
                tuple(self.bottom_right.items()),
                tuple(self.bottom_left.items()),
                self.angle_degrees,
                self.angle_radians,
                self.confidence,
                (
                    tuple(self.extracted_data.items())
                    if self.extracted_data
                    else None
                ),
                self.embedding_status,
                self.is_noise,
            )
        )


def item_to_receipt_word(item: Dict[str, Any]) -> ReceiptWord:
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
        # Safely extract embedding_status string from DynamoDB item (default to NONE)
        es_attr = item.get("embedding_status")
        if isinstance(es_attr, dict):
            es_val = es_attr.get("S", EmbeddingStatus.NONE.value)
        else:
            es_val = EmbeddingStatus.NONE.value

        return ReceiptWord(
            receipt_id=int(item["SK"]["S"].split("#")[1]),
            image_id=item["PK"]["S"].split("#")[1],
            line_id=int(item["SK"]["S"].split("#")[3]),
            word_id=int(item["SK"]["S"].split("#")[5]),
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
            extracted_data=(
                None
                if "NULL" in item.get("extracted_data", {})
                else {
                    "type": item.get("extracted_data", {})
                    .get("M", {})
                    .get("type", {})
                    .get("S"),
                    "value": item.get("extracted_data", {})
                    .get("M", {})
                    .get("value", {})
                    .get("S"),
                }
            ),
            embedding_status=es_val,
            is_noise=item.get("is_noise", {}).get("BOOL", False),
        )
    except (KeyError, ValueError) as e:
        raise ValueError("Error converting item to ReceiptWord") from e
