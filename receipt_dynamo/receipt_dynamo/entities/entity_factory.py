"""
Type-safe Entity Factory for DynamoDB item_to_* converter functions.

This version maintains full MyPy compatibility while eliminating code duplication.
"""

from datetime import datetime
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    Set,
    Type,
    TypeVar,
    Union,
    cast,
)

from .entity_mixins import SerializationMixin

T = TypeVar("T")


class DynamoDBItemExtractor(Protocol):
    """Protocol for type-safe field extractors."""

    def __call__(self, item: Dict[str, Any]) -> Any:
        """Extract a field value from a DynamoDB item."""
        ...


KeyParser = Callable[[str], Dict[str, Any]]


class EntityFactory(SerializationMixin):
    """
    Type-safe factory for creating entities from DynamoDB items.

    Provides full MyPy compatibility while eliminating code duplication.
    """

    @classmethod
    def create_entity(
        cls,
        entity_class: Type[T],
        item: Dict[str, Any],
        required_keys: Set[str],
        field_mappings: Optional[Dict[str, str]] = None,
        custom_extractors: Optional[Dict[str, DynamoDBItemExtractor]] = None,
        key_parsers: Optional[Dict[str, KeyParser]] = None,
    ) -> T:
        """
        Generic entity creation from DynamoDB item with full type safety.

        Args:
            entity_class: The entity class to instantiate
            item: DynamoDB item dictionary
            required_keys: Set of required field names
            field_mappings: Map DynamoDB field names to entity field names
            custom_extractors: Type-safe extraction functions for complex fields
            key_parsers: Type-safe functions to parse PK/SK components

        Returns:
            Instance of entity_class

        Raises:
            ValueError: If required keys are missing or parsing fails
        """
        # Validate required keys
        cls.validate_required_keys(item, required_keys)

        field_mappings = field_mappings or {}
        custom_extractors = custom_extractors or {}
        key_parsers = key_parsers or {}

        # Extract constructor arguments
        kwargs: Dict[str, Any] = {}

        # Parse key components if needed
        for key_name, parser in key_parsers.items():
            if key_name == "PK" and "PK" in item:
                parsed_data = parser(item["PK"]["S"])
                kwargs.update(parsed_data)
            elif key_name == "SK" and "SK" in item:
                parsed_data = parser(item["SK"]["S"])
                kwargs.update(parsed_data)

        # Extract fields using custom extractors first
        for field_name, extractor in custom_extractors.items():
            kwargs[field_name] = extractor(item)

        # Extract remaining required fields
        for field_name in required_keys:
            if field_name in {"PK", "SK", "TYPE"}:
                continue  # Already handled by key parsers or not a constructor param

            if field_name in custom_extractors:
                continue  # Already handled above

            # Map field name if needed
            item_key = field_mappings.get(field_name, field_name)

            # Use safe deserialization
            kwargs[field_name] = cls.safe_deserialize_field(item, item_key)

        try:
            return entity_class(**kwargs)
        except Exception as e:
            raise ValueError(
                f"Failed to create {entity_class.__name__}: {e}"
            ) from e

    @classmethod
    def parse_image_receipt_key(cls, pk: str, sk: str) -> Dict[str, Any]:
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

            # Parse SK: RECEIPT#{id:05d}#...
            if not sk.startswith("RECEIPT#"):
                raise ValueError(f"Invalid SK format: {sk}")
            sk_parts = sk.split("#")
            receipt_id = int(sk_parts[1])

            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "sk_parts": sk_parts,  # List[str]
            }
        except (IndexError, ValueError) as e:
            raise ValueError(f"Error parsing IMAGE/RECEIPT keys: {e}") from e

    @classmethod
    def parse_receipt_line_word_key(cls, pk: str, sk: str) -> Dict[str, Any]:
        """
        Parse RECEIPT#/LINE#/WORD# key pattern with type safety.

        Args:
            pk: Primary key like "IMAGE#{uuid}"
            sk: Sort key like "RECEIPT#{receipt:05d}#LINE#{line:05d}#WORD#{word:05d}"

        Returns:
            Dictionary with image_id, receipt_id, line_id, word_id
        """
        try:
            # Parse PK: IMAGE#{uuid}
            if not pk.startswith("IMAGE#"):
                raise ValueError(f"Invalid PK format: {pk}")
            image_id = pk.split("#", 1)[1]

            # Parse SK: RECEIPT#{receipt:05d}#LINE#{line:05d}#WORD#{word:05d}
            if not sk.startswith("RECEIPT#"):
                raise ValueError(f"Invalid SK format: {sk}")
            sk_parts = sk.split("#")

            if (
                len(sk_parts) < 6
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
    def extract_geometry_fields(item: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract common geometry fields with type safety.

        Args:
            item: DynamoDB item

        Returns:
            Dictionary of geometry field values
        """
        from .util import (
            deserialize_bounding_box,
            deserialize_confidence,
            deserialize_coordinate_point,
        )

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
    def extract_text_field(item: Dict[str, Any]) -> str:
        """Extract text field from DynamoDB item with type safety."""
        if "text" not in item:
            raise ValueError("Missing required field: text")
        if "S" not in item["text"]:
            raise ValueError(
                "Field 'text' must be a string type (S), got: "
                + str(item["text"])
            )
        return cast(str, item["text"]["S"])

    @staticmethod
    def extract_optional_extracted_data(
        item: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Extract optional extracted_data field with type safety."""
        if "extracted_data" not in item or item["extracted_data"].get("NULL"):
            return None

        extracted_data = item["extracted_data"]["M"]
        result: Dict[str, Any] = {}

        # Extract type if present
        if "type" in extracted_data and "S" in extracted_data["type"]:
            result["type"] = cast(str, extracted_data["type"]["S"])

        # Extract value if present
        if "value" in extracted_data and "S" in extracted_data["value"]:
            result["value"] = cast(str, extracted_data["value"]["S"])

        return result if result else None

    @staticmethod
    def extract_embedding_status(item: Dict[str, Any]) -> str:
        """Extract embedding_status with default and type safety."""
        return cast(str, item.get("embedding_status", {}).get("S", "none"))

    @staticmethod
    def extract_is_noise(item: Dict[str, Any]) -> bool:
        """Extract is_noise boolean with default False and type safety."""
        return cast(bool, item.get("is_noise", {}).get("BOOL", False))

    @staticmethod
    def extract_datetime_field(field_name: str) -> DynamoDBItemExtractor:
        """Create a type-safe datetime field extractor."""

        def extractor(item: Dict[str, Any]) -> datetime:
            return datetime.fromisoformat(cast(str, item[field_name]["S"]))

        return extractor

    @staticmethod
    def extract_string_field(
        field_name: str, default: Optional[str] = None
    ) -> DynamoDBItemExtractor:
        """Create a type-safe string field extractor with default."""

        def extractor(item: Dict[str, Any]) -> Optional[str]:
            if field_name not in item:
                return default
            value = item[field_name].get("S")
            return value if value else default

        return extractor

    @staticmethod
    def extract_string_list_field(field_name: str) -> DynamoDBItemExtractor:
        """Create a type-safe string list field extractor."""

        def extractor(item: Dict[str, Any]) -> List[str]:
            return cast(List[str], item.get(field_name, {}).get("SS", []))

        return extractor

    @staticmethod
    def extract_int_field(
        field_name: str, default: Optional[int] = None
    ) -> DynamoDBItemExtractor:
        """Create a type-safe int field extractor with optional default."""

        def extractor(item: Dict[str, Any]) -> Optional[int]:
            if field_name not in item:
                return default
            n_value = item[field_name].get("N")
            return int(n_value) if n_value is not None else default

        return extractor

    @staticmethod
    def extract_float_field(
        field_name: str, default: Optional[float] = None
    ) -> DynamoDBItemExtractor:
        """Create a type-safe float field extractor with optional default."""

        def extractor(item: Dict[str, Any]) -> Optional[float]:
            if field_name not in item:
                return default
            n_value = item[field_name].get("N")
            return float(n_value) if n_value is not None else default

        return extractor


# Type-safe key parser creators
def create_image_receipt_pk_parser() -> KeyParser:
    """Create a type-safe PK parser for IMAGE# pattern."""

    def parser(pk: str) -> Dict[str, Any]:
        return {
            "image_id": EntityFactory.parse_image_receipt_key(
                pk, "RECEIPT#00000"
            )["image_id"]
        }

    return parser


def create_image_receipt_sk_parser() -> KeyParser:
    """Create a type-safe SK parser for RECEIPT# pattern."""

    def parser(sk: str) -> Dict[str, Any]:
        return {
            "receipt_id": EntityFactory.parse_image_receipt_key(
                "IMAGE#dummy", sk
            )["receipt_id"]
        }

    return parser


def create_receipt_line_word_sk_parser() -> KeyParser:
    """Create a type-safe SK parser for RECEIPT#/LINE#/WORD# pattern."""

    def parser(sk: str) -> Dict[str, Any]:
        import logging
        logger = logging.getLogger(__name__)
        
        logger.info(f"Parsing SK: {sk}")
        
        parsed = EntityFactory.parse_image_receipt_key("IMAGE#dummy", sk)
        sk_parts = sk.split("#")
        
        logger.info(f"SK parts: {sk_parts} (length: {len(sk_parts)})")
        
        # Expected format: RECEIPT#123#LINE#456#WORD#789
        # Should have 6 parts: ['RECEIPT', '123', 'LINE', '456', 'WORD', '789']
        if len(sk_parts) < 6:
            logger.error(f"Invalid SK format: expected at least 6 parts, got {len(sk_parts)}: {sk_parts}")
            raise ValueError(f"Invalid SK format for receipt word: '{sk}'. Expected format: RECEIPT#id#LINE#id#WORD#id")
        
        try:
            line_id = int(sk_parts[3])  # LINE is at position 3
            word_id = int(sk_parts[5])  # WORD is at position 5
            
            logger.info(f"Parsed successfully: receipt_id={parsed['receipt_id']}, line_id={line_id}, word_id={word_id}")
            
            return {
                "receipt_id": parsed["receipt_id"],
                "line_id": line_id,
                "word_id": word_id,
            }
        except (ValueError, IndexError) as e:
            logger.error(f"Error parsing SK parts: {e}")
            logger.error(f"SK: {sk}, Parts: {sk_parts}")
            raise ValueError(f"Invalid SK format for receipt word: '{sk}'. Error: {e}") from e

    return parser


def create_geometry_extractors() -> Dict[str, DynamoDBItemExtractor]:
    """Create type-safe extractors for geometry fields."""

    def create_geometry_field_extractor(
        field_name: str,
    ) -> DynamoDBItemExtractor:
        def extractor(item: Dict[str, Any]) -> Any:
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
