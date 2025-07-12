"""Base geometry mixin for OCR entity classes.

This module provides a mixin class that implements common geometry operations
for OCR entities (Line, Word, Letter and their Receipt counterparts).
"""

from math import atan2, cos, degrees, pi, radians, sin
from typing import Any, Dict, Protocol, Tuple

from receipt_dynamo.entities.util import shear_point


class GeometryProtocol(Protocol):
    """Protocol defining required attributes for GeometryMixin."""

    top_left: Dict[str, Any]
    top_right: Dict[str, Any]
    bottom_left: Dict[str, Any]
    bottom_right: Dict[str, Any]
    bounding_box: Dict[str, Any]
    angle_degrees: float
    angle_radians: float


class GeometryMixin:
    """Mixin providing common geometry operations for OCR entities.

    This mixin assumes the class has the following attributes:
    - top_left, top_right, bottom_left, bottom_right: dict with 'x', 'y' keys
    - bounding_box: dict with 'x', 'y', 'width', 'height' keys
    - angle_degrees: float
    - angle_radians: float
    """

    def calculate_centroid(self) -> Tuple[float, float]:
        """Calculates the centroid of the entity.

        Returns:
            Tuple[float, float]: The (x, y) coordinates of the centroid.
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

    def is_point_in_bounding_box(self, x: float, y: float) -> bool:
        """Determines if a point (x,y) is inside the bounding box.

        Args:
            x (float): The x-coordinate of the point.
            y (float): The y-coordinate of the point.

        Returns:
            bool: True if the point is inside the bounding box, False
                otherwise.
        """
        return bool(
            self.bounding_box["x"]
            <= x
            <= self.bounding_box["x"] + self.bounding_box["width"]
            and self.bounding_box["y"]
            <= y
            <= self.bounding_box["y"] + self.bounding_box["height"]
        )

    def translate(self, x: float, y: float) -> None:
        """Translates the entity by the specified x and y offsets.

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
        """Scales the entity by the specified factors along the x and y axes.

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
        """Rotates the entity by the specified angle about a given origin.

        Only rotates if the angle is within the allowed range:
        - [-π/2, π/2] if `use_radians` is True
        - [-90, 90] degrees if `use_radians` is False

        The method updates the corner coordinates and accumulates the rotation
        in the angle properties.

        Args:
            angle (float): The angle by which to rotate the entity.
            rotate_origin_x (float): The x-coordinate of the rotation origin.
            rotate_origin_y (float): The y-coordinate of the rotation origin.
            use_radians (bool, optional): Whether the angle is in radians.
                Defaults to True.

        Raises:
            ValueError: If the angle is outside the allowed range.
        """
        if use_radians:
            if not -pi / 2 <= angle <= pi / 2:
                raise ValueError(
                    f"Angle {angle} (radians) is outside the allowed "
                    "range [-π/2, π/2]."
                )
            angle_radians = angle
        else:
            if not -90 <= angle <= 90:
                raise ValueError(
                    f"Angle {angle} (degrees) is outside the allowed "
                    "range [-90, 90]."
                )
            angle_radians = radians(angle)

        def rotate_point(px, py, ox, oy, theta):
            """Rotates a point (px, py) around (ox, oy) by theta radians."""
            translated_x = px - ox
            translated_y = py - oy
            rotated_x = translated_x * cos(theta) - translated_y * sin(theta)
            rotated_y = translated_x * sin(theta) + translated_y * cos(theta)
            return rotated_x + ox, rotated_y + oy

        for corner in [
            self.top_right,
            self.top_left,
            self.bottom_right,
            self.bottom_left,
        ]:
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

        xs = [
            pt["x"]
            for pt in [
                self.top_right,
                self.top_left,
                self.bottom_right,
                self.bottom_left,
            ]
        ]
        ys = [
            pt["y"]
            for pt in [
                self.top_right,
                self.top_left,
                self.bottom_right,
                self.bottom_left,
            ]
        ]
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
        """Applies a shear transformation to the entity about a pivot point.

        Args:
            shx (float): The horizontal shear factor.
            shy (float): The vertical shear factor.
            pivot_x (float, optional): The x-coordinate of the pivot point.
                Defaults to 0.0.
            pivot_y (float, optional): The y-coordinate of the pivot point.
                Defaults to 0.0.
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

    # pylint: disable=too-many-arguments
    def warp_affine(self, a, b, c, d, e, f) -> None:
        """Applies an affine transformation to the entity's corners and updates
        its properties.

        The transformation is defined by the equations:
            x' = a * x + b * y + c
            y' = d * x + e * y + f

        After transforming the corners, the bounding box and the entity's angle
        are recalculated.

        Args:
            a, b, c, d, e, f (float): Parameters defining the 2x3 affine
                transformation.
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
            corner["x"] = a * x_old + b * y_old + c
            corner["y"] = d * x_old + e * y_old + f

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

    # pylint: disable=too-many-arguments,too-many-locals
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
    ) -> None:
        """Applies a normalized forward affine transformation to the entity's
        corners.

        The transformation converts normalized coordinates from the original
        image to new normalized coordinates in the warped image.

        Args:
            a_f, b_f, c_f, d_f, e_f, f_f (float): Parameters for the forward
                affine transform.
            orig_width (int): The width of the original image in pixels.
            orig_height (int): The height of the original image in pixels.
            new_width (int): The width of the new warped image in pixels.
            new_height (int): The height of the new warped image in pixels.
            flip_y (bool, optional): Whether to flip the y-coordinate. Defaults
                to False.
        """
        corners = [
            self.top_left,
            self.top_right,
            self.bottom_left,
            self.bottom_right,
        ]
        for corner in corners:
            x_old = corner["x"] * orig_width
            y_old = corner["y"] * orig_height
            if flip_y:
                y_old = orig_height - y_old
            x_new_px = a_f * x_old + b_f * y_old + c_f
            y_new_px = d_f * x_old + e_f * y_old + f_f
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

    # pylint: disable=too-many-arguments,too-many-locals
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
        Maps Vision (bottom-left) normalized coords in the 'warped' image
        back to Vision (bottom-left) normalized coords in the 'original' image.

        Args:
            a, b, c, d, e, f, g, h (float): The perspective coefficients
                that mapped the original image -> new image. We will invert
                them here so we can map new coords -> old coords.
            src_width (int): The original (old) image width in pixels.
            src_height (int): The original (old) image height in pixels.
            dst_width (int): The new (warped) image width in pixels.
            dst_height (int): The new (warped) image height in pixels.
            flip_y (bool): If True, we treat the coordinate system as
                flipped in Y. Defaults to False.
        """
        corners = [
            self.top_left,
            self.top_right,
            self.bottom_left,
            self.bottom_right,
        ]
        corner_names = ["top_left", "top_right", "bottom_left", "bottom_right"]

        if flip_y:
            # Receipt classes use different coordinate system
            for corner in corners:
                # 1) Convert normalized new coords -> pixel coords in the 'new'
                # (warped) image
                x_new_px = corner["x"] * dst_width
                y_new_px = corner["y"] * dst_height

                # If the new system's Y=0 was at the top, then from the
                # perspective of a typical "bottom=0" system, we flip:
                y_new_px = dst_height - y_new_px

                # 2) Solve the perspective equations for old pixel coords
                # (X_old, Y_old). We have the system:
                #   x_new_px = (a*X_old + b*Y_old + c) /
                #              (1 + g*X_old + h*Y_old)
                #   y_new_px = (d*X_old + e*Y_old + f) /
                #              (1 + g*X_old + h*Y_old)
                #
                # Put it in the form:
                #    (g*x_new_px - a)*X_old + (h*x_new_px - b)*Y_old =
                #    c - x_new_px
                #    (g*y_new_px - d)*X_old + (h*y_new_px - e)*Y_old =
                #    f - y_new_px

                a11 = g * x_new_px - a
                a12 = h * x_new_px - b
                b1 = c - x_new_px

                a21 = g * y_new_px - d
                a22 = h * y_new_px - e
                b2 = f - y_new_px

                # Solve the 2×2 linear system via determinant
                det = a11 * a22 - a12 * a21
                if abs(det) < 1e-12:
                    # Degenerate or singular. You can raise an exception
                    # or skip. For robust code, handle it gracefully:
                    raise ValueError(
                        "Inverse perspective transform is singular for "
                        "this corner."
                    )

                x_old_px = (b1 * a22 - b2 * a12) / det
                y_old_px = (a11 * b2 - a21 * b1) / det

                # 3) Convert old pixel coords -> old normalized coords
                # in [0..1]
                corner["x"] = x_old_px / src_width
                corner["y"] = y_old_px / src_height

                # If the old/original system also had Y=0 at top, do the final
                # flip:
                corner["y"] = 1.0 - corner["y"]
        else:
            # Vision coordinate system (non-receipt classes)
            for corner, name in zip(corners, corner_names):
                # 1) Flip Y from bottom-left to top-left
                # Because the perspective transform code uses top-left
                # orientation
                x_vision_warped = corner["x"]  # 0..1
                y_vision_warped = corner["y"]  # 0..1, bottom=0
                y_top_left_warped = 1.0 - y_vision_warped

                # 2) Scale to pixel coordinates in the *warped* image
                x_warped_px = x_vision_warped * dst_width
                y_warped_px = y_top_left_warped * dst_height

                # 3) Apply the *inverse* perspective (already inverted) to
                # get original top-left px
                denom = (g * x_warped_px) + (h * y_warped_px) + 1.0
                if abs(denom) < 1e-12:
                    raise ValueError(
                        "Inverse warp denominator ~ 0 at corner: " + name
                    )

                x_old_px = (a * x_warped_px + b * y_warped_px + c) / denom
                y_old_px = (d * x_warped_px + e * y_warped_px + f) / denom

                # 4) Convert to normalized coordinates in top-left of the
                # *original* image
                x_old_norm_tl = x_old_px / src_width
                y_old_norm_tl = y_old_px / src_height

                # 5) Flip Y back to bottom-left for Vision
                x_old_vision = x_old_norm_tl
                y_old_vision = 1.0 - y_old_norm_tl

                # Update the corner
                corner["x"] = x_old_vision
                corner["y"] = y_old_vision

        # 4) Recompute bounding box + angle
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

    def rotate_90_ccw_in_place(self, old_w: int, old_h: int) -> None:
        """Rotates the entity 90 degrees counter-clockwise in-place.

        The rotation is performed about the origin (0, 0) in pixel space, and
        the coordinates are re-normalized based on the new image dimensions.

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
            corner["x"] = y_old
            corner["y"] = old_w - x_old

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
