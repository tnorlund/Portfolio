"""
Immutable value objects for the receipt_dynamo package.

These frozen dataclasses encapsulate commonly duplicated attribute groups,
providing type safety and reducing repetition across entities.

Value objects are:
- Immutable (frozen=True)
- Hashable (can be used in sets/dict keys)
- Self-validating (validate in __post_init__)
- Serializable to/from DynamoDB format

Classes:
    Point: 2D coordinate (x, y)
    BoundingBox: Axis-aligned rectangle (x, y, width, height)
    Corners: Quadrilateral defined by four corner points
    Angle: Angle with both degree and radian representations
    S3Location: S3 bucket and key pair
    CDNVariants: All CDN image format/size variants
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from receipt_dynamo.entities.util import _format_float


@dataclass(frozen=True)
class Point:
    """
    Immutable 2D coordinate point.

    Used for corner coordinates (top_left, top_right, etc.) across
    geometry entities.

    Attributes:
        x: X coordinate (normalized 0-1 or pixel value)
        y: Y coordinate (normalized 0-1 or pixel value)
    """

    x: float
    y: float

    def __post_init__(self) -> None:
        """Validate and normalize to float."""
        if not isinstance(self.x, (int, float)):
            raise ValueError(f"x must be numeric, got {type(self.x).__name__}")
        if not isinstance(self.y, (int, float)):
            raise ValueError(f"y must be numeric, got {type(self.y).__name__}")
        # Convert to float if int (frozen dataclass workaround)
        object.__setattr__(self, "x", float(self.x))
        object.__setattr__(self, "y", float(self.y))

    def to_dict(self) -> Dict[str, float]:
        """Convert to dict for backwards compatibility with existing code."""
        return {"x": self.x, "y": self.y}

    def to_dynamodb(
        self, precision: int = 20, scale: int = 22
    ) -> Dict[str, Any]:
        """
        Serialize to DynamoDB map format.

        Args:
            precision: Decimal places for formatting
            scale: Total length for formatting

        Returns:
            DynamoDB-formatted map: {"M": {"x": {"N": "..."}, "y": {"N": "..."}}}
        """
        return {
            "M": {
                "x": {"N": _format_float(self.x, precision, scale)},
                "y": {"N": _format_float(self.y, precision, scale)},
            }
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Point":
        """
        Create from dictionary.

        Args:
            d: Dict with 'x' and 'y' keys

        Returns:
            Point instance
        """
        return cls(x=float(d["x"]), y=float(d["y"]))

    @classmethod
    def from_dynamodb(cls, item: Dict[str, Any]) -> "Point":
        """
        Create from DynamoDB map format.

        Args:
            item: DynamoDB map: {"M": {"x": {"N": "..."}, "y": {"N": "..."}}}

        Returns:
            Point instance
        """
        m = item["M"]
        return cls(x=float(m["x"]["N"]), y=float(m["y"]["N"]))


@dataclass(frozen=True)
class BoundingBox:
    """
    Immutable axis-aligned bounding box.

    Represents a rectangle defined by position (x, y) and dimensions
    (width, height).

    Attributes:
        x: Left edge x coordinate
        y: Top edge y coordinate
        width: Box width
        height: Box height
    """

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        """Validate and normalize all fields to float."""
        for field_name in ["x", "y", "width", "height"]:
            val = getattr(self, field_name)
            if not isinstance(val, (int, float)):
                raise ValueError(
                    f"{field_name} must be numeric, got {type(val).__name__}"
                )
            object.__setattr__(self, field_name, float(val))

    def to_dict(self) -> Dict[str, float]:
        """Convert to dict for backwards compatibility."""
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    def to_dynamodb(
        self, precision: int = 20, scale: int = 22
    ) -> Dict[str, Any]:
        """
        Serialize to DynamoDB map format.

        Returns:
            DynamoDB-formatted map with x, y, width, height as numbers
        """
        return {
            "M": {
                "x": {"N": _format_float(self.x, precision, scale)},
                "y": {"N": _format_float(self.y, precision, scale)},
                "width": {"N": _format_float(self.width, precision, scale)},
                "height": {"N": _format_float(self.height, precision, scale)},
            }
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BoundingBox":
        """Create from dictionary."""
        return cls(
            x=float(d["x"]),
            y=float(d["y"]),
            width=float(d["width"]),
            height=float(d["height"]),
        )

    @classmethod
    def from_dynamodb(cls, item: Dict[str, Any]) -> "BoundingBox":
        """Create from DynamoDB map format."""
        m = item["M"]
        return cls(
            x=float(m["x"]["N"]),
            y=float(m["y"]["N"]),
            width=float(m["width"]["N"]),
            height=float(m["height"]["N"]),
        )

    @property
    def right(self) -> float:
        """Right edge x coordinate."""
        return self.x + self.width

    @property
    def bottom(self) -> float:
        """Bottom edge y coordinate."""
        return self.y + self.height

    @property
    def center(self) -> Point:
        """Center point of the bounding box."""
        return Point(x=self.x + self.width / 2, y=self.y + self.height / 2)

    @property
    def area(self) -> float:
        """Area of the bounding box."""
        return self.width * self.height

    def contains_point(self, x: float, y: float) -> bool:
        """Check if point (x, y) is inside this bounding box."""
        return self.x <= x <= self.right and self.y <= y <= self.bottom


@dataclass(frozen=True)
class Corners:
    """
    Immutable quadrilateral defined by four corner points.

    Used for rotated bounding boxes where an axis-aligned box isn't
    sufficient. Common in OCR entities where text may be at an angle.

    Attributes:
        top_left: Top-left corner point
        top_right: Top-right corner point
        bottom_left: Bottom-left corner point
        bottom_right: Bottom-right corner point
    """

    top_left: Point
    top_right: Point
    bottom_left: Point
    bottom_right: Point

    def to_dict(self) -> Dict[str, Dict[str, float]]:
        """Convert to nested dict for backwards compatibility."""
        return {
            "top_left": self.top_left.to_dict(),
            "top_right": self.top_right.to_dict(),
            "bottom_left": self.bottom_left.to_dict(),
            "bottom_right": self.bottom_right.to_dict(),
        }

    def to_dynamodb(
        self, precision: int = 20, scale: int = 22
    ) -> Dict[str, Dict[str, Any]]:
        """Serialize all corners to DynamoDB format."""
        return {
            "top_left": self.top_left.to_dynamodb(precision, scale),
            "top_right": self.top_right.to_dynamodb(precision, scale),
            "bottom_left": self.bottom_left.to_dynamodb(precision, scale),
            "bottom_right": self.bottom_right.to_dynamodb(precision, scale),
        }

    @classmethod
    def from_dicts(
        cls,
        top_left: Dict[str, Any],
        top_right: Dict[str, Any],
        bottom_left: Dict[str, Any],
        bottom_right: Dict[str, Any],
    ) -> "Corners":
        """
        Create from four corner dictionaries.

        This is the primary factory method for backwards compatibility
        with existing code that uses dict-based corners.
        """
        return cls(
            top_left=Point.from_dict(top_left),
            top_right=Point.from_dict(top_right),
            bottom_left=Point.from_dict(bottom_left),
            bottom_right=Point.from_dict(bottom_right),
        )

    @classmethod
    def from_dynamodb(cls, item: Dict[str, Any]) -> "Corners":
        """Create from DynamoDB item containing corner fields."""
        return cls(
            top_left=Point.from_dynamodb(item["top_left"]),
            top_right=Point.from_dynamodb(item["top_right"]),
            bottom_left=Point.from_dynamodb(item["bottom_left"]),
            bottom_right=Point.from_dynamodb(item["bottom_right"]),
        )

    @property
    def centroid(self) -> Point:
        """Calculate the center point of the quadrilateral."""
        x = (
            self.top_left.x
            + self.top_right.x
            + self.bottom_left.x
            + self.bottom_right.x
        ) / 4
        y = (
            self.top_left.y
            + self.top_right.y
            + self.bottom_left.y
            + self.bottom_right.y
        ) / 4
        return Point(x=x, y=y)

    def to_bounding_box(self) -> BoundingBox:
        """
        Calculate axis-aligned bounding box containing all corners.

        Returns:
            BoundingBox that fully contains this quadrilateral
        """
        xs = [
            self.top_left.x,
            self.top_right.x,
            self.bottom_left.x,
            self.bottom_right.x,
        ]
        ys = [
            self.top_left.y,
            self.top_right.y,
            self.bottom_left.y,
            self.bottom_right.y,
        ]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        return BoundingBox(
            x=min_x, y=min_y, width=max_x - min_x, height=max_y - min_y
        )


@dataclass(frozen=True)
class Angle:
    """
    Immutable angle with both degree and radian representations.

    Provides factory methods to create from either unit and ensures
    both representations are always in sync.

    Attributes:
        degrees: Angle in degrees
        radians: Angle in radians
    """

    degrees: float
    radians: float

    def __post_init__(self) -> None:
        """Normalize to float."""
        object.__setattr__(self, "degrees", float(self.degrees))
        object.__setattr__(self, "radians", float(self.radians))

    @classmethod
    def from_degrees(cls, degrees: float) -> "Angle":
        """Create from degrees, computing radians automatically."""
        import math

        return cls(degrees=degrees, radians=math.radians(degrees))

    @classmethod
    def from_radians(cls, radians: float) -> "Angle":
        """Create from radians, computing degrees automatically."""
        import math

        return cls(degrees=math.degrees(radians), radians=radians)

    def normalized(self) -> "Angle":
        """
        Return normalized angle in range [-180, 180] degrees.

        Returns:
            New Angle instance with normalized values
        """
        import math

        deg = self.degrees
        while deg > 180:
            deg -= 360
        while deg < -180:
            deg += 360

        rad = self.radians
        while rad > math.pi:
            rad -= 2 * math.pi
        while rad < -math.pi:
            rad += 2 * math.pi

        return Angle(degrees=deg, radians=rad)


@dataclass(frozen=True)
class S3Location:
    """
    Immutable S3 location reference.

    Consolidates bucket + key pairs that are commonly used together.

    Attributes:
        bucket: S3 bucket name
        key: S3 object key
    """

    bucket: str
    key: str

    def __post_init__(self) -> None:
        """Validate string fields."""
        if not isinstance(self.bucket, str):
            raise ValueError(
                f"bucket must be string, got {type(self.bucket).__name__}"
            )
        if not isinstance(self.key, str):
            raise ValueError(
                f"key must be string, got {type(self.key).__name__}"
            )

    @property
    def uri(self) -> str:
        """Return S3 URI (s3://bucket/key)."""
        return f"s3://{self.bucket}/{self.key}"

    def to_dynamodb(self) -> Dict[str, Dict[str, str]]:
        """Serialize to DynamoDB format."""
        return {
            "bucket": {"S": self.bucket},
            "key": {"S": self.key},
        }

    @classmethod
    def from_dynamodb(cls, item: Dict[str, Any]) -> "S3Location":
        """Create from DynamoDB format."""
        return cls(bucket=item["bucket"]["S"], key=item["key"]["S"])


@dataclass(frozen=True)
class CDNVariants:
    """
    Consolidated CDN image variants for different sizes and formats.

    Replaces 12 individual Optional[str] fields with a single object
    that manages all CDN key variants. Provides backward-compatible
    properties for accessing individual fields.

    Size variants:
        - original: Full-size image
        - thumbnail: ~150px
        - small: ~300px
        - medium: ~600px

    Format variants for each size:
        - original format (typically JPEG/PNG)
        - webp: WebP format
        - avif: AVIF format

    Attributes:
        original: Original format, full size
        webp: WebP format, full size
        avif: AVIF format, full size
        thumbnail: Original format, thumbnail size
        thumbnail_webp: WebP format, thumbnail size
        thumbnail_avif: AVIF format, thumbnail size
        small: Original format, small size
        small_webp: WebP format, small size
        small_avif: AVIF format, small size
        medium: Original format, medium size
        medium_webp: WebP format, medium size
        medium_avif: AVIF format, medium size
    """

    # Full size variants
    original: Optional[str] = None
    webp: Optional[str] = None
    avif: Optional[str] = None
    # Thumbnail variants (~150px)
    thumbnail: Optional[str] = None
    thumbnail_webp: Optional[str] = None
    thumbnail_avif: Optional[str] = None
    # Small variants (~300px)
    small: Optional[str] = None
    small_webp: Optional[str] = None
    small_avif: Optional[str] = None
    # Medium variants (~600px)
    medium: Optional[str] = None
    medium_webp: Optional[str] = None
    medium_avif: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate all fields are strings or None."""
        for field_name in [
            "original",
            "webp",
            "avif",
            "thumbnail",
            "thumbnail_webp",
            "thumbnail_avif",
            "small",
            "small_webp",
            "small_avif",
            "medium",
            "medium_webp",
            "medium_avif",
        ]:
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise ValueError(
                    f"{field_name} must be string or None, "
                    f"got {type(value).__name__}"
                )

    def to_dynamodb(self) -> Dict[str, Any]:
        """
        Serialize all CDN fields to DynamoDB format.

        Returns:
            Dict with all cdn_*_s3_key fields in DynamoDB format
        """
        field_mapping = {
            "cdn_s3_key": self.original,
            "cdn_webp_s3_key": self.webp,
            "cdn_avif_s3_key": self.avif,
            "cdn_thumbnail_s3_key": self.thumbnail,
            "cdn_thumbnail_webp_s3_key": self.thumbnail_webp,
            "cdn_thumbnail_avif_s3_key": self.thumbnail_avif,
            "cdn_small_s3_key": self.small,
            "cdn_small_webp_s3_key": self.small_webp,
            "cdn_small_avif_s3_key": self.small_avif,
            "cdn_medium_s3_key": self.medium,
            "cdn_medium_webp_s3_key": self.medium_webp,
            "cdn_medium_avif_s3_key": self.medium_avif,
        }
        result: Dict[str, Any] = {}
        for field_name, value in field_mapping.items():
            result[field_name] = {"S": value} if value else {"NULL": True}
        return result

    @classmethod
    def from_dynamodb(cls, item: Dict[str, Any]) -> "CDNVariants":
        """
        Extract CDN fields from DynamoDB item.

        Args:
            item: DynamoDB item containing cdn_*_s3_key fields

        Returns:
            CDNVariants instance with all available fields
        """

        def get_optional(key: str) -> Optional[str]:
            if key in item and "S" in item[key]:
                return item[key]["S"]
            return None

        return cls(
            original=get_optional("cdn_s3_key"),
            webp=get_optional("cdn_webp_s3_key"),
            avif=get_optional("cdn_avif_s3_key"),
            thumbnail=get_optional("cdn_thumbnail_s3_key"),
            thumbnail_webp=get_optional("cdn_thumbnail_webp_s3_key"),
            thumbnail_avif=get_optional("cdn_thumbnail_avif_s3_key"),
            small=get_optional("cdn_small_s3_key"),
            small_webp=get_optional("cdn_small_webp_s3_key"),
            small_avif=get_optional("cdn_small_avif_s3_key"),
            medium=get_optional("cdn_medium_s3_key"),
            medium_webp=get_optional("cdn_medium_webp_s3_key"),
            medium_avif=get_optional("cdn_medium_avif_s3_key"),
        )

    def has_any_variant(self) -> bool:
        """Check if any CDN variant is set."""
        return any(
            [
                self.original,
                self.webp,
                self.avif,
                self.thumbnail,
                self.thumbnail_webp,
                self.thumbnail_avif,
                self.small,
                self.small_webp,
                self.small_avif,
                self.medium,
                self.medium_webp,
                self.medium_avif,
            ]
        )

    def get_best_variant(self, size: str = "original") -> Optional[str]:
        """
        Get the best available variant for a given size.

        Prefers AVIF > WebP > original format.

        Args:
            size: One of "original", "thumbnail", "small", "medium"

        Returns:
            Best available S3 key for the requested size, or None
        """
        if size == "original":
            return self.avif or self.webp or self.original
        elif size == "thumbnail":
            return self.thumbnail_avif or self.thumbnail_webp or self.thumbnail
        elif size == "small":
            return self.small_avif or self.small_webp or self.small
        elif size == "medium":
            return self.medium_avif or self.medium_webp or self.medium
        return None


__all__ = [
    "Point",
    "BoundingBox",
    "Corners",
    "Angle",
    "S3Location",
    "CDNVariants",
]
