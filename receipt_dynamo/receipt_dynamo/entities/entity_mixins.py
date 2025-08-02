"""
Consolidated entity mixins for the receipt_dynamo package.

This module combines all entity mixins to reduce file sprawl and make the
codebase easier to navigate. It includes:
- DynamoDB serialization/deserialization
- Common validation patterns
- CDN field handling
- Geometry operations and validation
"""

from datetime import datetime
from decimal import Decimal
from math import atan2, pi
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Protocol,
    Set,
    Tuple,
    TypeVar,
    Union,
)

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

T = TypeVar("T")


# Protocol classes define the expected interface for mixins
class HasKey(Protocol):
    """Protocol for entities that have a DynamoDB key property."""

    @property
    def key(self) -> Dict[str, Any]: ...


class HasGeometryAttributes(Protocol):
    """Protocol for entities with geometry attributes."""

    text: str
    bounding_box: Dict[str, float]
    top_left: Dict[str, float]
    top_right: Dict[str, float]
    bottom_left: Dict[str, float]
    bottom_right: Dict[str, float]
    angle_degrees: float
    angle_radians: float
    confidence: float


# =============================================================================
# DynamoDB Serialization
# =============================================================================


class SerializationMixin:
    """
    Mixin providing standardized DynamoDB serialization/deserialization.

    Reduces code duplication by providing:
    - Generic to_item() implementation based on entity properties
    - Generic from_item() class method for deserialization
    - Common DynamoDB type conversion utilities
    - Standardized error handling for missing/invalid fields
    """

    def build_dynamodb_item(
        self,
        entity_type: str,
        gsi_methods: Optional[List[str]] = None,
        custom_fields: Optional[Dict[str, Any]] = None,
        exclude_fields: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        """
        Build a DynamoDB item from entity attributes.

        Args:
            entity_type: The TYPE field value (e.g., "RECEIPT_WORD")
            gsi_methods: List of method names that return GSI keys
            custom_fields: Custom field mappings that override
                auto-serialization
            exclude_fields: Set of field names to exclude from serialization

        Returns:
            Complete DynamoDB item dictionary
        """
        gsi_methods = gsi_methods or []
        custom_fields = custom_fields or {}
        exclude_fields = exclude_fields or set()

        # Start with primary key and type
        item = {"TYPE": {"S": entity_type}}

        # Add primary key if the entity implements HasKey protocol
        if hasattr(self, "key"):
            # Type assertion to satisfy mypy
            entity_with_key = self  # type: HasKey
            item.update(entity_with_key.key)

        # Add GSI keys
        for gsi_method in gsi_methods:
            if hasattr(self, gsi_method):
                gsi_keys = getattr(self, gsi_method)()
                if gsi_keys:
                    item.update(gsi_keys)

        # Add custom fields first (they take precedence)
        item.update(custom_fields)

        # Auto-serialize remaining entity attributes
        for attr_name in dir(self):
            if (
                attr_name not in exclude_fields
                and not attr_name.startswith("_")
                and not callable(getattr(self, attr_name))
                and attr_name not in item  # Don't override custom fields
                and attr_name not in {"key"}  # Skip property methods
            ):
                value = getattr(self, attr_name)
                item[attr_name] = self._serialize_value(value)

        return item

    def _serialize_value(
        self, value: Any, serialize_decimal: bool = False
    ) -> Dict[str, Any]:
        """
        Convert a Python value to DynamoDB attribute format.

        Args:
            value: The value to serialize
            serialize_decimal: Whether to use Decimal serialization for floats

        Returns:
            DynamoDB attribute dict like {"S": "text"} or {"N": "123"}
        """
        if value is None:
            return {"NULL": True}
        if isinstance(value, str):
            return {"S": value} if value else {"NULL": True}
        if isinstance(value, bool):
            return {"BOOL": value}
        if isinstance(value, int):
            return {"N": str(value)}
        if isinstance(value, float):
            if serialize_decimal:
                return {"N": str(Decimal(str(value)))}
            return {"N": str(value)}
        if isinstance(value, datetime):
            return {"S": value.isoformat()}
        if isinstance(value, list):
            if not value:
                return {"L": []}
            # Check if it's a string set
            if all(isinstance(item, str) for item in value):
                return {"SS": value}
            return {"L": [self._serialize_value(item) for item in value]}
        if isinstance(value, dict):
            if not value:
                return {"M": {}}
            return {
                "M": {k: self._serialize_value(v) for k, v in value.items()}
            }
        if isinstance(value, set):
            if not value:
                return {"L": []}
            # Convert to sorted list for consistency
            if all(isinstance(item, str) for item in value):
                return {"SS": sorted(value)}
            return {
                "L": [self._serialize_value(item) for item in sorted(value)]
            }
        # Fallback to string representation
        return {"S": str(value)}

    def _python_to_dynamo(self, value: Any) -> Dict[str, Any]:
        """
        Convert a Python value to a DynamoDB typed value.

        This is an alias for _serialize_value to maintain compatibility
        with entities using the _python_to_dynamo naming convention.
        """
        return self._serialize_value(value)

    @staticmethod
    def _dynamo_to_python(dynamo_value: Dict[str, Any]) -> Any:
        """
        Convert a DynamoDB typed value to a Python value.

        This is a static method to maintain compatibility with entities
        using the _dynamo_to_python naming convention.
        """
        if "NULL" in dynamo_value:
            return None
        if "S" in dynamo_value:
            return dynamo_value["S"]
        if "N" in dynamo_value:
            # Try to convert to int if possible, otherwise float
            try:
                return int(dynamo_value["N"])
            except ValueError:
                return float(dynamo_value["N"])
        if "BOOL" in dynamo_value:
            return dynamo_value["BOOL"]
        if "M" in dynamo_value:
            return {
                k: SerializationMixin._dynamo_to_python(v)
                for k, v in dynamo_value["M"].items()
            }
        if "L" in dynamo_value:
            return [
                SerializationMixin._dynamo_to_python(item)
                for item in dynamo_value["L"]
            ]
        if "SS" in dynamo_value:
            return dynamo_value["SS"]
        if "NS" in dynamo_value:
            return [float(n) for n in dynamo_value["NS"]]
        if "BS" in dynamo_value:
            return dynamo_value["BS"]
        # Convert any other type to string
        return str(dynamo_value)

    @classmethod
    def safe_deserialize_field(
        cls,
        item: Dict[str, Any],
        field_name: str,
        default: Any = None,
        field_type: Optional[type] = None,
    ) -> Any:
        """
        Safely deserialize a field from a DynamoDB item.

        Args:
            item: DynamoDB item dictionary
            field_name: Name of the field to deserialize
            default: Default value if field is missing or NULL
            field_type: Expected type for validation (optional)

        Returns:
            Deserialized value or default
        """
        if field_name not in item:
            return default

        dynamo_value = item[field_name]
        if dynamo_value.get("NULL"):
            return default

        try:
            value = cls._dynamo_to_python(dynamo_value)

            # Type validation if specified
            if field_type is not None and value is not None:
                if field_type == dict and isinstance(value, dict):
                    return value
                elif field_type == list and isinstance(value, list):
                    return value
                elif not isinstance(value, field_type):
                    raise TypeError(
                        f"Expected {field_type.__name__} for field {field_name}, "
                        f"got {type(value).__name__}"
                    )

            return value
        except Exception as e:
            # Return default on any deserialization error
            return default

    @classmethod
    def validate_required_keys(
        cls, item: Dict[str, Any], required_keys: Set[str]
    ) -> None:
        """
        Validate that a DynamoDB item contains all required keys.

        Args:
            item: The DynamoDB item to validate
            required_keys: Set of required key names

        Raises:
            ValueError: If required keys are missing
        """
        if not required_keys.issubset(item.keys()):
            missing_keys = required_keys - item.keys()
            raise ValueError(f"Item is missing required keys: {missing_keys}")

    @classmethod
    def extract_key_components(
        cls, item: Dict[str, Any], pk_pattern: str, sk_pattern: str
    ) -> Dict[str, Union[str, int]]:
        """
        Extract structured components from PK/SK strings.

        Args:
            item: DynamoDB item containing PK and SK
            pk_pattern: Expected PK pattern (e.g., "IMAGE#")
            sk_pattern: Expected SK pattern (e.g., "RECEIPT#")

        Returns:
            Dictionary of extracted components

        Raises:
            ValueError: If key format is invalid
        """
        try:
            pk_value = item["PK"]["S"]
            sk_value = item["SK"]["S"]

            # Validate and extract PK components
            if not pk_value.startswith(pk_pattern):
                raise ValueError(f"Invalid PK format: {pk_value}")
            pk_parts = pk_value.split("#")

            # Validate and extract SK components
            if not sk_value.startswith(sk_pattern):
                raise ValueError(f"Invalid SK format: {sk_value}")
            sk_parts = sk_value.split("#")

            return {
                "pk_parts": pk_parts,
                "sk_parts": sk_parts,
                "pk_value": pk_value,
                "sk_value": sk_value,
            }
        except (KeyError, IndexError) as e:
            raise ValueError(f"Error parsing key components: {e}") from e


# =============================================================================
# Base Entity Validation
# =============================================================================


class BaseEntityMixin:
    """
    Base mixin providing common entity functionality.

    This mixin provides:
    - Generic field validation based on type hints
    - Standardized __repr__ implementation
    - Common DynamoDB conversion helpers
    """

    def validate_string_fields(self, *field_names: str) -> None:
        """
        Validate that specified fields are strings if not None.

        Args:
            *field_names: Names of fields to validate

        Raises:
            ValueError: If any field is not a string
        """
        for field_name in field_names:
            value = getattr(self, field_name, None)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{field_name} must be a string")

    def validate_int_fields(self, *field_names: str) -> None:
        """
        Validate that specified fields are integers if not None.

        Args:
            *field_names: Names of fields to validate

        Raises:
            ValueError: If any field is not an integer
        """
        for field_name in field_names:
            value = getattr(self, field_name, None)
            if value is not None and not isinstance(value, int):
                raise ValueError(f"{field_name} must be an integer")

    def validate_datetime_fields(self, *field_names: str) -> None:
        """
        Validate that specified fields are datetime objects if not None.

        Args:
            *field_names: Names of fields to validate

        Raises:
            ValueError: If any field is not a datetime
        """
        for field_name in field_names:
            value = getattr(self, field_name, None)
            if value is not None and not isinstance(value, datetime):
                raise ValueError(f"{field_name} must be a datetime object")

    def generate_repr(self, exclude_fields: Optional[Set[str]] = None) -> str:
        """
        Generate a standardized __repr__ string for the entity.

        Args:
            exclude_fields: Set of field names to exclude from repr

        Returns:
            A formatted repr string
        """
        class_name = self.__class__.__name__
        exclude_fields = exclude_fields or set()

        # Get all attributes that don't start with underscore
        fields = []
        for attr_name in sorted(dir(self)):
            if (
                not attr_name.startswith("_")
                and not callable(getattr(self, attr_name))
                and attr_name not in exclude_fields
            ):
                value = getattr(self, attr_name)
                fields.append(f"{attr_name}={_repr_str(value)}")

        return f"{class_name}({', '.join(fields)})"


# =============================================================================
# CDN Fields
# =============================================================================


class CDNFieldsMixin:
    """
    Mixin that provides CDN S3 key validation and serialization.

    Expects the implementing class to have these CDN fields as attributes:
    - cdn_s3_bucket
    - cdn_s3_key
    - cdn_webp_s3_key
    - cdn_avif_s3_key
    - cdn_thumbnail_s3_key
    - cdn_thumbnail_webp_s3_key
    - cdn_thumbnail_avif_s3_key
    - cdn_small_s3_key
    - cdn_small_webp_s3_key
    - cdn_small_avif_s3_key
    - cdn_medium_s3_key (optional)
    - cdn_medium_webp_s3_key (optional)
    - cdn_medium_avif_s3_key (optional)
    """

    # Define CDN field groups
    CDN_BASIC_FIELDS = [
        "cdn_s3_bucket",
        "cdn_s3_key",
        "cdn_webp_s3_key",
        "cdn_avif_s3_key",
    ]

    CDN_SIZE_VARIANTS = ["thumbnail", "small", "medium"]
    CDN_FORMAT_VARIANTS = ["", "webp", "avif"]

    def validate_cdn_fields(self) -> None:
        """
        Validate all CDN S3 key fields are strings if present.

        Raises:
            ValueError: If any CDN field is not a string
        """
        # Validate basic CDN fields
        for field in self.CDN_BASIC_FIELDS:
            value = getattr(self, field, None)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{field} must be a string")

        # Validate size variant CDN fields
        for size in self.CDN_SIZE_VARIANTS:
            for format_variant in self.CDN_FORMAT_VARIANTS:
                if format_variant:
                    field_name = f"cdn_{size}_{format_variant}_s3_key"
                else:
                    field_name = f"cdn_{size}_s3_key"

                # Skip if field doesn't exist (e.g., medium fields might be
                # optional)
                if not hasattr(self, field_name):
                    continue

                value = getattr(self, field_name)
                if value is not None and not isinstance(value, str):
                    raise ValueError(f"{field_name} must be a string")

    def cdn_fields_to_dynamodb_item(self) -> Dict[str, Dict[str, Any]]:
        """
        Convert CDN fields to DynamoDB item format.

        Returns:
            Dictionary with CDN fields in DynamoDB format
        """
        item = {}

        # Add basic CDN fields
        for field in self.CDN_BASIC_FIELDS:
            value = getattr(self, field, None)
            item[field] = {"S": value} if value else {"NULL": True}

        # Add size variant CDN fields
        for size in self.CDN_SIZE_VARIANTS:
            for format_variant in self.CDN_FORMAT_VARIANTS:
                if format_variant:
                    field_name = f"cdn_{size}_{format_variant}_s3_key"
                else:
                    field_name = f"cdn_{size}_s3_key"

                # Skip if field doesn't exist
                if not hasattr(self, field_name):
                    continue

                value = getattr(self, field_name)
                item[field_name] = {"S": value} if value else {"NULL": True}

        return item


# =============================================================================
# Specialized Geometry Mixins
# =============================================================================


class GeometryHashMixin:
    """
    Mixin providing standardized __hash__ implementation for geometry entities.

    This mixin eliminates duplicate __hash__ methods across geometry entities by
    providing a common implementation that hashes geometry fields in a consistent
    order. The implementing class must have geometry attributes defined.

    Expected attributes:
    - text: str
    - bounding_box: Dict[str, float]
    - top_right, top_left, bottom_right, bottom_left: Dict[str, float]
    - angle_degrees: float
    - angle_radians: float
    - confidence: float
    """

    # These attributes will be provided by the class using this mixin
    text: str
    bounding_box: Dict[str, float]
    top_right: Dict[str, float]
    top_left: Dict[str, float]
    bottom_right: Dict[str, float]
    bottom_left: Dict[str, float]
    angle_degrees: float
    angle_radians: float
    confidence: float

    def _get_base_geometry_hash_fields(self) -> Tuple[Any, ...]:
        """
        Returns the common geometry fields that should be included in hash computation.

        This helper method provides a consistent ordering of core geometry fields
        for hashing, eliminating duplication across geometry entities. Entities
        can call this method and add their specific ID fields.

        Returns:
            Tuple of core geometry values to include in hash computation
        """
        return (
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

    def _get_geometry_hash_fields(self) -> Tuple[Any, ...]:
        """
        Returns the geometry fields that should be included in hash computation.

        This method provides a consistent ordering of geometry fields for hashing.
        Subclasses can override this method to include additional fields or
        modify the field order.

        Returns:
            Tuple of values to include in hash computation
        """
        return self._get_base_geometry_hash_fields()


class GeometryReprMixin:
    """
    Mixin providing standardized __repr__ implementation for geometry entities.

    This mixin eliminates duplicate __repr__ methods across geometry entities by
    providing a common implementation that formats geometry fields consistently.
    The implementing class must have geometry attributes defined.

    Expected attributes:
    - text: str
    - bounding_box: Dict[str, float]
    - top_right, top_left, bottom_right, bottom_left: Dict[str, float]
    - angle_degrees: float
    - angle_radians: float
    - confidence: float
    """

    # These attributes will be provided by the class using this mixin
    text: str
    bounding_box: Dict[str, float]
    top_right: Dict[str, float]
    top_left: Dict[str, float]
    bottom_right: Dict[str, float]
    bottom_left: Dict[str, float]
    angle_degrees: float
    angle_radians: float
    confidence: float

    def _get_geometry_repr_fields(self) -> str:
        """
        Returns a formatted string of geometry fields for __repr__.

        This method provides consistent formatting of geometry fields for repr.
        Subclasses can override this method to customize the representation.

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


class WarpTransformMixin:
    """
    Mixin providing warp_transform method for receipt geometry entities.

    This mixin eliminates duplicate warp_transform implementations across
    receipt entities (receipt_line, receipt_word, receipt_letter) by providing
    a common wrapper around the GeometryMixin's inverse_perspective_transform.

    The implementing class must inherit from GeometryMixin to access the
    inverse_perspective_transform method.
    """

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
        Receipt-specific inverse perspective transform from 'new' space back to 'old' space.

        This delegates to GeometryMixin's inverse_perspective_transform method
        which uses the 2x2 linear system approach optimized for receipt
        coordinate systems.

        Args:
            a, b, c, d, e, f, g, h (float): The perspective coefficients that
                mapped the original image -> new image. They are inverted
                here so we can map new coords -> old coords.
            src_width (int): The original (old) image width in pixels.
            src_height (int): The original (old) image height in pixels.
            dst_width (int): The new (warped) image width in pixels.
            dst_height (int): The new (warped) image height in pixels.
            flip_y (bool): If True, we treat the new coordinate system as
                flipped in Y (e.g. some OCR engines treat top=0).
        """
        # Delegate to the existing GeometryMixin implementation
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


class GeometryValidationUtilsMixin:
    """
    Mixin providing common validation utilities for geometry entities.

    This mixin eliminates duplicate validation logic across geometry entities
    by providing common validation methods for image_id (UUID), text (string),
    and geometry fields. It works in conjunction with GeometryValidationMixin.

    Expected attributes:
    - image_id: str (UUID)
    - text: str
    - geometry fields (validated through GeometryValidationMixin)
    """

    # These attributes will be provided by the class using this mixin
    image_id: str
    text: str

    def _validate_image_id(self) -> None:
        """
        Validates that image_id is a valid UUID string.

        Raises:
            ValueError: If image_id is not a valid UUID
        """
        assert_valid_uuid(self.image_id)

    def _validate_text_field(self) -> None:
        """
        Validates that text is a string.

        Raises:
            ValueError: If text is not a string
        """
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")

    def _validate_common_geometry_entity_fields(self) -> None:
        """
        Validates common fields for geometry entities.

        This method validates:
        1. image_id as a valid UUID
        2. text as a string
        3. geometry fields through GeometryValidationMixin

        This method should be called from the entity's __post_init__ method.
        """
        self._validate_image_id()
        self._validate_text_field()

        # Delegate to GeometryValidationMixin for geometry field validation
        if hasattr(self, "_validate_geometry_fields"):
            self._validate_geometry_fields()


# =============================================================================
# Geometry Operations and Validation
# =============================================================================


class GeometryMixin:
    """Mixin providing common geometry operations for OCR entities.

    This mixin assumes the class has the following attributes:
    - top_left, top_right, bottom_left, bottom_right: dict with 'x', 'y' keys
    - bounding_box: dict with 'x', 'y', 'width', 'height' keys
    - angle_degrees: float
    - angle_radians: float
    """

    # These attributes will be provided by the class using this mixin
    top_left: Dict[str, float]
    top_right: Dict[str, float]
    bottom_left: Dict[str, float]
    bottom_right: Dict[str, float]
    bounding_box: Dict[str, float]
    angle_degrees: float
    angle_radians: float

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
        origin_x: float,
        origin_y: float,
        use_radians: bool = False,
    ) -> None:
        """Rotates the entity by the specified angle around the given origin.

        Args:
            angle (float): The rotation angle.
            origin_x (float): The x-coordinate of the rotation origin.
            origin_y (float): The y-coordinate of the rotation origin.
            use_radians (bool): If True, angle is in radians. If False, angle
                is in degrees (default: False).

        Raises:
            ValueError: If the angle is outside the allowed range of -90 to 90
                degrees (or -π/2 to π/2 radians).
        """
        import math

        # Convert angle to radians if needed
        if use_radians:
            theta = angle
            # Check range: -π/2 to π/2
            if theta < -math.pi / 2 or theta > math.pi / 2:
                raise ValueError(
                    f"Angle {theta} radians is outside the allowed range "
                    f"[-π/2, π/2]"
                )
        else:
            # Check range: -90 to 90 degrees
            if angle < -90 or angle > 90:
                raise ValueError(
                    f"Angle {angle} degrees is outside the allowed range "
                    f"[-90, 90]"
                )
            theta = math.radians(angle)

        # Rotate all corner points
        def rotate_point(
            px: float, py: float, ox: float, oy: float, theta: float
        ) -> Tuple[float, float]:
            """Rotate a point around an origin by theta radians."""
            tx, ty = px - ox, py - oy
            rx = tx * math.cos(theta) - ty * math.sin(theta)
            ry = tx * math.sin(theta) + ty * math.cos(theta)
            return rx + ox, ry + oy

        # Rotate corners
        self.top_right["x"], self.top_right["y"] = rotate_point(
            self.top_right["x"], self.top_right["y"], origin_x, origin_y, theta
        )
        self.top_left["x"], self.top_left["y"] = rotate_point(
            self.top_left["x"], self.top_left["y"], origin_x, origin_y, theta
        )
        self.bottom_right["x"], self.bottom_right["y"] = rotate_point(
            self.bottom_right["x"],
            self.bottom_right["y"],
            origin_x,
            origin_y,
            theta,
        )
        self.bottom_left["x"], self.bottom_left["y"] = rotate_point(
            self.bottom_left["x"],
            self.bottom_left["y"],
            origin_x,
            origin_y,
            theta,
        )

        # Update bounding box based on rotated corners
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

        # Update angle attributes
        self.angle_degrees += angle if not use_radians else math.degrees(angle)
        self.angle_radians += theta

        # Normalize angles to [-180, 180] degrees and [-π, π] radians
        while self.angle_degrees > 180:
            self.angle_degrees -= 360
        while self.angle_degrees < -180:
            self.angle_degrees += 360

        while self.angle_radians > math.pi:
            self.angle_radians -= 2 * math.pi
        while self.angle_radians < -math.pi:
            self.angle_radians += 2 * math.pi

    def warp_affine(
        self,
        a: float,
        b: float,
        c: float,
        d: float,
        e: float,
        f: float,
    ) -> None:
        """Applies an affine transformation to the entity.

        The transformation is defined by the matrix:
        [x']   [a  b  c] [x]
        [y'] = [d  e  f] [y]
        [1 ]   [0  0  1] [1]

        Args:
            a, b, c, d, e, f (float): The affine transformation coefficients.
        """

        # Transform all corner points
        def transform_point(px: float, py: float) -> Tuple[float, float]:
            """Apply affine transformation to a point."""
            nx = a * px + b * py + c
            ny = d * px + e * py + f
            return nx, ny

        # Transform corners
        self.top_right["x"], self.top_right["y"] = transform_point(
            self.top_right["x"], self.top_right["y"]
        )
        self.top_left["x"], self.top_left["y"] = transform_point(
            self.top_left["x"], self.top_left["y"]
        )
        self.bottom_right["x"], self.bottom_right["y"] = transform_point(
            self.bottom_right["x"], self.bottom_right["y"]
        )
        self.bottom_left["x"], self.bottom_left["y"] = transform_point(
            self.bottom_left["x"], self.bottom_left["y"]
        )

        # Update bounding box based on transformed corners
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

        # Update angle based on the transformation
        # The angle is affected by the linear part of the transformation
        import math

        # Calculate how a horizontal unit vector is transformed
        dx, dy = transform_point(1, 0)
        origin_x, origin_y = transform_point(0, 0)
        new_angle = math.atan2(dy - origin_y, dx - origin_x)
        self.angle_radians = new_angle
        self.angle_degrees = math.degrees(new_angle)

    def warp_affine_normalized_forward(
        self,
        a: float,
        b: float,
        c: float,
        d: float,
        e: float,
        f: float,
        src_width: float,
        src_height: float,
        dst_width: float,
        dst_height: float,
        flip_y: bool = False,
    ) -> None:
        """Applies a normalized forward affine transformation to the entity.

        This method applies an affine transformation where the c and f parameters
        are normalized offsets that get scaled based on the bounding box dimensions
        and the source/destination dimensions.

        The actual offset applied is:
        - x_offset = c * (bounding_box.width / (src_width * dst_width))
        - y_offset = f * (bounding_box.height / (src_height * dst_height))

        Args:
            a, b, c, d, e, f: The affine transformation coefficients
            src_width: Source image width
            src_height: Source image height
            dst_width: Destination image width
            dst_height: Destination image height
            flip_y: Whether to flip Y coordinates (not used in current implementation)
        """
        # Calculate the scaled offsets based on bounding box and image dimensions
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

    def rotate_90_ccw_in_place(
        self, old_width: float, old_height: float
    ) -> None:
        """
        Rotates the entity 90 degrees counter-clockwise in place.

        This is a special transformation used when rotating an entire image/page
        90 degrees counter-clockwise. The coordinates are transformed according
        to the formula:
        new_x = old_y
        new_y = old_width - old_x

        This accounts for the coordinate system change when rotating a page.

        Args:
            old_width: The width of the original image/page
            old_height: The height of the original image/page
        """

        # Transform all corner points according to 90 degree CCW rotation
        def rotate_90_ccw(px: float, py: float) -> Tuple[float, float]:
            """Rotate a point 90 degrees counter-clockwise with coordinate adjustment."""
            return py, -(px - 1)

        # Transform corners
        self.top_right["x"], self.top_right["y"] = rotate_90_ccw(
            self.top_right["x"], self.top_right["y"]
        )
        self.top_left["x"], self.top_left["y"] = rotate_90_ccw(
            self.top_left["x"], self.top_left["y"]
        )
        self.bottom_right["x"], self.bottom_right["y"] = rotate_90_ccw(
            self.bottom_right["x"], self.bottom_right["y"]
        )
        self.bottom_left["x"], self.bottom_left["y"] = rotate_90_ccw(
            self.bottom_left["x"], self.bottom_left["y"]
        )

        # Update bounding box based on transformed corners
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

        # Update angle - add 90 degrees
        import math

        self.angle_degrees += 90
        self.angle_radians += math.pi / 2

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
            pivot_x (float): The x-coordinate of the pivot point (default: 0.0).
            pivot_y (float): The y-coordinate of the pivot point (default: 0.0).
        """
        # Shear all corner points
        self.top_right["x"], self.top_right["y"] = shear_point(
            self.top_right["x"],
            self.top_right["y"],
            pivot_x,
            pivot_y,
            shx,
            shy,
        )
        self.top_left["x"], self.top_left["y"] = shear_point(
            self.top_left["x"], self.top_left["y"], pivot_x, pivot_y, shx, shy
        )
        self.bottom_right["x"], self.bottom_right["y"] = shear_point(
            self.bottom_right["x"],
            self.bottom_right["y"],
            pivot_x,
            pivot_y,
            shx,
            shy,
        )
        self.bottom_left["x"], self.bottom_left["y"] = shear_point(
            self.bottom_left["x"],
            self.bottom_left["y"],
            pivot_x,
            pivot_y,
            shx,
            shy,
        )

        # Update bounding box based on transformed corner points
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

    # pylint: disable=too-many-arguments,too-many-locals
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
        """Applies inverse perspective transform from warped space back to
        original.

        This uses a 2x2 linear system approach optimized for receipt
        coordinate systems.

        Args:
            a, b, c, d, e, f, g, h (float): The perspective coefficients that
                mapped the original image -> new image. They are inverted
                here so we can map new coords -> old coords.
            src_width (int): The original (old) image width in pixels.
            src_height (int): The original (old) image height in pixels.
            dst_width (int): The new (warped) image width in pixels.
            dst_height (int): The new (warped) image height in pixels.
            flip_y (bool): If True, we treat the new coordinate system as
                flipped in Y (e.g. some OCR engines treat top=0).
        """
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
                # If the new system's Y=0 was at the top, then from the
                # perspective of a typical "bottom=0" system, we flip:
                y_new_px = dst_height - y_new_px

            # 2) Solve the perspective equations for old pixel coords
            # (X_old, Y_old).
            # We have the system:
            #   x_new_px = (a*X_old + b*Y_old + c) / (1 + g*X_old + h*Y_old)
            #   y_new_px = (d*X_old + e*Y_old + f) / (1 + g*X_old + h*Y_old)
            #
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
                # Degenerate or singular.
                raise ValueError(
                    "Inverse perspective transform is singular for this "
                    "corner."
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

    # Add other geometry methods as needed (rotate, shear, warp_affine, etc.)
    # We can add them later if they're actually being used


class GeometrySerializationMixin:
    """Mixin providing common geometry field serialization for DynamoDB
    entities.

    This mixin provides a standardized way to serialize geometry fields (text,
    bounding box, corner points, angles, and confidence) for entities that
    implement the GeometryProtocol.

    The mixin assumes the class has the following attributes:
    - text: str
    - bounding_box: dict with 'x', 'y', 'width', 'height' keys
    - top_left, top_right, bottom_left, bottom_right: dict with 'x', 'y' keys
    - angle_degrees: float
    - angle_radians: float
    - confidence: float
    """

    # These attributes will be provided by the class using this mixin
    text: str
    bounding_box: Dict[str, float]
    top_left: Dict[str, float]
    top_right: Dict[str, float]
    bottom_left: Dict[str, float]
    bottom_right: Dict[str, float]
    angle_degrees: float
    angle_radians: float
    confidence: float

    def _get_geometry_fields(self) -> Dict[str, Any]:
        """Returns the common geometry fields serialization for DynamoDB.

        Returns:
            Dict[str, Any]: A dictionary containing serialized geometry fields
                with DynamoDB type descriptors.
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


class GeometryValidationMixin:
    """Mixin providing common geometry field validation for entities.

    This mixin provides a standardized way to validate geometry fields
    (bounding box, corner points, angles, and confidence) for entities that
    have these fields. It eliminates duplicate validation code across geometry
    entities.

    The mixin assumes the class has the following attributes:
    - bounding_box: dict with 'x', 'y', 'width', 'height' keys
    - top_left, top_right, bottom_left, bottom_right: dict with 'x', 'y' keys
    - angle_degrees: float or int (will be converted to float)
    - angle_radians: float or int (will be converted to float)
    - confidence: float or int (will be converted to float)
    """

    # These attributes will be provided by the class using this mixin
    bounding_box: Dict[str, float]
    top_left: Dict[str, float]
    top_right: Dict[str, float]
    bottom_left: Dict[str, float]
    bottom_right: Dict[str, float]
    angle_degrees: Union[float, int]
    angle_radians: Union[float, int]
    confidence: Union[float, int]

    def _validate_geometry_fields(self) -> None:
        """Validates and normalizes common geometry fields.

        This method should be called from the entity's __post_init__ method
        to validate all geometry-related fields. It performs the following
        validations:

        1. Validates bounding box structure and values
        2. Validates all four corner points
        3. Normalizes and validates angle_degrees and angle_radians
        4. Normalizes and validates confidence (0 < confidence <= 1)

        Raises:
            ValueError: If any field has invalid type or value
            AssertionError: If bounding box or points have invalid structure
        """
        # Validate bounding box and corner points using existing utilities
        assert_valid_bounding_box(self.bounding_box)
        assert_valid_point(self.top_right)
        assert_valid_point(self.top_left)
        assert_valid_point(self.bottom_right)
        assert_valid_point(self.bottom_left)

        # Validate and normalize angle_degrees
        if not isinstance(self.angle_degrees, (float, int)):
            raise ValueError(
                f"angle_degrees must be float or int, got "
                f"{type(self.angle_degrees).__name__}"
            )
        self.angle_degrees = float(self.angle_degrees)

        # Validate and normalize angle_radians
        if not isinstance(self.angle_radians, (float, int)):
            raise ValueError(
                f"angle_radians must be float or int, got "
                f"{type(self.angle_radians).__name__}"
            )
        self.angle_radians = float(self.angle_radians)

        # Validate and normalize confidence with special handling for int
        if isinstance(self.confidence, int):
            self.confidence = float(self.confidence)
        if not isinstance(self.confidence, float):
            raise ValueError(
                "confidence must be float or int, got "
                f"{type(self.confidence).__name__}"
            )
        if not 0.0 < self.confidence <= 1.0:
            raise ValueError(
                "confidence must be between 0 and 1, got " f"{self.confidence}"
            )


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Protocols
    "HasKey",
    "HasGeometryAttributes",
    # Core mixins
    "SerializationMixin",
    "BaseEntityMixin",
    "CDNFieldsMixin",
    # Geometry mixins
    "GeometryMixin",
    "GeometrySerializationMixin",
    "GeometryValidationMixin",
    # Specialized geometry mixins
    "GeometryHashMixin",
    "GeometryReprMixin",
    "WarpTransformMixin",
    "GeometryValidationUtilsMixin",
]
