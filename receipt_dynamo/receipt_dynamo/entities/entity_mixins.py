"""
Consolidated entity mixins for the receipt_dynamo package.

This module combines all entity mixins to reduce file sprawl and make the
codebase easier to navigate. It includes:
- DynamoDB serialization/deserialization
- Common validation patterns
- CDN field handling

Note: Geometry-related mixins have been consolidated into TextGeometryEntity
and ReceiptTextGeometryEntity base classes. See text_geometry_entity.py and
receipt_text_geometry_entity.py for geometry operations.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import (
    Any,
    Protocol,
    TypeVar,
)

from receipt_dynamo.entities.util import _repr_str


T = TypeVar("T")


# Protocol classes define the expected interface for mixins
class HasKey(Protocol):
    """Protocol for entities that have a DynamoDB key property."""

    @property
    def key(self) -> dict[str, Any]: ...


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
        gsi_methods: list[str] | None = None,
        custom_fields: dict[str, Any] | None = None,
        exclude_fields: set[str] | None = None,
    ) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
        """
        Convert a Python value to DynamoDB attribute format.

        Args:
            value: The value to serialize
            serialize_decimal: Whether to use Decimal serialization for floats

        Returns:
            DynamoDB attribute dict like {"S": "text"} or {"N": "123"}
        """
        # pylint: disable=too-many-return-statements
        # Maps Python types to DynamoDB type markers. Each Python type (None,
        # str, bool, int, float, datetime, list, dict, set) requires a
        # different DynamoDB representation, making multiple returns inherent
        # to this type dispatch.
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
        # pylint: enable=too-many-return-statements

    def _python_to_dynamo(self, value: Any) -> dict[str, Any]:
        """
        Convert a Python value to a DynamoDB typed value.

        This is an alias for _serialize_value to maintain compatibility
        with entities using the _python_to_dynamo naming convention.
        """
        return self._serialize_value(value)

    @staticmethod
    def _dynamo_to_python(dynamo_value: dict[str, Any]) -> Any:
        """
        Convert a DynamoDB typed value to a Python value.

        This is a static method to maintain compatibility with entities
        using the _dynamo_to_python naming convention.
        """
        # pylint: disable=too-many-return-statements
        # DynamoDB has 9 distinct type markers (NULL, S, N, BOOL, M, L, SS, NS,
        # BS). Each requires different handling, making multiple returns
        # inherent to this type dispatch logic.
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
        # pylint: enable=too-many-return-statements

    @classmethod
    def safe_deserialize_field(
        cls,
        item: dict[str, Any],
        field_name: str,
        default: Any = None,
        field_type: type | None = None,
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
                if field_type is dict and isinstance(value, dict):
                    return value
                if field_type is list and isinstance(value, list):
                    return value
                if not isinstance(value, field_type):
                    raise TypeError(
                        f"Expected {field_type.__name__} for field "
                        f"{field_name}, got {type(value).__name__}"
                    )

            return value
        except (KeyError, TypeError, ValueError):
            # Return default on any deserialization error
            return default

    @classmethod
    def validate_required_keys(
        cls, item: dict[str, Any], required_keys: set[str]
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
        cls, item: dict[str, Any], pk_pattern: str, sk_pattern: str
    ) -> dict[str, str | int]:
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
# CDN Fields
# =============================================================================


@dataclass(kw_only=True)
class CDNFieldsMixin:
    """
    Mixin that provides CDN S3 key fields, validation, and serialization.

    This mixin defines all CDN-related fields as dataclass fields with
    kw_only=True, allowing subclasses to define their own required fields
    before these optional CDN fields.

    Fields:
        sha256: SHA256 hash of the image
        cdn_s3_bucket: S3 bucket for CDN-hosted image
        cdn_s3_key: S3 key for original CDN image
        cdn_webp_s3_key: S3 key for WebP version
        cdn_avif_s3_key: S3 key for AVIF version
        cdn_thumbnail_*: Thumbnail size variants
        cdn_small_*: Small size variants
        cdn_medium_*: Medium size variants
    """

    # CDN fields - all optional with None defaults
    sha256: str | None = None
    cdn_s3_bucket: str | None = None
    cdn_s3_key: str | None = None
    cdn_webp_s3_key: str | None = None
    cdn_avif_s3_key: str | None = None
    # Thumbnail versions
    cdn_thumbnail_s3_key: str | None = None
    cdn_thumbnail_webp_s3_key: str | None = None
    cdn_thumbnail_avif_s3_key: str | None = None
    # Small versions
    cdn_small_s3_key: str | None = None
    cdn_small_webp_s3_key: str | None = None
    cdn_small_avif_s3_key: str | None = None
    # Medium versions
    cdn_medium_s3_key: str | None = None
    cdn_medium_webp_s3_key: str | None = None
    cdn_medium_avif_s3_key: str | None = None

    # Define CDN field groups for iteration
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

    def cdn_fields_to_dynamodb_item(self) -> dict[str, dict[str, Any]]:
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

    def _get_cdn_repr_fields(self) -> str:
        """
        Return formatted string of CDN fields for __repr__.

        Returns:
            String with all CDN fields formatted for repr output
        """
        return (
            f"sha256={_repr_str(self.sha256)}, "
            f"cdn_s3_bucket={_repr_str(self.cdn_s3_bucket)}, "
            f"cdn_s3_key={_repr_str(self.cdn_s3_key)}, "
            f"cdn_webp_s3_key={_repr_str(self.cdn_webp_s3_key)}, "
            f"cdn_avif_s3_key={_repr_str(self.cdn_avif_s3_key)}, "
            f"cdn_thumbnail_s3_key={_repr_str(self.cdn_thumbnail_s3_key)}, "
            f"cdn_thumbnail_webp_s3_key="
            f"{_repr_str(self.cdn_thumbnail_webp_s3_key)}, "
            f"cdn_thumbnail_avif_s3_key="
            f"{_repr_str(self.cdn_thumbnail_avif_s3_key)}, "
            f"cdn_small_s3_key={_repr_str(self.cdn_small_s3_key)}, "
            f"cdn_small_webp_s3_key={_repr_str(self.cdn_small_webp_s3_key)}, "
            f"cdn_small_avif_s3_key={_repr_str(self.cdn_small_avif_s3_key)}, "
            f"cdn_medium_s3_key={_repr_str(self.cdn_medium_s3_key)}, "
            f"cdn_medium_webp_s3_key="
            f"{_repr_str(self.cdn_medium_webp_s3_key)}, "
            f"cdn_medium_avif_s3_key={_repr_str(self.cdn_medium_avif_s3_key)}"
        )

    @classmethod
    def _cdn_fields_from_item(cls, item: dict[str, Any]) -> dict[str, Any]:
        """
        Extract CDN fields from a DynamoDB item.

        Args:
            item: DynamoDB item dictionary

        Returns:
            Dictionary of CDN field names to values for entity construction
        """
        return {
            "sha256": item.get("sha256", {}).get("S"),
            "cdn_s3_bucket": item.get("cdn_s3_bucket", {}).get("S"),
            "cdn_s3_key": item.get("cdn_s3_key", {}).get("S"),
            "cdn_webp_s3_key": item.get("cdn_webp_s3_key", {}).get("S"),
            "cdn_avif_s3_key": item.get("cdn_avif_s3_key", {}).get("S"),
            "cdn_thumbnail_s3_key": item.get("cdn_thumbnail_s3_key", {}).get(
                "S"
            ),
            "cdn_thumbnail_webp_s3_key": item.get(
                "cdn_thumbnail_webp_s3_key", {}
            ).get("S"),
            "cdn_thumbnail_avif_s3_key": item.get(
                "cdn_thumbnail_avif_s3_key", {}
            ).get("S"),
            "cdn_small_s3_key": item.get("cdn_small_s3_key", {}).get("S"),
            "cdn_small_webp_s3_key": item.get("cdn_small_webp_s3_key", {}).get(
                "S"
            ),
            "cdn_small_avif_s3_key": item.get("cdn_small_avif_s3_key", {}).get(
                "S"
            ),
            "cdn_medium_s3_key": item.get("cdn_medium_s3_key", {}).get("S"),
            "cdn_medium_webp_s3_key": item.get(
                "cdn_medium_webp_s3_key", {}
            ).get("S"),
            "cdn_medium_avif_s3_key": item.get(
                "cdn_medium_avif_s3_key", {}
            ).get("S"),
        }


# =============================================================================
# Batch Result GSI Keys
# =============================================================================


class BatchResultGSIMixin:
    """
    Mixin providing shared GSI key methods for batch result entities.

    CompletionBatchResult and EmbeddingBatchResult share identical GSI2 and
    GSI3 access patterns for querying by batch/status and image/receipt.

    Required attributes on the implementing class:
        batch_id: str - The batch identifier
        image_id: str - The image identifier
        receipt_id: int - The receipt identifier (zero-padded to 5 digits)
        status: str - The batch status
    """

    # These attributes must be defined by the implementing class
    batch_id: str
    image_id: str
    receipt_id: int
    status: str

    @property
    def gsi2_key(self) -> dict[str, Any]:
        """GSI2 key for querying batch results by batch_id and status."""
        return {
            "GSI2PK": {"S": f"BATCH#{self.batch_id}"},
            "GSI2SK": {"S": f"STATUS#{self.status}"},
        }

    @property
    def gsi3_key(self) -> dict[str, Any]:
        """GSI3 key for querying batch results by image/receipt."""
        return {
            "GSI3PK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}"
            },
            "GSI3SK": {"S": f"BATCH#{self.batch_id}#STATUS#{self.status}"},
        }


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Protocols
    "HasKey",
    # Core mixins
    "SerializationMixin",
    "CDNFieldsMixin",
    "BatchResultGSIMixin",
]
