"""
Type-safe Entity Factory for DynamoDB item_to_* converter functions.

This version maintains full MyPy compatibility while eliminating code
duplication.
"""

from datetime import datetime
from math import isfinite
from typing import (
    Any,
    Callable,
    Mapping,
    Protocol,
    Type,
    TypeVar,
    cast,
)

from receipt_dynamo.constants import EmbeddingStatus

from .entity_mixins import SerializationMixin
from .util import (
    deserialize_bounding_box,
    deserialize_confidence,
    deserialize_coordinate_point,
)

T = TypeVar("T")


class DynamoDBItemExtractor(Protocol):
    """Protocol for type-safe field extractors."""

    def __call__(self, item: dict[str, Any]) -> Any:
        """Extract a field value from a DynamoDB item."""


KeyParser = Callable[[str], dict[str, Any]]


class EntityFactory(SerializationMixin):
    """
    Type-safe factory for creating entities from DynamoDB items.

    Provides full MyPy compatibility while eliminating code duplication.
    """

    @classmethod
    # pylint: disable=too-many-positional-arguments
    def create_entity(
        cls,
        entity_class: Type[T],
        item: dict[str, Any],
        required_keys: set[str],
        field_mappings: dict[str, str] | None = None,
        custom_extractors: (
            Mapping[str, DynamoDBItemExtractor | None] | None
        ) = None,
        key_parsers: Mapping[str, KeyParser | None] | None = None,
    ) -> T:
        """
        Generic entity creation from DynamoDB item with full type safety.

        Args:
            entity_class: The entity class to instantiate
            item: DynamoDB item dictionary
            required_keys: Set of required field names
            field_mappings: Map DynamoDB field names to entity field names
            custom_extractors: Type-safe extraction functions for
                complex fields
            key_parsers: Type-safe functions to parse PK/SK components

        Returns:
            Instance of entity_class

        Raises:
            ValueError: If required keys are missing or parsing fails
        """
        field_mappings = field_mappings or {}
        custom_extractors = custom_extractors or {}
        key_parsers = key_parsers or {}

        # Resolve required DynamoDB fields to constructor arguments.  The
        # documented mapping orientation is item field -> constructor field.
        # Preserve the historical inverse orientation when only the mapped
        # item field is present.
        resolved_fields: list[tuple[str, str]] = []
        missing_keys: set[str] = set()
        for required_name in required_keys:
            if required_name in {"PK", "SK", "TYPE"}:
                if required_name not in item:
                    missing_keys.add(required_name)
                continue
            if required_name in item:
                resolved_fields.append(
                    (
                        required_name,
                        field_mappings.get(required_name, required_name),
                    )
                )
                continue
            mapped_name = field_mappings.get(required_name)
            if mapped_name is not None and mapped_name in item:
                resolved_fields.append((mapped_name, required_name))
                continue
            inverse_mapping = next(
                (
                    item_name
                    for item_name, constructor_name in field_mappings.items()
                    if constructor_name == required_name and item_name in item
                ),
                None,
            )
            if inverse_mapping is not None:
                resolved_fields.append((inverse_mapping, required_name))
            else:
                missing_keys.add(required_name)
        if missing_keys:
            raise ValueError(f"Item is missing required keys: {missing_keys}")

        # Extract constructor arguments
        kwargs: dict[str, Any] = {}

        # Parse key components if needed
        for key_name, parser in key_parsers.items():
            if parser is None:
                continue
            if key_name == "PK" and "PK" in item:
                parsed_data = parser(cls._extract_key_string(item, "PK"))
                kwargs.update(parsed_data)
            elif key_name == "SK" and "SK" in item:
                parsed_data = parser(cls._extract_key_string(item, "SK"))
                kwargs.update(parsed_data)

        # Extract fields using custom extractors first
        for field_name, extractor in custom_extractors.items():
            if extractor is None:
                continue
            kwargs[field_name] = extractor(item)

        # Extract remaining required fields
        for item_key, field_name in resolved_fields:
            if custom_extractors.get(field_name) is not None:
                continue  # Already handled above

            # Use safe deserialization
            kwargs[field_name] = cls.safe_deserialize_field(item, item_key)

        try:
            return entity_class(**kwargs)
        except Exception as e:
            raise ValueError(
                f"Failed to create {entity_class.__name__}: {e}"
            ) from e

    @staticmethod
    def _extract_key_string(item: dict[str, Any], key_name: str) -> str:
        """Return a DynamoDB string key or raise a stable validation error."""
        key = item[key_name]
        if not isinstance(key, dict) or set(key) != {"S"}:
            raise ValueError(
                f"Field '{key_name}' must be a DynamoDB string (S)"
            )
        value = key["S"]
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"Field '{key_name}' must contain a non-empty string"
            )
        return value

    @classmethod
    def parse_image_receipt_key(cls, pk: str, sk: str) -> dict[str, Any]:
        """
        Parse common IMAGE#/RECEIPT# key pattern with type safety.

        Args:
            pk: Primary key like "IMAGE#{uuid}"
            sk: Sort key like "RECEIPT#{id:05d}#..."

        Returns:
            Dictionary with image_id and receipt_id
        """
        try:
            # Parse PK: IMAGE#{uuid}
            if not pk.startswith("IMAGE#"):
                raise ValueError(f"Invalid PK format: {pk}")
            image_id = pk.split("#", 1)[1]
            if not image_id:
                raise ValueError(f"Invalid PK format: {pk}")

            # Parse SK: RECEIPT#{id:05d}#...
            if not sk.startswith("RECEIPT#"):
                raise ValueError(f"Invalid SK format: {sk}")
            sk_parts = sk.split("#")
            if len(sk_parts) < 2 or not sk_parts[1]:
                raise ValueError(f"Invalid SK format: {sk}")
            receipt_id = int(sk_parts[1])

            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "sk_parts": sk_parts,  # list[str]
            }
        except (IndexError, ValueError) as e:
            raise ValueError(f"Error parsing IMAGE/RECEIPT keys: {e}") from e

    @classmethod
    def parse_receipt_line_word_key(cls, pk: str, sk: str) -> dict[str, Any]:
        """
        Parse RECEIPT#/LINE#/WORD# key pattern with type safety.

        Args:
            pk: Primary key like "IMAGE#{uuid}"
            sk: Sort key like
                "RECEIPT#{receipt:05d}#LINE#{line:05d}#WORD#{word:05d}"

        Returns:
            Dictionary with image_id, receipt_id, line_id, word_id
        """
        try:
            # Parse PK: IMAGE#{uuid}
            if not pk.startswith("IMAGE#"):
                raise ValueError(f"Invalid PK format: {pk}")
            image_id = pk.split("#", 1)[1]
            if not image_id:
                raise ValueError(f"Invalid PK format: {pk}")

            # Parse SK: RECEIPT#{receipt:05d}#LINE#{line:05d}#WORD#{word:05d}
            if not sk.startswith("RECEIPT#"):
                raise ValueError(f"Invalid SK format: {sk}")
            sk_parts = sk.split("#")

            if (
                len(sk_parts) != 6
                or sk_parts[2] != "LINE"
                or sk_parts[4] != "WORD"
            ):
                raise ValueError(
                    f"Invalid SK format for RECEIPT/LINE/WORD: {sk}"
                )

            receipt_id = int(sk_parts[1])
            line_id = int(sk_parts[3])
            word_id = int(sk_parts[5])

            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "line_id": line_id,
                "word_id": word_id,
            }
        except (IndexError, ValueError) as e:
            raise ValueError(
                f"Error parsing RECEIPT/LINE/WORD key: {e}"
            ) from e

    # Type-safe field extractors
    @staticmethod
    def extract_geometry_fields(item: dict[str, Any]) -> dict[str, Any]:
        """
        Extract common geometry fields with type safety.

        Args:
            item: DynamoDB item

        Returns:
            Dictionary of geometry field values
        """
        return {
            "bounding_box": deserialize_bounding_box(item["bounding_box"]),
            "top_right": deserialize_coordinate_point(item["top_right"]),
            "top_left": deserialize_coordinate_point(item["top_left"]),
            "bottom_right": deserialize_coordinate_point(item["bottom_right"]),
            "bottom_left": deserialize_coordinate_point(item["bottom_left"]),
            "angle_degrees": float(item["angle_degrees"]["N"]),
            "angle_radians": float(item["angle_radians"]["N"]),
            "confidence": deserialize_confidence(item["confidence"]),
        }

    @staticmethod
    def extract_text_field(item: dict[str, Any]) -> str:
        """Extract text field from DynamoDB item with type safety."""
        if "text" not in item:
            raise ValueError("Missing required field: text")
        attribute = item["text"]
        if (
            not isinstance(attribute, dict)
            or set(attribute) != {"S"}
            or not isinstance(attribute["S"], str)
        ):
            raise ValueError(
                "Field 'text' must be a string type (S), got: "
                + str(attribute)
            )
        return cast(str, attribute["S"])

    @staticmethod
    def extract_optional_extracted_data(
        item: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Extract optional extracted_data field with type safety."""
        if "extracted_data" not in item:
            return None
        attribute = item["extracted_data"]
        if attribute == {"NULL": True}:
            return None
        if not isinstance(attribute, dict) or set(attribute) != {"M"}:
            raise ValueError(
                "extracted_data must be a DynamoDB map (M) or NULL"
            )
        extracted_data = attribute["M"]
        if not isinstance(extracted_data, dict) or not set(
            extracted_data
        ).issubset({"type", "value"}):
            raise ValueError(
                "extracted_data must contain only type and value strings"
            )
        result: dict[str, Any] = {}

        for field_name in ("type", "value"):
            if field_name not in extracted_data:
                continue
            value = extracted_data[field_name]
            if (
                not isinstance(value, dict)
                or set(value) != {"S"}
                or not isinstance(value["S"], str)
            ):
                raise ValueError(
                    f"extracted_data.{field_name} must be a "
                    "DynamoDB string (S)"
                )
            result[field_name] = cast(str, value["S"])

        return result if result else None

    @staticmethod
    def extract_embedding_status(item: dict[str, Any]) -> str:
        """Extract embedding_status with default and type safety."""
        if "embedding_status" not in item:
            return EmbeddingStatus.NONE.value
        attribute = item["embedding_status"]
        if attribute == {"NULL": True}:
            return EmbeddingStatus.NONE.value
        if (
            not isinstance(attribute, dict)
            or set(attribute) != {"S"}
            or not isinstance(attribute["S"], str)
        ):
            raise ValueError("embedding_status must be a DynamoDB string (S)")
        return cast(str, attribute["S"])

    @staticmethod
    def extract_is_noise(item: dict[str, Any]) -> bool:
        """Extract is_noise boolean with default False and type safety."""
        if "is_noise" not in item:
            return False
        attribute = item["is_noise"]
        if attribute == {"NULL": True}:
            return False
        if (
            not isinstance(attribute, dict)
            or set(attribute) != {"BOOL"}
            or not isinstance(attribute["BOOL"], bool)
        ):
            raise ValueError("is_noise must be a DynamoDB boolean (BOOL)")
        return cast(bool, attribute["BOOL"])

    @staticmethod
    def extract_datetime_field(field_name: str) -> DynamoDBItemExtractor:
        """Create a type-safe datetime field extractor."""

        def extractor(item: dict[str, Any]) -> datetime:
            if field_name not in item:
                raise ValueError(f"Missing required field: {field_name}")
            attribute = item[field_name]
            if (
                not isinstance(attribute, dict)
                or set(attribute) != {"S"}
                or not isinstance(attribute["S"], str)
            ):
                raise ValueError(f"{field_name} must be a DynamoDB string (S)")
            return datetime.fromisoformat(cast(str, attribute["S"]))

        return extractor

    @staticmethod
    def extract_string_field(
        field_name: str, default: str | None = None
    ) -> DynamoDBItemExtractor:
        """Create a type-safe string field extractor with default."""

        def extractor(item: dict[str, Any]) -> str | None:
            if field_name not in item:
                return default
            attribute = item[field_name]
            if attribute == {"NULL": True}:
                return default
            if (
                not isinstance(attribute, dict)
                or set(attribute) != {"S"}
                or not isinstance(attribute["S"], str)
            ):
                raise ValueError(
                    f"{field_name} must be a DynamoDB string (S) or NULL"
                )
            value = attribute["S"]
            return value if value else default

        return extractor

    @staticmethod
    def extract_string_list_field(field_name: str) -> DynamoDBItemExtractor:
        """Create a type-safe string list field extractor."""

        def extractor(item: dict[str, Any]) -> list[str]:
            if field_name not in item:
                return []
            attribute = item[field_name]
            if attribute == {"NULL": True}:
                return []
            if not isinstance(attribute, dict) or set(attribute) != {"SS"}:
                raise ValueError(
                    f"{field_name} must be a DynamoDB string set (SS) or NULL"
                )
            values = attribute["SS"]
            if (
                not isinstance(values, list)
                or not values
                or any(
                    not isinstance(value, str) or not value for value in values
                )
                or len(values) != len(set(values))
            ):
                raise ValueError(
                    f"{field_name} must contain unique non-empty strings"
                )
            return list(cast(list[str], values))

        return extractor

    @staticmethod
    def extract_int_field(
        field_name: str, default: int | None = None
    ) -> DynamoDBItemExtractor:
        """Create a type-safe int field extractor with optional default."""

        def extractor(item: dict[str, Any]) -> int | None:
            if field_name not in item:
                return default
            attribute = item[field_name]
            if attribute == {"NULL": True}:
                return default
            if (
                not isinstance(attribute, dict)
                or set(attribute) != {"N"}
                or not isinstance(attribute["N"], str)
                or not attribute["N"]
            ):
                raise ValueError(
                    f"{field_name} must be a DynamoDB number (N) or NULL"
                )
            return int(attribute["N"])

        return extractor

    @staticmethod
    def extract_float_field(
        field_name: str, default: float | None = None
    ) -> DynamoDBItemExtractor:
        """Create a type-safe float field extractor with optional default."""

        def extractor(item: dict[str, Any]) -> float | None:
            if field_name not in item:
                return default
            attribute = item[field_name]
            if attribute == {"NULL": True}:
                return default
            if (
                not isinstance(attribute, dict)
                or set(attribute) != {"N"}
                or not isinstance(attribute["N"], str)
                or not attribute["N"]
            ):
                raise ValueError(
                    f"{field_name} must be a DynamoDB number (N) or NULL"
                )
            value = float(attribute["N"])
            if not isfinite(value):
                raise ValueError(f"{field_name} must be finite")
            return value

        return extractor


# Type-safe key parser creators
def create_image_receipt_pk_parser() -> KeyParser:
    """Create a type-safe PK parser for IMAGE# pattern."""

    def parser(pk: str) -> dict[str, Any]:
        return {
            "image_id": EntityFactory.parse_image_receipt_key(
                pk, "RECEIPT#00000"
            )["image_id"]
        }

    return parser


def create_image_receipt_sk_parser() -> KeyParser:
    """Create a type-safe SK parser for RECEIPT# pattern."""

    def parser(sk: str) -> dict[str, Any]:
        return {
            "receipt_id": EntityFactory.parse_image_receipt_key(
                "IMAGE#dummy", sk
            )["receipt_id"]
        }

    return parser


def create_receipt_line_word_sk_parser() -> KeyParser:
    """Create a type-safe SK parser for RECEIPT#/LINE#/WORD# pattern."""

    def parser(sk: str) -> dict[str, Any]:
        parsed = EntityFactory.parse_receipt_line_word_key("IMAGE#dummy", sk)
        return {
            "receipt_id": parsed["receipt_id"],
            "line_id": parsed["line_id"],
            "word_id": parsed["word_id"],
        }

    return parser


def create_receipt_barcode_sk_parser() -> KeyParser:
    """Create a type-safe SK parser for RECEIPT#/BARCODE# pattern."""

    def parser(sk: str) -> dict[str, Any]:
        parsed = EntityFactory.parse_image_receipt_key("IMAGE#dummy", sk)
        sk_parts = sk.split("#")
        if len(sk_parts) != 4 or sk_parts[2] != "BARCODE" or not sk_parts[3]:
            raise ValueError(f"Invalid receipt barcode SK format: {sk}")
        return {
            "receipt_id": parsed["receipt_id"],
            "barcode_id": int(sk_parts[3]),  # BARCODE is at position 3
        }

    return parser


def create_geometry_extractors() -> dict[str, DynamoDBItemExtractor]:
    """Create type-safe extractors for geometry fields."""

    def create_geometry_field_extractor(
        field_name: str,
    ) -> DynamoDBItemExtractor:
        def extractor(item: dict[str, Any]) -> Any:
            geometry_fields = EntityFactory.extract_geometry_fields(item)
            return geometry_fields[field_name]

        return extractor

    geometry_field_names = [
        "bounding_box",
        "top_right",
        "top_left",
        "bottom_right",
        "bottom_left",
        "angle_degrees",
        "angle_radians",
        "confidence",
    ]

    return {
        field: create_geometry_field_extractor(field)
        for field in geometry_field_names
    }


def create_ocr_job_sk_parser() -> KeyParser:
    """Create a type-safe SK parser for OCR_JOB# pattern.

    Parses SK like "OCR_JOB#{job_id}" or "ROUTING#{job_id}"
    """

    def parser(sk: str) -> dict[str, Any]:
        sk_parts = sk.split("#")
        if (
            len(sk_parts) != 2
            or sk_parts[0] not in {"OCR_JOB", "ROUTING"}
            or not sk_parts[1]
        ):
            raise ValueError(f"Invalid OCR job SK format: {sk}")
        return {"job_id": sk_parts[1]}

    return parser


def create_ocr_job_extractors() -> dict[str, DynamoDBItemExtractor]:
    """Create type-safe extractors for common OCR job fields.

    These fields are shared between OCRJob and OCRRoutingDecision:
    - s3_bucket
    - s3_key
    - created_at
    - updated_at (optional)
    - status
    """
    return {
        "s3_bucket": EntityFactory.extract_string_field("s3_bucket"),
        "s3_key": EntityFactory.extract_string_field("s3_key"),
        "created_at": EntityFactory.extract_datetime_field("created_at"),
        "updated_at": _create_optional_datetime_extractor("updated_at"),
        "status": EntityFactory.extract_string_field("status"),
    }


def _create_optional_datetime_extractor(
    field_name: str,
) -> DynamoDBItemExtractor:
    """Create extractor for optional datetime field."""

    def extractor(item: dict[str, Any]) -> datetime | None:
        if field_name not in item:
            return None
        if "S" not in item[field_name]:
            return None
        return datetime.fromisoformat(item[field_name]["S"])

    return extractor
