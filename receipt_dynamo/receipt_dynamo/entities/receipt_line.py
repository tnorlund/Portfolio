from dataclasses import dataclass
from math import atan2, pi
from typing import Any, Dict

from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.geometry_base import GeometryMixin
from receipt_dynamo.entities.receipt_word import EmbeddingStatus
from receipt_dynamo.entities.util import (
    _format_float,
    _repr_str,
    assert_valid_bounding_box,
    assert_valid_point,
    assert_valid_uuid,
    deserialize_bounding_box,
    deserialize_confidence,
    deserialize_coordinate_point,
    serialize_bounding_box,
    serialize_confidence,
    serialize_coordinate_point,
)


@dataclass(eq=True, unsafe_hash=False)
class ReceiptLine(GeometryMixin, DynamoDBEntity):
    """Receipt line metadata stored in DynamoDB.

    This class encapsulates receipt line information such as the receipt
    identifier, image UUID, text, geometric properties, and rotation angles.
    It includes detection confidence and supports generating DynamoDB keys
    before converting the line to a DynamoDB item.

    Attributes:
        receipt_id (int): Identifier for the receipt.
        image_id (str): UUID identifying the image to which the receipt line
            belongs.
        line_id (int): Identifier for the receipt line.
        text (str): The text content of the receipt line.
        bounding_box (dict): Bounding box with keys ``x``, ``y``, ``width`` and
            ``height``.
        top_right (dict): The top-right corner with keys ``x`` and ``y``.
        top_left (dict): The top-left corner with keys ``x`` and ``y``.
        bottom_right (dict): The bottom-right corner with keys ``x`` and ``y``.
        bottom_left (dict): The bottom-left corner with keys ``x`` and ``y``.
        angle_degrees (float): The angle of the receipt line in degrees.
        angle_radians (float): The angle of the receipt line in radians.
        confidence (float): Confidence level of the line between 0 and 1.
        embedding_status (EmbeddingStatus): Embedding status for the line.
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
            allowed_statuses = [s.value for s in EmbeddingStatus]
            if self.embedding_status not in allowed_statuses:
                error_message = (
                    "embedding_status must be one of: "
                    f"{', '.join(allowed_statuses)}\n"
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
            "bounding_box": serialize_bounding_box(self.bounding_box),
            "top_right": serialize_coordinate_point(self.top_right),
            "top_left": serialize_coordinate_point(self.top_left),
            "bottom_right": serialize_coordinate_point(self.bottom_right),
            "bottom_left": serialize_coordinate_point(self.bottom_left),
            "angle_degrees": {
                "N": _format_float(
                    self.angle_degrees,
                    18,
                    20,
                )
            },
            "angle_radians": {
                "N": _format_float(
                    self.angle_radians,
                    18,
                    20,
                )
            },
            "confidence": serialize_confidence(self.confidence),
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
        """Inverse perspective transform from the warped space back to
        original.

        This uses a 2x2 linear system tailored for receipts and is
        independent of the GeometryMixin implementation.

        Args:
            a, b, c, d, e, f, g, h (float): Coefficients that mapped the
                original image to the warped image. They are inverted so we
                can map warped coordinates back to the original space.
            src_width (int): Original image width in pixels.
            src_height (int): Original image height in pixels.
            dst_width (int): Warped image width in pixels.
            dst_height (int): Warped image height in pixels.
            flip_y (bool): Treat the new coordinate system as flipped in Y.
                This mirrors ``warp_affine_normalized_forward``.
        """
        # For each corner in the warped space we need (x_old_px, y_old_px).
        # The forward mapping was:
        #   x_new = (a*x_old + b*y_old + c) / (1 + g*x_old + h*y_old)
        #   y_new = (d*x_old + e*y_old + f) / (1 + g*x_old + h*y_old)
        # We invert it by treating (x_new, y_new) as known and solving for
        # (x_old, y_old) using a 2×2 linear system.

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
                # The new system has Y=0 at the top. Flip to match a
                # bottom=0 coordinate system.
                y_new_px = dst_height - y_new_px

            # 2) Solve for the original pixel coordinates (X_old, Y_old).
            # System:
            #   x_new_px = (a*X_old + b*Y_old + c) / (1 + g*X_old + h*Y_old)
            #   y_new_px = (d*X_old + e*Y_old + f) / (1 + g*X_old + h*Y_old)
            # Put it in the form:
            #   (g*x_new_px - a)*X_old + (h*x_new_px - b)*Y_old = c - x_new_px
            #   (g*y_new_px - d)*X_old + (h*y_new_px - e)*Y_old = f - y_new_px

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
                    "Inverse perspective transform is "
                    "singular for this corner."
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
            bounding_box=deserialize_bounding_box(item["bounding_box"]),
            top_right=deserialize_coordinate_point(item["top_right"]),
            top_left=deserialize_coordinate_point(item["top_left"]),
            bottom_right=deserialize_coordinate_point(item["bottom_right"]),
            bottom_left=deserialize_coordinate_point(item["bottom_left"]),
            angle_degrees=float(item["angle_degrees"]["N"]),
            angle_radians=float(item["angle_radians"]["N"]),
            confidence=deserialize_confidence(item["confidence"]),
            # embedding_status=item["embedding_status"]["S"],
        )
    except (KeyError, IndexError) as e:
        raise ValueError("Error converting item to ReceiptLine") from e
