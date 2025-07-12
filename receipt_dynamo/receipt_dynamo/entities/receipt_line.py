from dataclasses import dataclass
from math import atan2, pi
from typing import Any, Dict, Tuple

from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.geometry_base import GeometryMixin
from receipt_dynamo.entities.receipt_word import EmbeddingStatus
from receipt_dynamo.entities.util import (
    _format_float,
    _repr_str,
    assert_valid_bounding_box,
    assert_valid_point,
    assert_valid_uuid,
)


@dataclass(eq=True, unsafe_hash=False)
class ReceiptLine(GeometryMixin, DynamoDBEntity):
    """
    Represents a receipt line and its associated metadata stored in a DynamoDB table.

    This class encapsulates receipt line-related information such as the receipt identifier,
    image UUID, text content, geometric properties, rotation angles, and detection confidence.
    It is designed to support operations such as generating DynamoDB keys and converting the
    receipt line to a DynamoDB item.

    Attributes:
        receipt_id (int): Identifier for the receipt.
        image_id (str): UUID identifying the image to which the receipt line belongs.
        id (int): Identifier for the receipt line.
        text (str): The text content of the receipt line.
        bounding_box (dict): The bounding box of the receipt line with keys 'x', 'y', 'width', and 'height'.
        top_right (dict): The top-right corner coordinates with keys 'x' and 'y'.
        top_left (dict): The top-left corner coordinates with keys 'x' and 'y'.
        bottom_right (dict): The bottom-right corner coordinates with keys 'x' and 'y'.
        bottom_left (dict): The bottom-left corner coordinates with keys 'x' and 'y'.
        angle_degrees (float): The angle of the receipt line in degrees.
        angle_radians (float): The angle of the receipt line in radians.
        confidence (float): The confidence level of the receipt line (between 0 and 1).
        embedding_status (EmbeddingStatus): The status of the embedding for the receipt line.
    """

    receipt_id: int
    image_id: str
    line_id: int
    text: str
    bounding_box: Dict[str, Any]
    top_right: Dict[str, Any]
    top_left: Dict[str, Any]
    bottom_right: Dict[str, Any]
    bottom_left: Dict[str, Any]
    angle_degrees: float
    angle_radians: float
    confidence: float
    embedding_status: EmbeddingStatus | str = EmbeddingStatus.NONE

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        if not isinstance(self.receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if self.receipt_id <= 0:
            raise ValueError("receipt_id must be positive")

        assert_valid_uuid(self.image_id)

        if not isinstance(self.line_id, int):
            raise ValueError("id must be an integer")
        if self.line_id <= 0:
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
        if not 0.0 < self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

        if isinstance(self.embedding_status, EmbeddingStatus):
            self.embedding_status = self.embedding_status.value
        elif isinstance(self.embedding_status, str):
            if self.embedding_status not in [s.value for s in EmbeddingStatus]:
                error_message = (
                    "embedding_status must be one of: "
                    f"{', '.join(s.value for s in EmbeddingStatus)}\n"
                    f"Got: {self.embedding_status}"
                )
                raise ValueError(error_message)
        else:
            raise ValueError(
                "embedding_status must be an EmbeddingStatus or a string"
            )

    @property
    def key(self) -> Dict[str, Any]:
        """
        Generates the primary key for the receipt line.

        Returns:
            dict: The primary key for the receipt line.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": f"RECEIPT#{self.receipt_id:05d}#LINE#{self.line_id:05d}"
            },
        }

    def gsi1_key(self) -> Dict[str, Any]:
        """
        Generates the secondary index key for the receipt line.
        """
        return {
            "GSI1PK": {"S": f"EMBEDDING_STATUS#{self.embedding_status}"},
            "GSI1SK": {
                "S": (
                    f"IMAGE#{self.image_id}#"
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"LINE#{self.line_id:05d}"
                )
            },
        }

    def to_item(self) -> Dict[str, Any]:
        """
        Converts the ReceiptLine object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the ReceiptLine object as a
                DynamoDB item.
        """
        return {
            **self.key,
            **self.gsi1_key(),
            "TYPE": {"S": "RECEIPT_LINE"},
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
            "embedding_status": {"S": self.embedding_status},
        }

    def __repr__(self) -> str:
        return (
            f"ReceiptLine("
            f"receipt_id={self.receipt_id}, "
            f"image_id={_repr_str(self.image_id)}, "
            f"line_id={self.line_id}, "
            f"text='{self.text}', "
            f"bounding_box={self.bounding_box}, "
            f"top_right={self.top_right}, "
            f"top_left={self.top_left}, "
            f"bottom_right={self.bottom_right}, "
            f"bottom_left={self.bottom_left}, "
            f"angle_degrees={self.angle_degrees}, "
            f"angle_radians={self.angle_radians}, "
            f"confidence={self.confidence}, "
            f"embedding_status={self.embedding_status}"
            f")"
        )

    def __hash__(self) -> int:
        """Returns the hash value of the ReceiptLine object."""
        return hash(
            (
                self.receipt_id,
                self.image_id,
                self.line_id,
                self.text,
                tuple(self.bounding_box.items()),
                tuple(self.top_right.items()),
                tuple(self.top_left.items()),
                tuple(self.bottom_right.items()),
                tuple(self.bottom_left.items()),
                self.angle_degrees,
                self.angle_radians,
                self.confidence,
                self.embedding_status,
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
        Receipt-specific inverse perspective transform from 'new' space back to 'old' space.

        This implementation uses the 2x2 linear system approach optimized for receipt
        coordinate systems, independent of the GeometryMixin's vision-based implementation.

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

            a11 = g * x_new_px - a
            a12 = h * x_new_px - b
            b1 = c - x_new_px

            a21 = g * y_new_px - d
            a22 = h * y_new_px - e
            b2 = f - y_new_px

            # Solve the 2×2 linear system via determinant
            det = a11 * a22 - a12 * a21
            if abs(det) < 1e-12:
                # Degenerate or singular.  You can raise an exception or skip.
                # For robust code, handle it gracefully:
                raise ValueError(
                    "Inverse perspective transform is singular for this corner."
                )

            x_old_px = (b1 * a22 - b2 * a12) / det
            y_old_px = (a11 * b2 - a21 * b1) / det

            # 3) Convert old pixel coords -> old normalized coords in [0..1]
            corner["x"] = x_old_px / src_width
            corner["y"] = y_old_px / src_height

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


def item_to_receipt_line(item: Dict[str, Any]) -> ReceiptLine:
    """
    Converts a DynamoDB item to a ReceiptLine object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptLine: The ReceiptLine object represented by the DynamoDB item.

    Raises:
        ValueError: When the item format is invalid or required keys are
            missing.
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
        # "embedding_status",
    }
    missing_keys = DynamoDBEntity.validate_keys(item, required_keys)
    if missing_keys:
        raise ValueError(f"Item is missing required keys: {missing_keys}")
    try:
        return ReceiptLine(
            image_id=item["PK"]["S"].split("#")[1],
            receipt_id=int(item["SK"]["S"].split("#")[1]),
            line_id=int(item["SK"]["S"].split("#")[3]),
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
            # embedding_status=item["embedding_status"]["S"],
        )
    except (KeyError, IndexError) as e:
        raise ValueError("Error converting item to ReceiptLine") from e
