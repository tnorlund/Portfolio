"""
Enhanced merchant place information for receipts.

ReceiptPlace captures comprehensive location data from Google Places API (v1)
to support:
- Advanced merchant clustering (by location, hours, status)
- Duplicate detection (nearby addresses)
- Merchant analytics (ratings, hours, photos)
- Map visualization (coordinates, viewport)

This entity replaces ReceiptMetadata with richer Place data while maintaining
backward compatibility through gradual migration.

Differences from ReceiptMetadata:
- Includes geographic coordinates for spatial clustering
- Includes business status and operating hours
- Includes ratings and review counts as quality signals
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from math import isfinite
from typing import Any

from boto3.dynamodb.types import TypeDeserializer

from receipt_dynamo.constants import ValidationMethod
from receipt_dynamo.entities.dynamodb_utils import to_dynamodb_value
from receipt_dynamo.entities.entity_mixins import SerializationMixin
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
    normalize_enum,
    validate_positive_int,
)

logger = __import__("logging").getLogger(__name__)

# Validation thresholds (reserved for future field validation logic)
# Currently ReceiptPlace relies on Google Places API validation, but
# these constants are available if additional client-side validation
# is needed in the future.
MIN_PHONE_DIGITS = 7
MIN_NAME_LENGTH = 2

# Fields that are computed (GSI keys) and should not be passed to
# constructor. Includes GSI4 and geohash for backward compatibility
# with older records that had geospatial indexing.
COMPUTED_FIELDS = {
    "PK",
    "SK",
    "GSI1PK",
    "GSI1SK",
    "GSI2PK",
    "GSI2SK",
    "GSI3PK",
    "GSI3SK",
    "GSI4PK",
    "GSI4SK",
    "TYPE",
    "geohash",
}


@dataclass(eq=True, unsafe_hash=False)
class ReceiptPlace(  # pylint: disable=too-many-instance-attributes
    SerializationMixin
):
    """
    Enhanced merchant place information for receipts.

    Captures comprehensive location data from Google Places API (v1).
    Replaces ReceiptMetadata with richer merchant and location data.

    Each ReceiptPlace record is stored in DynamoDB using the image_id
    and receipt_id, and indexed by merchant name, place_id, and
    validation status via GSIs.

    Attributes:
        image_id (str): UUID of the image the receipt belongs to.
        receipt_id (int): Identifier of the receipt within the image.
        place_id (str): Google Places API ID of the matched business.

        merchant_name (str): Business name (e.g., "Starbucks").
        merchant_category (str): Primary business type/category.
        merchant_types (list[str]): All Place types from Google.

        formatted_address (str): Full formatted address from Google.
        short_address (str): Short address (vicinity).
        address_components (dict): Structured address components.

        latitude (float): Decimal degrees latitude for the place.
        longitude (float): Decimal degrees longitude for the place.
        viewport_ne_lat (float): Viewport northeast corner latitude.
        viewport_ne_lng (float): Viewport northeast corner longitude.
        viewport_sw_lat (float): Viewport southwest corner latitude.
        viewport_sw_lng (float): Viewport southwest corner longitude.
        plus_code (str): Open Location Code (Plus Code).

        phone_number (str): Phone in national format.
        phone_intl (str): Phone in international format.
        website (str): Business website URL.
        maps_url (str): Google Maps link for the place.

        business_status (str): Status (OPERATIONAL, CLOSED_TEMPORARILY,
            CLOSED_PERMANENTLY).
        open_now (bool): Whether the place is currently open.
        hours_summary (list[str]): Human-readable hours.
        hours_data (dict): Structured hours data (periods).

        photo_references (list[str]): Photo resource names from Google.

        matched_fields (list[str]): Which fields matched.
        validated_by (str): Source of validation (INFERENCE, GPT,
            MANUAL).
        validation_status (str): Status (MATCHED, UNSURE, NO_MATCH).
        confidence (float): Match confidence score (0.0-1.0).
        reasoning (str): Why this match was made.

        timestamp (datetime): When created/updated.
        places_api_version (str): API version ("v1" or "legacy").
    """

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "TYPE",
        "image_id",
        "receipt_id",
        "place_id",
        "merchant_name",
    }

    # === Identity (Required) ===
    image_id: str
    receipt_id: int
    place_id: str

    # === Basic Info ===
    merchant_name: str
    merchant_category: str = ""
    merchant_types: list[str] = field(default_factory=list)

    # === Address ===
    formatted_address: str = ""
    short_address: str = ""
    address_components: dict[str, Any] = field(default_factory=dict)

    # === Location & Geometry ===
    latitude: float | None = None
    longitude: float | None = None
    viewport_ne_lat: float | None = None
    viewport_ne_lng: float | None = None
    viewport_sw_lat: float | None = None
    viewport_sw_lng: float | None = None
    plus_code: str = ""

    # === Contact ===
    phone_number: str = ""
    phone_intl: str = ""
    website: str = ""
    maps_url: str = ""

    # === Business Status & Hours ===
    business_status: str = ""
    open_now: bool | None = None
    hours_summary: list[str] = field(default_factory=list)
    hours_data: dict[str, Any] = field(default_factory=dict)

    # === Media ===
    photo_references: list[str] = field(default_factory=list)

    # === Validation & Metadata ===
    matched_fields: list[str] = field(default_factory=list)
    validated_by: str = ""
    validation_status: str = ""
    confidence: float = 0.0
    reasoning: str = ""

    # === Timestamps ===
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    places_api_version: str = "v1"

    def __post_init__(  # pylint: disable=too-many-branches
        self,
    ) -> None:
        """Validate and normalize initialization arguments."""
        # Validate required fields
        assert_valid_uuid(self.image_id)
        validate_positive_int("receipt_id", self.receipt_id)

        for attr_name in (
            "place_id",
            "merchant_name",
            "merchant_category",
            "formatted_address",
            "short_address",
            "plus_code",
            "phone_number",
            "phone_intl",
            "website",
            "maps_url",
            "business_status",
            "validated_by",
            "validation_status",
            "reasoning",
            "places_api_version",
        ):
            if not isinstance(getattr(self, attr_name), str):
                raise ValueError(f"{attr_name} must be a string")
        # An empty place ID is a meaningful state for unmatched records that
        # still need human review. Whitespace-only IDs are never meaningful
        # and would create a misleading GSI2 partition key.
        if self.place_id and not self.place_id.strip():
            raise ValueError("place_id cannot contain only whitespace")

        # Normalize enum field
        if self.validated_by:
            self.validated_by = normalize_enum(
                self.validated_by, ValidationMethod
            )

        # Ensure collection fields have stable, detached values.
        for attr_name in (
            "merchant_types",
            "matched_fields",
            "hours_summary",
            "photo_references",
        ):
            values = getattr(self, attr_name)
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value.strip()
                for value in values
            ):
                raise ValueError(f"{attr_name} must be a list of strings")
            setattr(self, attr_name, list(dict.fromkeys(values)))

        for attr_name in ("address_components", "hours_data"):
            value = getattr(self, attr_name)
            if not isinstance(value, dict):
                raise ValueError(f"{attr_name} must be a dictionary")

        if self.open_now is not None and not isinstance(self.open_now, bool):
            raise ValueError("open_now must be a boolean or None")
        if not isinstance(self.timestamp, datetime):
            raise ValueError("timestamp must be a datetime")

        # Validate confidence score
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float, Decimal))
            or not isfinite(float(self.confidence))
            or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError(
                f"confidence must be between 0.0 and 1.0, "
                f"got {self.confidence}"
            )
        self.confidence = float(self.confidence)

        # Validate merchant_name produces non-empty GSI1 key after
        # normalization
        if not self.merchant_name.strip():
            raise ValueError("merchant_name cannot be empty")
        normalized_merchant = self.merchant_name.upper()
        normalized_merchant = re.sub(r"[^A-Z0-9]+", "_", normalized_merchant)
        normalized_merchant = normalized_merchant.strip("_")
        if not normalized_merchant:
            raise ValueError(
                f"merchant_name '{self.merchant_name}' contains no "
                "alphanumeric characters and would produce an invalid "
                "GSI1 key"
            )

        # Validate coordinates if present
        for attr, minimum, maximum in (
            ("latitude", -90.0, 90.0),
            ("longitude", -180.0, 180.0),
            ("viewport_ne_lat", -90.0, 90.0),
            ("viewport_sw_lat", -90.0, 90.0),
            ("viewport_ne_lng", -180.0, 180.0),
            ("viewport_sw_lng", -180.0, 180.0),
        ):
            val = getattr(self, attr)
            if val is None:
                continue
            if (
                isinstance(val, bool)
                or not isinstance(val, (int, float, Decimal))
                or not isfinite(float(val))
                or not minimum <= val <= maximum
            ):
                raise ValueError(f"{attr} out of range: {val}")
            setattr(self, attr, float(val))

    @property
    def key(self) -> dict[str, dict[str, str]]:
        """Get DynamoDB key for this entity."""
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"RECEIPT#{self.receipt_id:05d}#PLACE"},
        }

    @property
    def gsi1_key(self) -> dict[str, dict[str, str]]:
        """Get GSI1 key (query by merchant name)."""
        # Normalize name: uppercase, replace special chars with
        # underscore, collapse whitespace
        normalized_name = self.merchant_name.upper()
        normalized_name = re.sub(r"[^A-Z0-9]+", "_", normalized_name)
        normalized_name = normalized_name.strip("_")
        sk = f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}#PLACE"
        return {
            "GSI1PK": {"S": f"MERCHANT#{normalized_name}"},
            "GSI1SK": {"S": sk},
        }

    @property
    def gsi2_key(self) -> dict[str, dict[str, str]]:
        """Get the sparse GSI2 key used to query by Google place ID."""
        if not self.place_id:
            return {}
        sk = f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}#PLACE"
        return {
            "GSI2PK": {"S": f"PLACE#{self.place_id}"},
            "GSI2SK": {"S": sk},
        }

    @property
    def gsi3_key(self) -> dict[str, dict[str, str]]:
        """Get GSI3 key (query by confidence and validation status)."""
        # Format confidence to 4 decimal places for consistent sorting
        confidence_str = f"{self.confidence:.4f}"
        sk = (
            f"CONFIDENCE#{confidence_str}#STATUS#{self.validation_status}"
            f"#IMAGE#{self.image_id}"
        )
        return {
            "GSI3PK": {"S": "PLACE_VALIDATION"},
            "GSI3SK": {"S": sk},
        }

    @property
    def gsi4_key(self) -> dict[str, dict[str, str]]:
        """Get GSI4 key for receipt details access pattern.

        GSI4 enables efficient single-query retrieval of all receipt-related
        entities (Receipt, Lines, Words, Labels, Place) excluding Letters.
        """
        return {
            "GSI4PK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}"
            },
            "GSI4SK": {"S": "1_PLACE"},
        }

    def to_item(  # pylint: disable=too-many-branches
        self,
    ) -> dict[str, Any]:
        """
        Serialize the ReceiptPlace object into DynamoDB item format.

        Includes primary keys, GSI keys, and all location/merchant
        data with appropriate DynamoDB type annotations (S for string,
        N for number, etc).

        Returns:
            Dict with DynamoDB formatted item ready for PutItem/UpdateItem
        """
        self.__post_init__()
        item: dict[str, Any] = {
            **self.key,
            **self.gsi1_key,
            **self.gsi2_key,
            **self.gsi3_key,
            **self.gsi4_key,
            "TYPE": {"S": "RECEIPT_PLACE"},
            # Required identity fields
            "image_id": {"S": self.image_id},
            "receipt_id": {"N": str(self.receipt_id)},
            "place_id": {"S": self.place_id},
            "merchant_name": {"S": self.merchant_name},
            "validation_status": {"S": self.validation_status},
            "timestamp": {"S": self.timestamp.isoformat()},
            "confidence": {"N": str(self.confidence)},
        }

        # Optional string fields - include if non-empty, else NULL
        for attr in (
            "merchant_category",
            "formatted_address",
            "short_address",
            "phone_number",
            "phone_intl",
            "website",
            "maps_url",
            "business_status",
            "plus_code",
            "validated_by",
            "reasoning",
            "places_api_version",
        ):
            value = getattr(self, attr, "")
            if isinstance(value, str):
                if value:
                    item[attr] = {"S": value}
                else:
                    item[attr] = {"NULL": True}

        # Lists remain lists so round trips preserve semantically meaningful
        # ordering (especially weekday hours and photo preference order).
        for attr_name in (
            "merchant_types",
            "matched_fields",
            "hours_summary",
            "photo_references",
        ):
            value = getattr(self, attr_name)
            if value:
                item[attr_name] = to_dynamodb_value(value)

        # Numeric fields (coordinates)
        if self.latitude is not None:
            item["latitude"] = {"N": str(self.latitude)}
        if self.longitude is not None:
            item["longitude"] = {"N": str(self.longitude)}
        if self.viewport_ne_lat is not None:
            item["viewport_ne_lat"] = {"N": str(self.viewport_ne_lat)}
        if self.viewport_ne_lng is not None:
            item["viewport_ne_lng"] = {"N": str(self.viewport_ne_lng)}
        if self.viewport_sw_lat is not None:
            item["viewport_sw_lat"] = {"N": str(self.viewport_sw_lat)}
        if self.viewport_sw_lng is not None:
            item["viewport_sw_lng"] = {"N": str(self.viewport_sw_lng)}

        # Boolean field
        if self.open_now is not None:
            item["open_now"] = {"BOOL": self.open_now}

        # Complex fields use native DynamoDB maps/lists. from_item continues
        # accepting legacy JSON strings for records written by older clients.
        if self.address_components:
            item["address_components"] = to_dynamodb_value(
                self.address_components
            )
        if self.hours_data:
            item["hours_data"] = to_dynamodb_value(self.hours_data)

        return item

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"ReceiptPlace("
            f"image_id={_repr_str(self.image_id)}, "
            f"receipt_id={self.receipt_id}, "
            f"place_id={_repr_str(self.place_id)}, "
            f"merchant_name={_repr_str(self.merchant_name)}, "
            f"validation_status={self.validation_status}"
            f")"
        )

    @classmethod
    def from_item(  # pylint: disable=too-many-branches
        cls, item: dict[str, Any]
    ) -> "ReceiptPlace":
        """Converts a DynamoDB item to a ReceiptPlace object.

        Args:
            item: The DynamoDB item to convert.

        Returns:
            ReceiptPlace: The ReceiptPlace object.

        Raises:
            ValueError: When the item format is invalid.
        """
        cls.validate_required_keys(item, cls.REQUIRED_KEYS)
        if item["TYPE"] != {"S": "RECEIPT_PLACE"}:
            raise ValueError("TYPE must be RECEIPT_PLACE")

        # First, deserialize from DynamoDB JSON format to Python types
        deserializer = TypeDeserializer()
        try:
            deserialized = {
                k: deserializer.deserialize(v) for k, v in item.items()
            }
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid ReceiptPlace DynamoDB item") from exc

        # Filter out computed fields
        filtered_item = {
            k: v for k, v in deserialized.items() if k not in COMPUTED_FIELDS
        }

        # Parse complex fields that legacy records JSON-serialized.
        if "address_components" in filtered_item and isinstance(
            filtered_item["address_components"], str
        ):
            try:
                filtered_item["address_components"] = json.loads(
                    filtered_item["address_components"]
                )
            except (json.JSONDecodeError, TypeError):
                raise ValueError("Invalid address_components JSON") from None

        if "hours_data" in filtered_item and isinstance(
            filtered_item["hours_data"], str
        ):
            try:
                filtered_item["hours_data"] = json.loads(
                    filtered_item["hours_data"]
                )
            except (json.JSONDecodeError, TypeError):
                raise ValueError("Invalid hours_data JSON") from None

        # Parse timestamp if it's a string
        if "timestamp" in filtered_item and isinstance(
            filtered_item["timestamp"], str
        ):
            filtered_item["timestamp"] = datetime.fromisoformat(
                filtered_item["timestamp"]
            )

        # Convert receipt_id: boto3 deserializes numbers as Decimal, need int
        if "receipt_id" in filtered_item:
            if isinstance(filtered_item["receipt_id"], Decimal):
                filtered_item["receipt_id"] = int(filtered_item["receipt_id"])
            elif isinstance(filtered_item["receipt_id"], str):
                try:
                    filtered_item["receipt_id"] = int(
                        filtered_item["receipt_id"]
                    )
                except (ValueError, TypeError):
                    pass

        # Handle None values for string fields that should be empty strings
        for attr_name in [
            "merchant_category",
            "formatted_address",
            "short_address",
            "phone_number",
            "phone_intl",
            "website",
            "maps_url",
            "business_status",
            "plus_code",
            "validated_by",
            "reasoning",
            "places_api_version",
        ]:
            if attr_name in filtered_item and filtered_item[attr_name] is None:
                filtered_item[attr_name] = ""

        # Convert Decimal numbers to float for floating point fields
        for attr_name in [
            "latitude",
            "longitude",
            "confidence",
            "viewport_ne_lat",
            "viewport_ne_lng",
            "viewport_sw_lat",
            "viewport_sw_lng",
        ]:
            if attr_name in filtered_item:
                value = filtered_item[attr_name]
                if isinstance(value, (str, int, Decimal)):
                    try:
                        filtered_item[attr_name] = float(value)
                    except (ValueError, TypeError):
                        filtered_item.pop(attr_name, None)

        for attr_name in (
            "merchant_types",
            "matched_fields",
            "hours_summary",
            "photo_references",
        ):
            if isinstance(filtered_item.get(attr_name), set):
                filtered_item[attr_name] = sorted(filtered_item[attr_name])

        try:
            result = cls(**filtered_item)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid ReceiptPlace item: {exc}") from exc

        expected_keys = {
            **result.key,
            **result.gsi1_key,
            **result.gsi2_key,
            **result.gsi3_key,
            **result.gsi4_key,
        }
        for key_name, expected_value in expected_keys.items():
            if key_name in item and item[key_name] != expected_value:
                raise ValueError(f"{key_name} does not match entity keys")

        # Older unmatched records wrote PLACE# to GSI2. Continue accepting
        # that exact legacy shape while new writes omit GSI2 entirely so the
        # index remains sparse.
        if not result.place_id and ("GSI2PK" in item or "GSI2SK" in item):
            legacy_gsi2 = {
                "GSI2PK": {"S": "PLACE#"},
                "GSI2SK": {
                    "S": (
                        f"IMAGE#{result.image_id}#"
                        f"RECEIPT#{result.receipt_id:05d}#PLACE"
                    )
                },
            }
            for key_name, expected_value in legacy_gsi2.items():
                if item.get(key_name) != expected_value:
                    raise ValueError(f"{key_name} does not match entity keys")
        return result


def item_to_receipt_place(item: dict[str, Any]) -> ReceiptPlace:
    """Converts a DynamoDB item to a ReceiptPlace object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptPlace: The ReceiptPlace object.

    Raises:
        ValueError: When the item format is invalid.
    """
    return ReceiptPlace.from_item(item)
