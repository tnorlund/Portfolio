# infra/lambda_layer/python/dynamo/entities/word.py
from dataclasses import dataclass
from math import atan2, cos, degrees, pi, radians, sin
from typing import Any, Dict, Generator, Optional, Tuple

from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.util import (
    _format_float,
    _repr_str,
    assert_type,
    assert_valid_bounding_box,
    assert_valid_point,
    assert_valid_uuid,
    format_type_error,
    shear_point,
)


@dataclass(eq=True, unsafe_hash=False)
class Word(DynamoDBEntity):
    """Represents a word extracted from an image for DynamoDB.

    This class encapsulates word-related information such as its unique
    identifiers, text content, geometric properties (bounding box and corner
    coordinates), rotation angles, detection confidence, character
    histogram, and character count. It supports operations such as generating
    DynamoDB keys and applying geometric transformations including translation,
    scaling, rotation, shear, and affine warping.

    Attributes:
        image_id (str): UUID identifying the image.
        line_id (int): Identifier for the line containing the word.
        word_id (int): Identifier for the word.
        text (str): The text of the word.
        bounding_box (dict): The bounding box of the word with keys 'x', 'y', 'width', and 'height'.
        top_right (dict): The top-right corner coordinates with keys 'x' and 'y'.
        top_left (dict): The top-left corner coordinates with keys 'x' and 'y'.
        bottom_right (dict): The bottom-right corner coordinates with keys 'x' and 'y'.
        bottom_left (dict): The bottom-left corner coordinates with keys 'x' and 'y'.
        angle_degrees (float): The angle of the word in degrees.
        angle_radians (float): The angle of the word in radians.
        confidence (float): The confidence level of the word (between 0 and 1).
        histogram (dict): A histogram representing character frequencies in the word.
        num_chars (int): The number of characters in the word.
    """

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

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        assert_valid_uuid(self.image_id)

        assert_type("line_id", self.line_id, int, ValueError)
        if self.line_id < 0:
            raise ValueError("line_id must be positive")

        assert_type("word_id", self.word_id, int, ValueError)
        if self.word_id < 0:
            raise ValueError("id must be positive")

        assert_type("text", self.text, str, ValueError)

        assert_valid_bounding_box(self.bounding_box)
        assert_valid_point(self.top_right)
        assert_valid_point(self.top_left)
        assert_valid_point(self.bottom_right)
        assert_valid_point(self.bottom_left)

        assert_type(
            "angle_degrees", self.angle_degrees, (float, int), ValueError
        )
        assert_type(
            "angle_radians", self.angle_radians, (float, int), ValueError
        )

        if isinstance(self.confidence, int):
            self.confidence = float(self.confidence)
        assert_type("confidence", self.confidence, float, ValueError)
        if self.confidence <= 0.0 or self.confidence > 1.0:
            raise ValueError("confidence must be between 0 and 1")

        if self.extracted_data is not None:
            assert_type(
                "extracted_data", self.extracted_data, dict, ValueError
            )

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the Word.

        Returns:
            dict: The primary key for the Word.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"LINE#{self.line_id:05d}#WORD#{self.word_id:05d}"},
        }

    def gsi2_key(self) -> Dict[str, Any]:
        """Generates the GSI2 key for the Word.

        Returns:
            dict: The GSI2 key for the Word.
        """
        return {
            "GSI2PK": {"S": f"IMAGE#{self.image_id}"},
            "GSI2SK": {
                "S": f"LINE#{self.line_id:05d}#WORD#{self.word_id:05d}"
            },
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the Word object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the Word object as a DynamoDB item.
        """
        item: Dict[str, Any] = {
            **self.key,
            **self.gsi2_key(),
            "TYPE": {"S": "WORD"},
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

        # Add extracted_data conditionally to avoid type conflicts
        if self.extracted_data:
            item["extracted_data"] = {
                "M": {
                    "type": {"S": self.extracted_data["type"]},
                    "value": {"S": self.extracted_data["value"]},
                }
            }
        else:
            item["extracted_data"] = {"NULL": True}

        return item

    def calculate_centroid(
        self,
        width: Optional[int] = None,
        height: Optional[int] = None,
        flip_y: bool = False,
    ) -> Tuple[float, float]:
        """Calculates the centroid of the Word.

        Args:
            width (int, optional): The width of the image to scale coordinates. Defaults to None.
            height (int, optional): The height of the image to scale coordinates. Defaults to None.
            flip_y (bool, optional): Whether to flip the y coordinate. Defaults to False.

        Returns:
            Tuple[float, float]: The (x, y) coordinates of the centroid.

        Raises:
            ValueError: If only one of width or height is provided.
        """
        if (width is None) != (height is None):
            raise ValueError("Both width and height must be provided together")

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

        if width is not None and height is not None:
            x *= width
            y *= height
            if flip_y:
                y = height - y

        return x, y

    def calculate_bounding_box(
        self,
        width: Optional[int] = None,
        height: Optional[int] = None,
        flip_y: bool = False,
    ) -> Tuple[float, float, float, float]:
        """Calculates the bounding box of the Word.

        Args:
            width (int, optional): The width of the image to scale coordinates. Defaults to None.
            height (int, optional): The height of the image to scale coordinates. Defaults to None.
            flip_y (bool, optional): Whether to flip the y coordinate. Defaults to False.

        Returns:
            Tuple[float, float, float, float]: The bounding box of the Word with keys 'x', 'y', 'width', and 'height'.

        Raises:
            ValueError: If only one of width or height is provided.
        """
        if (width is None) != (height is None):
            raise ValueError("Both width and height must be provided together")

        x = self.bounding_box["x"]
        y = self.bounding_box["y"]
        w = self.bounding_box["width"]
        h = self.bounding_box["height"]

        if width is not None and height is not None:
            x *= width
            y *= height
            if flip_y:
                y = height - y

        return x, y, w, h

    def calculate_corners(
        self,
        width: Optional[int] = None,
        height: Optional[int] = None,
        flip_y: bool = False,
    ) -> Tuple[
        Tuple[float, float],
        Tuple[float, float],
        Tuple[float, float],
        Tuple[float, float],
    ]:
        """Calculates the top-left, top-right, bottom-left, and bottom-right corners of the Word in image coordinates.

        Args:
            width (int, optional): The width of the image to scale coordinates. Defaults to None.
            height (int, optional): The height of the image to scale coordinates. Defaults to None.
            flip_y (bool, optional): Whether to flip the y coordinate. Defaults to False.

        Returns:
            Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float], Tuple[float, float]]: The corners of the Word.

        Raises:
            ValueError: If only one of width or height is provided.
        """
        if (width is None) != (height is None):
            raise ValueError("Both width and height must be provided together")

        if width is not None and height is not None:
            x_scale: float = float(width)
            y_scale: float = float(height)
        else:
            x_scale = y_scale = 1.0

        top_left_x = self.top_left["x"] * x_scale
        top_right_x = self.top_right["x"] * x_scale
        bottom_left_x = self.bottom_left["x"] * x_scale
        bottom_right_x = self.bottom_right["x"] * x_scale

        if flip_y:
            top_left_y = height - (self.top_left["y"] * y_scale)
            top_right_y = height - (self.top_right["y"] * y_scale)
            bottom_left_y = height - (self.bottom_left["y"] * y_scale)
            bottom_right_y = height - (self.bottom_right["y"] * y_scale)
        else:
            top_left_y = self.top_left["y"] * y_scale
            top_right_y = self.top_right["y"] * y_scale
            bottom_left_y = self.bottom_left["y"] * y_scale
            bottom_right_y = self.bottom_right["y"] * y_scale

        return (
            (top_left_x, top_left_y),
            (top_right_x, top_right_y),
            (bottom_left_x, bottom_left_y),
            (bottom_right_x, bottom_right_y),
        )

    def translate(self, x: float, y: float) -> None:
        """Translates the Word by the specified x and y offsets.

        Args:
            x (float): The offset to add to the x-coordinate.
            y (float): The offset to add to the y-coordinate.
        """
        self.top_right["x"] += x
        self.top_right["y"] += y
        self.top_left["x"] += x
        self.top_left["y"] += y
        self.bottom_right["x"] += x
        self.bottom_right["y"] += y
        self.bottom_left["x"] += x
        self.bottom_left["y"] += y
        self.bounding_box["x"] += x
        self.bounding_box["y"] += y

    def scale(self, sx: float, sy: float) -> None:
        """Scales the Word by the specified factors along the x and y axes.

        Args:
            sx (float): The scaling factor for the x-coordinate.
            sy (float): The scaling factor for the y-coordinate.
        """
        self.top_right["x"] *= sx
        self.top_right["y"] *= sy
        self.top_left["x"] *= sx
        self.top_left["y"] *= sy
        self.bottom_right["x"] *= sx
        self.bottom_right["y"] *= sy
        self.bottom_left["x"] *= sx
        self.bottom_left["y"] *= sy
        self.bounding_box["x"] *= sx
        self.bounding_box["y"] *= sy
        self.bounding_box["width"] *= sx
        self.bounding_box["height"] *= sy

    def rotate(
        self,
        angle: float,
        rotate_origin_x: float,
        rotate_origin_y: float,
        use_radians: bool = True,
    ) -> None:
        """Rotates the Word by the specified angle about a given origin.

        Only rotates if the angle is within:
            - [-π/2, π/2] when use_radians=True
            - [-90°, 90°] when use_radians=False

        Args:
            angle (float): The angle by which to rotate.
            rotate_origin_x (float): The x-coordinate of the rotation origin.
            rotate_origin_y (float): The y-coordinate of the rotation origin.
            use_radians (bool, optional): Whether the angle is in radians. Defaults to True.

        Raises:
            ValueError: If the angle is outside the allowed range.
        """

        if use_radians:
            if not (-pi / 2 <= angle <= pi / 2):
                raise ValueError(
                    f"Angle {angle} (radians) is outside the allowed range [-π/2, π/2]."
                )
            angle_radians = angle
        else:
            if not (-90 <= angle <= 90):
                raise ValueError(
                    f"Angle {angle} (degrees) is outside the allowed range [-90°, 90°]."
                )
            angle_radians = radians(angle)

        def rotate_point(px, py, ox, oy, theta):
            """Rotates point (px, py) around (ox, oy) by theta radians."""
            translated_x = px - ox
            translated_y = py - oy
            rotated_x = translated_x * cos(theta) - translated_y * sin(theta)
            rotated_y = translated_x * sin(theta) + translated_y * cos(theta)
            return rotated_x + ox, rotated_y + oy

        corners = [
            self.top_right,
            self.top_left,
            self.bottom_right,
            self.bottom_left,
        ]
        for corner in corners:
            x_new, y_new = rotate_point(
                corner["x"],
                corner["y"],
                rotate_origin_x,
                rotate_origin_y,
                angle_radians,
            )
            corner["x"] = x_new
            corner["y"] = y_new

        if use_radians:
            self.angle_radians += angle_radians
            self.angle_degrees += angle_radians * 180.0 / pi
        else:
            self.angle_degrees += angle
            self.angle_radians += radians(angle)

        xs = [pt["x"] for pt in corners]
        ys = [pt["y"] for pt in corners]
        self.bounding_box["x"] = min(xs)
        self.bounding_box["y"] = min(ys)
        self.bounding_box["width"] = max(xs) - min(xs)
        self.bounding_box["height"] = max(ys) - min(ys)

    def shear(
        self,
        shx: float,
        shy: float,
        pivot_x: float = 0.0,
        pivot_y: float = 0.0,
    ) -> None:
        """Applies a shear transformation to the Word about a pivot point.

        Args:
            shx (float): The horizontal shear factor.
            shy (float): The vertical shear factor.
            pivot_x (float, optional): The x-coordinate of the pivot point. Defaults to 0.0.
            pivot_y (float, optional): The y-coordinate of the pivot point. Defaults to 0.0.
        """
        corners = [
            self.top_right,
            self.top_left,
            self.bottom_right,
            self.bottom_left,
        ]
        for corner in corners:
            x_new, y_new = shear_point(
                corner["x"], corner["y"], pivot_x, pivot_y, shx, shy
            )
            corner["x"] = x_new
            corner["y"] = y_new

        xs = [pt["x"] for pt in corners]
        ys = [pt["y"] for pt in corners]
        self.bounding_box["x"] = min(xs)
        self.bounding_box["y"] = min(ys)
        self.bounding_box["width"] = max(xs) - min(xs)
        self.bounding_box["height"] = max(ys) - min(ys)

    def warp_affine(self, a, b, c, d, e, f):
        """Applies an affine transformation to the Word's corners and updates its properties.

        The transformation is defined by:
            x' = a * x + b * y + c
            y' = d * x + e * y + f

        This method updates the corner coordinates, recalculates the axis-aligned
        bounding box, and recalculates the rotation angle based on the transformed corners.

        Args:
            a (float): The coefficient for x in the new x-coordinate.
            b (float): The coefficient for y in the new x-coordinate.
            c (float): The translation term for the new x-coordinate.
            d (float): The coefficient for x in the new y-coordinate.
            e (float): The coefficient for y in the new y-coordinate.
            f (float): The translation term for the new y-coordinate.
        """
        corners = [
            self.top_left,
            self.top_right,
            self.bottom_left,
            self.bottom_right,
        ]

        for corner in corners:
            x_old = corner["x"]
            y_old = corner["y"]
            x_new = a * x_old + b * y_old + c
            y_new = d * x_old + e * y_old + f
            corner["x"] = x_new
            corner["y"] = y_new

        xs = [pt["x"] for pt in corners]
        ys = [pt["y"] for pt in corners]
        self.bounding_box["x"] = min(xs)
        self.bounding_box["y"] = min(ys)
        self.bounding_box["width"] = max(xs) - min(xs)
        self.bounding_box["height"] = max(ys) - min(ys)

        dx = self.top_right["x"] - self.top_left["x"]
        dy = self.top_right["y"] - self.top_left["y"]

        new_angle_radians = atan2(dy, dx)
        self.angle_radians = new_angle_radians
        self.angle_degrees = new_angle_radians * 180.0 / pi

    def warp_affine_normalized_forward(
        self,
        a_f,
        b_f,
        c_f,
        d_f,
        e_f,
        f_f,
        orig_width,
        orig_height,
        new_width,
        new_height,
        flip_y=False,
    ):
        """Applies a normalized forward affine transformation to the Word's corners.

        The transformation converts normalized coordinates from the original image to new
        normalized coordinates in the warped image.

        Args:
            a_f (float): The coefficient for x in the new x-coordinate.
            b_f (float): The coefficient for y in the new x-coordinate.
            c_f (float): The translation term for the new x-coordinate.
            d_f (float): The coefficient for x in the new y-coordinate.
            e_f (float): The coefficient for y in the new y-coordinate.
            f_f (float): The translation term for the new y-coordinate.
            orig_width (int): The width of the original image in pixels.
            orig_height (int): The height of the original image in pixels.
            new_width (int): The width of the new warped image in pixels.
            new_height (int): The height of the new warped image in pixels.
            flip_y (bool, optional): Whether to flip the y-coordinate. Defaults to False.
        """
        corners = [
            self.top_left,
            self.top_right,
            self.bottom_left,
            self.bottom_right,
        ]

        for corner in corners:
            x_o = corner["x"] * orig_width
            y_o = corner["y"] * orig_height

            if flip_y:
                y_o = orig_height - y_o

            x_new_px = a_f * x_o + b_f * y_o + c_f
            y_new_px = d_f * x_o + e_f * y_o + f_f

            if flip_y:
                corner["x"] = x_new_px / new_width
                corner["y"] = 1 - (y_new_px / new_height)
            else:
                corner["x"] = x_new_px / new_width
                corner["y"] = y_new_px / new_height

        xs = [pt["x"] for pt in corners]
        ys = [pt["y"] for pt in corners]
        self.bounding_box["x"] = min(xs)
        self.bounding_box["y"] = min(ys)
        self.bounding_box["width"] = max(xs) - min(xs)
        self.bounding_box["height"] = max(ys) - min(ys)

        dx = self.top_right["x"] - self.top_left["x"]
        dy = self.top_right["y"] - self.top_left["y"]
        angle_rad = atan2(dy, dx)
        self.angle_radians = angle_rad
        self.angle_degrees = degrees(angle_rad)

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
    ):
        # We will assume the corners come in as Vision bottom-left coords
        # and we want them to end as Vision bottom-left coords in the original
        # image.
        """
        Maps Vision (bottom-left) normalized coords in the 'warped' image
        back to Vision (bottom-left) normalized coords in the 'original' image.
        """

        corners = [
            self.top_left,
            self.top_right,
            self.bottom_left,
            self.bottom_right,
        ]
        corner_names = ["top_left", "top_right", "bottom_left", "bottom_right"]

        for corner, name in zip(corners, corner_names):
            # 1) Flip Y from bottom-left to top-left
            # Because the perspective transform code uses top-left orientation
            x_vision_warped = corner["x"]  # 0..1
            y_vision_warped = corner["y"]  # 0..1, bottom=0
            y_top_left_warped = 1.0 - y_vision_warped

            # 2) Scale to pixel coordinates in the *warped* image
            x_warped_px = x_vision_warped * dst_width
            y_warped_px = y_top_left_warped * dst_height

            # 3) Apply the *inverse* perspective (already inverted) to get
            # original top-left px
            denom = (g * x_warped_px) + (h * y_warped_px) + 1.0
            if abs(denom) < 1e-12:
                raise ValueError(
                    "Inverse warp denominator ~ 0 at corner: " + name
                )

            X_old_px = (a * x_warped_px + b * y_warped_px + c) / denom
            Y_old_px = (d * x_warped_px + e * y_warped_px + f) / denom

            # 4) Convert to normalized coordinates in top-left of the
            # *original* image
            X_old_norm_tl = X_old_px / src_width
            Y_old_norm_tl = Y_old_px / src_height

            # 5) Flip Y back to bottom-left for Vision
            X_old_vision = X_old_norm_tl
            Y_old_vision = 1.0 - Y_old_norm_tl

            # Update the corner
            corner["x"] = X_old_vision
            corner["y"] = Y_old_vision

        xs = [pt["x"] for pt in corners]
        ys = [pt["y"] for pt in corners]
        self.bounding_box["x"] = min(xs)
        self.bounding_box["y"] = min(ys)
        self.bounding_box["width"] = max(xs) - min(xs)
        self.bounding_box["height"] = max(ys) - min(ys)
        dx = self.top_right["x"] - self.top_left["x"]
        dy = self.top_right["y"] - self.top_left["y"]
        angle_rad = atan2(dy, dx)
        self.angle_radians = angle_rad
        self.angle_degrees = degrees(angle_rad)

    def rotate_90_ccw_in_place(self, old_w: int, old_h: int):
        """Rotates the Word 90 degrees counter-clockwise in-place.

        The rotation is performed about the origin (0, 0) in pixel space, and the
        coordinates are re-normalized based on the new image dimensions.

        Args:
            old_w (int): The width of the image before rotation.
            old_h (int): The height of the image before rotation.
        """
        corners = [
            self.top_left,
            self.top_right,
            self.bottom_right,
            self.bottom_left,
        ]
        for corner in corners:
            corner["x"] *= old_w
            corner["y"] *= old_h

        for corner in corners:
            x_old = corner["x"]
            y_old = corner["y"]
            x_new = y_old
            y_new = old_w - x_old
            corner["x"] = x_new
            corner["y"] = y_new

        final_w = old_h
        final_h = old_w
        for corner in corners:
            corner["x"] /= final_w
            corner["y"] /= final_h

        xs = [pt["x"] for pt in corners]
        ys = [pt["y"] for pt in corners]
        self.bounding_box["x"] = min(xs)
        self.bounding_box["y"] = min(ys)
        self.bounding_box["width"] = max(xs) - min(xs)
        self.bounding_box["height"] = max(ys) - min(ys)

        self.angle_degrees += 90
        self.angle_radians += pi / 2

    def is_point_in_bounding_box(self, x: float, y: float) -> bool:
        """Determines if a point (x,y) is inside the bounding box of the line.

        Args:
            x (float): The x-coordinate of the point.
            y (float): The y-coordinate of the point.

        Returns:
            bool: True if the point is inside the bounding box, False otherwise.
        """
        return bool(
            self.bounding_box["x"]
            <= x
            <= self.bounding_box["x"] + self.bounding_box["width"]
            and self.bounding_box["y"]
            <= y
            <= self.bounding_box["y"] + self.bounding_box["height"]
        )

    def __repr__(self):
        """Returns a string representation of the Word object.

        Returns:
            str: The string representation of the Word object.
        """
        return (
            f"Word("
            f"word_id={self.word_id}, "
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
        """Returns the hash value of the Word object."""
        return hash(
            (
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
            )
        )


def item_to_word(item: Dict[str, Any]) -> Word:
    """Converts a DynamoDB item to a Word object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        Word: The Word object represented by the DynamoDB item.

    Raises:
        ValueError: When the item is missing required keys or has malformed
        fields.
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
        return Word(
            image_id=item["PK"]["S"][6:],
            line_id=int(item["SK"]["S"].split("#")[1]),
            word_id=int(item["SK"]["S"].split("#")[3]),
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
        )
    except (KeyError, ValueError) as e:
        raise ValueError(f"Error converting item to Word: {e}")
