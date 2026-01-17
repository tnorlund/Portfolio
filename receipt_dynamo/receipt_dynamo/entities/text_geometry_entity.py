"""
Flattened text geometry entity base class.

This module consolidates the 7 geometry mixins into a single base class,
following the same pattern as FlattenedStandardMixin for data operations.

Consolidated mixins:
    - GeometryMixin (transformations: translate, scale, rotate, etc.)
    - GeometryHashMixin (__hash__ implementation)
    - GeometryReprMixin (__repr__ implementation)
    - GeometrySerializationMixin (DynamoDB serialization)
    - GeometryValidationMixin (field validation)
    - GeometryValidationUtilsMixin (UUID/text validation)
    - WarpTransformMixin (perspective transforms)

Benefits:
    - Reduces inheritance depth from 8+ levels to 2-3
    - Avoids pylint's max-ancestors=7 warning
    - All geometry operations in one place for easier understanding
    - Maintains full backwards compatibility

Usage:
    @dataclass(kw_only=True)
    class MyGeometryEntity(TextGeometryEntity):
        # Add entity-specific fields
        my_field: str

        def __post_init__(self):
            self._validate_geometry()  # Call parent validation
            # Add entity-specific validation
"""

from dataclasses import dataclass
from math import atan2, cos, degrees, pi, radians, sin
from typing import Any, ClassVar, Dict, Optional, Set, Tuple

from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.util import (
    _format_float,
    _repr_str,
    assert_valid_bounding_box,
    assert_valid_point,
    assert_valid_uuid,
    serialize_bounding_box,
    serialize_confidence,
    serialize_coordinate_point,
    shear_point,
)


@dataclass(kw_only=True)
class TextGeometryEntity(DynamoDBEntity):
    """
    Flattened base class for entities with geometric properties.

    This class consolidates all geometry-related functionality that was
    previously spread across 7 separate mixins. Subclasses should define
    their specific ID fields and call _validate_geometry() in __post_init__.

    Geometry Fields (all required):
        image_id: UUID identifying the source image
        text: Text content of the entity
        bounding_box: Axis-aligned bounding box dict
        top_left, top_right, bottom_left, bottom_right: Corner point dicts
        angle_degrees: Rotation angle in degrees
        angle_radians: Rotation angle in radians
        confidence: Detection confidence (0-1)

    Methods are organized into sections:
        - Validation
        - Serialization (DynamoDB)
        - Hash computation
        - Repr formatting
        - Geometry operations (translate, scale, rotate, etc.)
        - Perspective transforms

    Class Variables:
        BASE_REQUIRED_KEYS: Required DynamoDB item keys for geometry entities
    """

    # Required keys shared by all geometry entities (subclasses add their own)
    BASE_REQUIRED_KEYS: ClassVar[Set[str]] = {
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

    # Core geometry fields - subclasses inherit these
    image_id: str
    text: str
    bounding_box: Dict[str, float]
    top_left: Dict[str, float]
    top_right: Dict[str, float]
    bottom_left: Dict[str, float]
    bottom_right: Dict[str, float]
    angle_degrees: float
    angle_radians: float
    confidence: float

    # =========================================================================
    # VALIDATION
    # From: GeometryValidationMixin + GeometryValidationUtilsMixin
    # =========================================================================

    def _validate_geometry(self) -> None:
        """
        Validate all geometry fields.

        Call this from subclass __post_init__ after validating ID fields.
        This method validates:
            1. image_id as valid UUIDv4
            2. text as string
            3. bounding_box structure and values
            4. All four corner points
            5. Angles (normalizes int to float)
            6. Confidence range (0 < confidence <= 1)

        Raises:
            ValueError: If any field has invalid type or value
            AssertionError: If bounding box or points have invalid structure
        """
        # Validate image_id
        assert_valid_uuid(self.image_id)

        # Validate text
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")

        # Validate bounding box and corners
        assert_valid_bounding_box(self.bounding_box)
        assert_valid_point(self.top_left)
        assert_valid_point(self.top_right)
        assert_valid_point(self.bottom_left)
        assert_valid_point(self.bottom_right)

        # Normalize and validate angles
        if not isinstance(self.angle_degrees, (float, int)):
            raise ValueError(
                f"angle_degrees must be float or int, "
                f"got {type(self.angle_degrees).__name__}"
            )
        self.angle_degrees = float(self.angle_degrees)

        if not isinstance(self.angle_radians, (float, int)):
            raise ValueError(
                f"angle_radians must be float or int, "
                f"got {type(self.angle_radians).__name__}"
            )
        self.angle_radians = float(self.angle_radians)

        # Validate confidence
        if isinstance(self.confidence, int):
            self.confidence = float(self.confidence)
        if not isinstance(self.confidence, float):
            raise ValueError(
                f"confidence must be float or int, "
                f"got {type(self.confidence).__name__}"
            )
        if not 0.0 < self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be between 0 and 1, got {self.confidence}"
            )

    # =========================================================================
    # SERIALIZATION
    # From: GeometrySerializationMixin + SerializationMixin
    # =========================================================================

    def _get_geometry_fields(self) -> Dict[str, Any]:
        """
        Return geometry fields serialized for DynamoDB.

        This provides the standard serialization for all geometry fields
        that can be merged into the to_item() result.

        Returns:
            Dict with DynamoDB-formatted geometry fields
        """
        return {
            "text": {"S": self.text},
            "bounding_box": serialize_bounding_box(self.bounding_box),
            "top_right": serialize_coordinate_point(self.top_right),
            "top_left": serialize_coordinate_point(self.top_left),
            "bottom_right": serialize_coordinate_point(self.bottom_right),
            "bottom_left": serialize_coordinate_point(self.bottom_left),
            "angle_degrees": {"N": _format_float(self.angle_degrees, 18, 20)},
            "angle_radians": {"N": _format_float(self.angle_radians, 18, 20)},
            "confidence": serialize_confidence(self.confidence),
        }

    # =========================================================================
    # HASH
    # From: GeometryHashMixin
    # =========================================================================

    def _get_base_geometry_hash_fields(self) -> Tuple[Any, ...]:
        """
        Return core geometry fields for hash computation.

        Subclasses should call this and extend with entity-specific fields.

        Returns:
            Tuple of hashable geometry field values
        """
        return (
            self.text,
            tuple(sorted(self.bounding_box.items())),
            tuple(sorted(self.top_right.items())),
            tuple(sorted(self.top_left.items())),
            tuple(sorted(self.bottom_right.items())),
            tuple(sorted(self.bottom_left.items())),
            self.angle_degrees,
            self.angle_radians,
            self.confidence,
        )

    def _get_geometry_hash_fields(self) -> Tuple[Any, ...]:
        """
        Return fields to include in hash computation.

        Override in subclasses to add entity-specific fields:

            def _get_geometry_hash_fields(self) -> Tuple[Any, ...]:
                return self._get_base_geometry_hash_fields() + (
                    self.receipt_id,
                    self.line_id,
                    self.word_id,
                )

        Returns:
            Tuple of all fields to hash
        """
        return self._get_base_geometry_hash_fields()

    def __hash__(self) -> int:
        """Return hash based on geometry and entity-specific fields."""
        return hash(self._get_geometry_hash_fields())

    # =========================================================================
    # REPR
    # From: GeometryReprMixin
    # =========================================================================

    def _get_geometry_repr_fields(self) -> str:
        """
        Return formatted geometry fields for __repr__.

        Subclasses can use this in their __repr__ implementation:

            def __repr__(self) -> str:
                return (
                    f"MyEntity("
                    f"my_id={self.my_id}, "
                    f"{self._get_geometry_repr_fields()}"
                    f")"
                )

        Returns:
            Formatted string of geometry fields
        """
        return (
            f"text={_repr_str(self.text)}, "
            f"bounding_box={self.bounding_box}, "
            f"top_right={self.top_right}, "
            f"top_left={self.top_left}, "
            f"bottom_right={self.bottom_right}, "
            f"bottom_left={self.bottom_left}, "
            f"angle_degrees={self.angle_degrees}, "
            f"angle_radians={self.angle_radians}, "
            f"confidence={self.confidence}"
        )

    def _iter_geometry_fields(self) -> Tuple[Tuple[str, Any], ...]:
        """
        Return geometry fields as tuple of (name, value) pairs.

        Subclasses can use this in their __iter__ implementation:

            def __iter__(self):
                yield "my_id", self.my_id
                yield from self._iter_geometry_fields()

        Returns:
            Tuple of (field_name, field_value) pairs for all geometry fields
        """
        return (
            ("text", self.text),
            ("bounding_box", self.bounding_box),
            ("top_right", self.top_right),
            ("top_left", self.top_left),
            ("bottom_right", self.bottom_right),
            ("bottom_left", self.bottom_left),
            ("angle_degrees", self.angle_degrees),
            ("angle_radians", self.angle_radians),
            ("confidence", self.confidence),
        )

    # =========================================================================
    # GEOMETRY OPERATIONS
    # From: GeometryMixin
    # =========================================================================

    def calculate_centroid(self) -> Tuple[float, float]:
        """
        Calculate the center point of the quadrilateral.

        Returns:
            Tuple (x, y) of centroid coordinates
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
        """
        Convert normalized coordinates (0-1) to pixel coordinates.

        Args:
            width: Image width for scaling (optional)
            height: Image height for scaling (optional)
            flip_y: If True, flip Y coordinates (for image coord systems)

        Returns:
            Tuple of four corners: (top_left, top_right, bottom_left,
            bottom_right)
            Each corner is (x, y) tuple

        Raises:
            ValueError: If only one of width/height is provided, or if
                flip_y=True without providing height
        """
        if (width is None) != (height is None):
            raise ValueError("Both width and height must be provided together")

        if flip_y and height is None:
            raise ValueError("height is required when flip_y=True")

        x_scale = float(width) if width else 1.0
        y_scale = float(height) if height else 1.0

        def scale_point(pt: Dict[str, float]) -> Tuple[float, float]:
            x = pt["x"] * x_scale
            y = pt["y"] * y_scale
            if flip_y:
                y = height - y  # type: ignore[operator]
            return (x, y)

        return (
            scale_point(self.top_left),
            scale_point(self.top_right),
            scale_point(self.bottom_left),
            scale_point(self.bottom_right),
        )

    def is_point_in_bounding_box(self, x: float, y: float) -> bool:
        """
        Check if point (x, y) is inside the bounding box.

        Args:
            x: X coordinate to test
            y: Y coordinate to test

        Returns:
            True if point is inside bounding box
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
        """
        Translate the entity by (x, y) offset.

        Modifies all corner points and bounding box in place.

        Args:
            x: X offset to add
            y: Y offset to add
        """
        for corner in [
            self.top_right,
            self.top_left,
            self.bottom_right,
            self.bottom_left,
        ]:
            corner["x"] += x
            corner["y"] += y
        self.bounding_box["x"] += x
        self.bounding_box["y"] += y

    def scale(self, sx: float, sy: float) -> None:
        """
        Scale the entity by factors (sx, sy).

        Modifies all corner points and bounding box in place.

        Args:
            sx: X scale factor
            sy: Y scale factor
        """
        for corner in [
            self.top_right,
            self.top_left,
            self.bottom_right,
            self.bottom_left,
        ]:
            corner["x"] *= sx
            corner["y"] *= sy
        self.bounding_box["x"] *= sx
        self.bounding_box["y"] *= sy
        self.bounding_box["width"] *= sx
        self.bounding_box["height"] *= sy

    def rotate(
        self,
        angle: float,
        origin_x: float,
        origin_y: float,
        use_radians: bool = False,
    ) -> None:
        """
        Rotate the entity around (origin_x, origin_y).

        Modifies corners, bounding box, and angle attributes in place.

        Args:
            angle: Rotation angle (degrees by default)
            origin_x: X coordinate of rotation center
            origin_y: Y coordinate of rotation center
            use_radians: If True, angle is in radians

        Raises:
            ValueError: If angle is outside [-90, 90] degrees
        """
        if use_radians:
            theta = angle
            if theta < -pi / 2 or theta > pi / 2:
                raise ValueError(
                    f"Angle {theta} radians is outside [-pi/2, pi/2]"
                )
        else:
            if angle < -90 or angle > 90:
                raise ValueError(f"Angle {angle} degrees is outside [-90, 90]")
            theta = radians(angle)

        def rotate_point(px: float, py: float) -> Tuple[float, float]:
            tx, ty = px - origin_x, py - origin_y
            rx = tx * cos(theta) - ty * sin(theta)
            ry = tx * sin(theta) + ty * cos(theta)
            return rx + origin_x, ry + origin_y

        for corner in [
            self.top_right,
            self.top_left,
            self.bottom_right,
            self.bottom_left,
        ]:
            corner["x"], corner["y"] = rotate_point(corner["x"], corner["y"])

        self._update_bounding_box_from_corners()
        self.angle_degrees += angle if not use_radians else degrees(angle)
        self.angle_radians += theta
        self._normalize_angles()

    def shear(
        self,
        shx: float,
        shy: float,
        pivot_x: float = 0.0,
        pivot_y: float = 0.0,
    ) -> None:
        """
        Apply shear transformation about a pivot point.

        Args:
            shx: Horizontal shear factor
            shy: Vertical shear factor
            pivot_x: X coordinate of pivot point
            pivot_y: Y coordinate of pivot point
        """
        for corner in [
            self.top_right,
            self.top_left,
            self.bottom_right,
            self.bottom_left,
        ]:
            corner["x"], corner["y"] = shear_point(
                corner["x"], corner["y"], pivot_x, pivot_y, shx, shy
            )
        self._update_bounding_box_from_corners()

    def rotate_90_ccw_in_place(
        self, _old_width: float, _old_height: float
    ) -> None:
        """
        Rotate the entity 90 degrees counter-clockwise in place.

        Special transformation for rotating entire pages with normalized
        coordinates (0-1). The coordinates transform as:
            new_x = old_y
            new_y = 1 - old_x

        Args:
            _old_width: The width of the original image/page (kept for API
                compatibility)
            _old_height: The height of the original image/page (kept for API
                compatibility)
        """

        def rotate_90_ccw(px: float, py: float) -> Tuple[float, float]:
            return py, -(px - 1)

        for corner in [
            self.top_right,
            self.top_left,
            self.bottom_right,
            self.bottom_left,
        ]:
            corner["x"], corner["y"] = rotate_90_ccw(corner["x"], corner["y"])

        self._update_bounding_box_from_corners()
        self.angle_degrees += 90
        self.angle_radians += pi / 2

    def warp_affine_normalized_forward(
        self,
        _a: float,
        _b: float,
        c: float,
        _d: float,
        _e: float,
        f: float,
        src_width: float,
        src_height: float,
        dst_width: float,
        dst_height: float,
        _flip_y: bool = False,
    ) -> None:
        """
        Apply a normalized forward affine transformation to the entity.

        This method applies an affine transformation where the c and f
        parameters are normalized offsets that get scaled based on the
        bounding box dimensions and the source/destination dimensions.

        The actual offset applied is:
        - x_offset = c * (bounding_box.width / (src_width * dst_width))
        - y_offset = f * (bounding_box.height / (src_height * dst_height))

        Args:
            _a, _b, c, _d, _e, f: The affine transformation coefficients.
                Only c and f are used; others kept for API compatibility.
            src_width: Source image width
            src_height: Source image height
            dst_width: Destination image width
            dst_height: Destination image height
            _flip_y: Whether to flip Y coordinates (not used in current
                implementation, kept for API compatibility)
        """
        # Calculate the scaled offsets based on bounding box and image
        # dimensions
        x_offset = c * (self.bounding_box["width"] / (src_width * dst_width))
        y_offset = f * (
            self.bounding_box["height"] / (src_height * dst_height)
        )

        # Apply the transformation to all corners
        self.top_left["x"] += x_offset
        self.top_left["y"] += y_offset
        self.top_right["x"] += x_offset
        self.top_right["y"] += y_offset
        self.bottom_left["x"] += x_offset
        self.bottom_left["y"] += y_offset
        self.bottom_right["x"] += x_offset
        self.bottom_right["y"] += y_offset

        # Update bounding box
        self.bounding_box["x"] += x_offset
        self.bounding_box["y"] += y_offset

    def warp_affine(
        self,
        a: float,
        b: float,
        c: float,
        d: float,
        e: float,
        f: float,
    ) -> None:
        """
        Apply an affine transformation to the entity.

        The transformation matrix is:
            [x']   [a  b  c] [x]
            [y'] = [d  e  f] [y]
            [1 ]   [0  0  1] [1]

        Args:
            a, b, c, d, e, f: Affine transformation coefficients
        """

        def transform_point(px: float, py: float) -> Tuple[float, float]:
            nx = a * px + b * py + c
            ny = d * px + e * py + f
            return nx, ny

        for corner in [
            self.top_right,
            self.top_left,
            self.bottom_right,
            self.bottom_left,
        ]:
            corner["x"], corner["y"] = transform_point(
                corner["x"], corner["y"]
            )

        self._update_bounding_box_from_corners()

        # Update angle based on transformation of unit vector
        dx, dy = transform_point(1, 0)
        origin_x, origin_y = transform_point(0, 0)
        new_angle = atan2(dy - origin_y, dx - origin_x)
        self.angle_radians = new_angle
        self.angle_degrees = degrees(new_angle)

    def _update_bounding_box_from_corners(self) -> None:
        """Recalculate bounding box from corner points."""
        xs = [
            self.top_right["x"],
            self.top_left["x"],
            self.bottom_right["x"],
            self.bottom_left["x"],
        ]
        ys = [
            self.top_right["y"],
            self.top_left["y"],
            self.bottom_right["y"],
            self.bottom_left["y"],
        ]
        self.bounding_box["x"] = min(xs)
        self.bounding_box["y"] = min(ys)
        self.bounding_box["width"] = max(xs) - min(xs)
        self.bounding_box["height"] = max(ys) - min(ys)

    def _normalize_angles(self) -> None:
        """Normalize angles to standard ranges."""
        while self.angle_degrees > 180:
            self.angle_degrees -= 360
        while self.angle_degrees < -180:
            self.angle_degrees += 360
        while self.angle_radians > pi:
            self.angle_radians -= 2 * pi
        while self.angle_radians < -pi:
            self.angle_radians += 2 * pi

    # =========================================================================
    # PERSPECTIVE TRANSFORMS
    # From: WarpTransformMixin
    # =========================================================================

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
    ) -> None:
        """
        Apply inverse perspective transform from warped to original space.

        This is the receipt-specific wrapper for inverse_perspective_transform.

        Args:
            a, b, c, d, e, f, g, h: Perspective transform coefficients
            src_width: Original image width in pixels
            src_height: Original image height in pixels
            dst_width: Warped image width in pixels
            dst_height: Warped image height in pixels
            flip_y: If True, flip Y coordinate system
        """
        self.inverse_perspective_transform(
            a,
            b,
            c,
            d,
            e,
            f,
            g,
            h,
            src_width,
            src_height,
            dst_width,
            dst_height,
            flip_y,
        )

    def inverse_perspective_transform(
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
    ) -> None:
        """
        Apply inverse perspective transform using 2x2 linear system.

        Maps coordinates from warped space back to original space.

        The perspective equations are:
            x_new = (a*X + b*Y + c) / (1 + g*X + h*Y)
            y_new = (d*X + e*Y + f) / (1 + g*X + h*Y)

        This method solves for (X, Y) given (x_new, y_new).

        Args:
            a, b, c, d, e, f, g, h: Perspective coefficients from
                original transform
            src_width: Original image width in pixels
            src_height: Original image height in pixels
            dst_width: Warped image width in pixels
            dst_height: Warped image height in pixels
            flip_y: If True, treat Y=0 as top of image

        Raises:
            ValueError: If transform is singular for any corner, or if
                any dimension parameter is not a positive integer
        """
        # Validate dimension parameters to prevent division by zero
        if not isinstance(src_width, int) or src_width <= 0:
            raise ValueError("src_width must be a positive integer")
        if not isinstance(src_height, int) or src_height <= 0:
            raise ValueError("src_height must be a positive integer")
        if not isinstance(dst_width, int) or dst_width <= 0:
            raise ValueError("dst_width must be a positive integer")
        if not isinstance(dst_height, int) or dst_height <= 0:
            raise ValueError("dst_height must be a positive integer")

        corners = [
            self.top_left,
            self.top_right,
            self.bottom_left,
            self.bottom_right,
        ]

        for corner in corners:
            # Convert normalized coords to pixel coords in warped image
            x_new_px = corner["x"] * dst_width
            y_new_px = corner["y"] * dst_height

            if flip_y:
                y_new_px = dst_height - y_new_px

            # Set up 2x2 linear system
            a11 = g * x_new_px - a
            a12 = h * x_new_px - b
            b1 = c - x_new_px

            a21 = g * y_new_px - d
            a22 = h * y_new_px - e
            b2 = f - y_new_px

            # Solve via determinant
            det = a11 * a22 - a12 * a21
            if abs(det) < 1e-12:
                raise ValueError(
                    "Inverse perspective transform is singular for this corner"
                )

            x_old_px = (b1 * a22 - b2 * a12) / det
            y_old_px = (a11 * b2 - a21 * b1) / det

            # Convert back to normalized coords
            corner["x"] = x_old_px / src_width
            corner["y"] = y_old_px / src_height

            if flip_y:
                corner["y"] = 1.0 - corner["y"]

        # Update bounding box and angle
        self._update_bounding_box_from_corners()
        dx = self.top_right["x"] - self.top_left["x"]
        dy = self.top_right["y"] - self.top_left["y"]
        self.angle_radians = atan2(dy, dx)
        self.angle_degrees = self.angle_radians * 180.0 / pi


# Backwards compatibility alias
GeometryEntity = TextGeometryEntity


__all__ = ["TextGeometryEntity", "GeometryEntity"]
