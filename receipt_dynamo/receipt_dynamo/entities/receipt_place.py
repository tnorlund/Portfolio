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
- Stores geohash for efficient spatial queries via GSI4
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from receipt_dynamo.constants import MerchantValidationStatus, ValidationMethod
from receipt_dynamo.entities.entity_mixins import SerializationMixin
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
    normalize_enum,
    validate_positive_int,
)
from receipt_dynamo.utils.geospatial import calculate_geohash

logger = __import__("logging").getLogger(__name__)

# Validation thresholds (reserved for future field validation logic)
# Currently ReceiptPlace relies on Google Places API validation, but these constants
# are available if additional client-side validation is needed in the future
MIN_PHONE_DIGITS = 7
MIN_NAME_LENGTH = 2

# Fields that are computed (GSI keys) and should not be passed to constructor
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
    "TYPE",  # Entity type discriminator
}

# Valid fields for ReceiptPlace dataclass (used for filtering unknown fields)
VALID_RECEIPT_PLACE_FIELDS = {
    "image_id",
    "receipt_id",
    "place_id",
    "merchant_name",
    "merchant_category",
    "merchant_types",
    "formatted_address",
    "short_address",
    "address_components",
    "latitude",
    "longitude",
    "geohash",
    "viewport_ne_lat",
    "viewport_ne_lng",
    "viewport_sw_lat",
    "viewport_sw_lng",
    "plus_code",
    "phone_number",
    "phone_intl",
    "website",
    "maps_url",
    "business_status",
    "open_now",
    "hours_summary",
    "hours_data",
    "photo_references",
    "matched_fields",
    "validated_by",
    "validation_status",
    "confidence",
    "reasoning",
    "timestamp",
    "places_api_version",
}


@dataclass(eq=True, unsafe_hash=False)
class ReceiptPlace(SerializationMixin):
    """
    Enhanced merchant place information for receipts.

    Captures comprehensive location data from Google Places API (v1).
    Replaces ReceiptMetadata with richer merchant and location data.

    Each ReceiptPlace record is stored in DynamoDB using the image_id and
    receipt_id, and indexed by merchant name, place_id, and geohash via GSIs.

    Attributes:
        image_id (str): UUID of the image the receipt belongs to.
        receipt_id (int): Identifier of the receipt within the image.
        place_id (str): Google Places API ID of the matched business (ChIJ...).

        merchant_name (str): Business name (e.g., "Starbucks").
        merchant_category (str): Primary business type/category.
        merchant_types (list[str]): All Place types from Google.

        formatted_address (str): Full formatted address from Google.
        short_address (str): Short address (vicinity).
        address_components (dict): Structured address components.

        latitude (float): Decimal degrees latitude for the place.
        longitude (float): Decimal degrees longitude for the place.
        geohash (str): Geohash for spatial indexing (GSI4).
        viewport_ne_lat (float): Viewport northeast corner latitude.
        viewport_ne_lng (float): Viewport northeast corner longitude.
        viewport_sw_lat (float): Viewport southwest corner latitude.
        viewport_sw_lng (float): Viewport southwest corner longitude.
        plus_code (str): Open Location Code (Plus Code).

        phone_number (str): Phone in national format (e.g., "(555) 123-4567").
        phone_intl (str): Phone in international format (e.g., "+1 555 123-4567").
        website (str): Business website URL.
        maps_url (str): Google Maps link for the place.

        business_status (str): Status (OPERATIONAL, CLOSED_TEMPORARILY, CLOSED_PERMANENTLY).
        open_now (bool): Whether the place is currently open.
        hours_summary (list[str]): Human-readable hours (e.g., "Mon: 9 AM - 5 PM").
        hours_data (dict): Structured hours data (periods).

        photo_references (list[str]): Photo resource names from Google.

        matched_fields (list[str]): Which fields matched (e.g., ["name", "phone"]).
        validated_by (str): Source of validation (INFERENCE, GPT, MANUAL).
        validation_status (str): Status (MATCHED, UNSURE, NO_MATCH).
        confidence (float): Match confidence score (0.0-1.0).
        reasoning (str): Why this match was made.

        timestamp (datetime): When created/updated.
        places_api_version (str): Which API version was used ("v1" or "legacy").
    """

    # === Identity (Required) ===
    image_id: str
    receipt_id: int
    place_id: str

    # === Basic Info ===
    merchant_name: str
    merchant_category: str = ""
    merchant_types: List[str] = field(default_factory=list)

    # === Address ===
    formatted_address: str = ""
    short_address: str = ""
    address_components: Dict[str, Any] = field(default_factory=dict)

    # === Location & Geometry ===
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    # Geohash calculated for spatial indexing (precision 6-7 ~1km)
    geohash: str = ""  # Precision 6-7 (~1km)
    viewport_ne_lat: Optional[float] = None
    viewport_ne_lng: Optional[float] = None
    viewport_sw_lat: Optional[float] = None
    viewport_sw_lng: Optional[float] = None
    plus_code: str = ""

    # === Contact ===
    phone_number: str = ""
    phone_intl: str = ""
    website: str = ""
    maps_url: str = ""

    # === Business Status & Hours ===
    business_status: str = ""
    open_now: Optional[bool] = None
    hours_summary: List[str] = field(default_factory=list)
    hours_data: Dict[str, Any] = field(default_factory=dict)

    # === Media ===
    photo_references: List[str] = field(default_factory=list)

    # === Validation & Metadata ===
    matched_fields: List[str] = field(default_factory=list)
    validated_by: str = ""
    validation_status: str = ""
    confidence: float = 0.0
    reasoning: str = ""

    # === Timestamps ===
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    places_api_version: str = "v1"

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        # Validate required fields
        assert_valid_uuid(self.image_id)
        validate_positive_int("receipt_id", self.receipt_id)

        # Normalize enum field
        if self.validated_by:
            self.validated_by = normalize_enum(
                self.validated_by, ValidationMethod
            )

        # Ensure lists are unique (preserve order)
        if self.merchant_types:
            self.merchant_types = list(dict.fromkeys(self.merchant_types))
        if self.matched_fields:
            self.matched_fields = list(dict.fromkeys(self.matched_fields))
        if self.hours_summary:
            self.hours_summary = list(dict.fromkeys(self.hours_summary))
        if self.photo_references:
            self.photo_references = list(dict.fromkeys(self.photo_references))

        # Validate confidence score
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be between 0.0 and 1.0, got {self.confidence}"
            )

        # Validate merchant_name produces non-empty GSI1 key after normalization
        if not self.merchant_name:
            raise ValueError("merchant_name cannot be empty")
        normalized_merchant = self.merchant_name.upper()
        normalized_merchant = re.sub(r"[^A-Z0-9]+", "_", normalized_merchant)
        normalized_merchant = normalized_merchant.strip("_")
        if not normalized_merchant:
            raise ValueError(
                f"merchant_name '{self.merchant_name}' contains no alphanumeric "
                "characters and would produce an invalid GSI1 key"
            )

        # Validate coordinates if present
        if self.latitude is not None:
            if not (-90.0 <= self.latitude <= 90.0):
                raise ValueError(f"latitude out of range: {self.latitude}")
        if self.longitude is not None:
            if not (-180.0 <= self.longitude <= 180.0):
                raise ValueError(f"longitude out of range: {self.longitude}")

        # Validate viewport coordinates if present
        for attr in ["viewport_ne_lat", "viewport_sw_lat"]:
            val = getattr(self, attr, None)
            if val is not None and not (-90.0 <= val <= 90.0):
                raise ValueError(f"{attr} out of range: {val}")

        for attr in ["viewport_ne_lng", "viewport_sw_lng"]:
            val = getattr(self, attr, None)
            if val is not None and not (-180.0 <= val <= 180.0):
                raise ValueError(f"{attr} out of range: {val}")

        # Auto-calculate geohash from coordinates if not provided
        if self.latitude is not None and self.longitude is not None:
            if not self.geohash:
                try:
                    self.geohash = calculate_geohash(
                        self.latitude, self.longitude, precision=6
                    )
                except ValueError as e:
                    # Only warn, don't fail - geohash is optional
                    logger.warning(
                        f"Failed to calculate geohash for {self.place_id}: {e}"
                    )

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
        # Normalize name: uppercase, replace special chars with underscore, collapse whitespace
        normalized_name = self.merchant_name.upper()
        normalized_name = re.sub(r"[^A-Z0-9]+", "_", normalized_name)
        normalized_name = normalized_name.strip("_")
        return {
            "GSI1PK": {"S": f"MERCHANT#{normalized_name}"},
            "GSI1SK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}#PLACE"
            },
        }

    @property
    def gsi2_key(self) -> dict[str, dict[str, str]]:
        """Get GSI2 key (query by place_id)."""
        return {
            "GSI2PK": {"S": f"PLACE#{self.place_id}"},
            "GSI2SK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}#PLACE"
            },
        }

    @property
    def gsi3_key(self) -> dict[str, dict[str, str]]:
        """Get GSI3 key (query by confidence and validation status)."""
        # Format confidence to 4 decimal places for consistent sorting
        confidence_str = f"{self.confidence:.4f}"
        return {
            "GSI3PK": {"S": "PLACE_VALIDATION"},
            "GSI3SK": {
                "S": f"CONFIDENCE#{confidence_str}#STATUS#{self.validation_status}#IMAGE#{self.image_id}"
            },
        }

    @property
    def gsi4_key(self) -> dict[str, dict[str, str]]:
        """
        Get GSI4 key (spatial queries via geohash).

        Note: GSI4 key structure for spatial queries via geohash.
        """
        if not self.geohash:
            return {}
        return {
            "GSI4PK": {"S": f"GEOHASH#{self.geohash}"},
            "GSI4SK": {"S": f"PLACE#{self.place_id}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """
        Serialize the ReceiptPlace object into DynamoDB item format.

        Includes primary keys, GSI keys, and all location/merchant data with
        appropriate DynamoDB type annotations (S for string, N for number, etc).

        Returns:
            Dict with DynamoDB formatted item ready for PutItem/UpdateItem
        """
        item: Dict[str, Any] = {
            **self.key,
            **self.gsi1_key,
            **self.gsi2_key,
            **self.gsi3_key,
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

        # Add GSI4 keys if geohash exists
        if self.geohash:
            item.update(self.gsi4_key)

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
            "geohash",
        ):
            value = getattr(self, attr, "")
            if isinstance(value, str):
                if value:
                    item[attr] = {"S": value}
                else:
                    item[attr] = {"NULL": True}

        # List fields (string sets) - filter out empty strings (DynamoDB SS rejects empty strings)
        merchant_types_filtered = [
            s.strip()
            for s in self.merchant_types
            if isinstance(s, str) and s.strip()
        ]
        if merchant_types_filtered:
            item["merchant_types"] = {"SS": merchant_types_filtered}

        matched_fields_filtered = [
            s.strip()
            for s in self.matched_fields
            if isinstance(s, str) and s.strip()
        ]
        if matched_fields_filtered:
            item["matched_fields"] = {"SS": matched_fields_filtered}

        hours_summary_filtered = [
            s.strip()
            for s in self.hours_summary
            if isinstance(s, str) and s.strip()
        ]
        if hours_summary_filtered:
            item["hours_summary"] = {"SS": hours_summary_filtered}

        photo_references_filtered = [
            s.strip()
            for s in self.photo_references
            if isinstance(s, str) and s.strip()
        ]
        if photo_references_filtered:
            item["photo_references"] = {"SS": photo_references_filtered}

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

        # Complex fields (JSON/Maps)
        if self.address_components:
            import json

            item["address_components"] = {
                "S": json.dumps(self.address_components)
            }
        if self.hours_data:
            import json

            item["hours_data"] = {"S": json.dumps(self.hours_data)}

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


def item_to_receipt_place(item: Dict[str, Any]) -> ReceiptPlace:
    """
    Create ReceiptPlace from DynamoDB item (low-level client format with type annotations).

    Handles DynamoDB JSON format items like {'S': 'value'}, {'N': '123'}, etc.

    Args:
        item: Dict from DynamoDB in low-level client format

    Returns:
        ReceiptPlace instance
    """
    import json
    from decimal import Decimal

    from boto3.dynamodb.types import TypeDeserializer

    # First, deserialize from DynamoDB JSON format to Python types
    deserializer = TypeDeserializer()
    deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}

    # Extract image_id and receipt_id from PK/SK if not present as fields
    # PK format: IMAGE#{image_id}, SK format: RECEIPT#{receipt_id:05d}#PLACE
    if "image_id" not in deserialized and "PK" in deserialized:
        pk = deserialized["PK"]
        if pk.startswith("IMAGE#"):
            deserialized["image_id"] = pk[6:]  # Remove "IMAGE#" prefix

    if "receipt_id" not in deserialized and "SK" in deserialized:
        sk = deserialized["SK"]
        if sk.startswith("RECEIPT#"):
            # Extract receipt_id from "RECEIPT#00001#PLACE" or similar
            parts = sk.split("#")
            if len(parts) >= 2:
                try:
                    deserialized["receipt_id"] = int(parts[1])
                except (ValueError, TypeError):
                    pass

    # Filter out computed fields
    filtered_item = {
        k: v for k, v in deserialized.items() if k not in COMPUTED_FIELDS
    }

    # Parse complex fields that were JSON-serialized
    if "address_components" in filtered_item and isinstance(
        filtered_item["address_components"], str
    ):
        try:
            filtered_item["address_components"] = json.loads(
                filtered_item["address_components"]
            )
        except (json.JSONDecodeError, TypeError):
            filtered_item["address_components"] = {}

    if "hours_data" in filtered_item and isinstance(
        filtered_item["hours_data"], str
    ):
        try:
            filtered_item["hours_data"] = json.loads(
                filtered_item["hours_data"]
            )
        except (json.JSONDecodeError, TypeError):
            filtered_item["hours_data"] = {}

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
                filtered_item["receipt_id"] = int(filtered_item["receipt_id"])
            except (ValueError, TypeError):
                pass

    # Handle None values for string fields that should be empty strings
    for attr_name in [
        "geohash",
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

    # Filter to only valid ReceiptPlace fields (handles legacy records)
    valid_item = {
        k: v
        for k, v in filtered_item.items()
        if k in VALID_RECEIPT_PLACE_FIELDS
    }

    return ReceiptPlace(**valid_item)
