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
- No canonical_* fields (moved to separate PlaceCluster entity)
- Includes geographic coordinates for spatial clustering
- Includes business status and operating hours
- Includes ratings and review counts as quality signals
- Stores geohash for efficient spatial queries via GSI4
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional, Tuple

from receipt_dynamo.constants import MerchantValidationStatus, ValidationMethod
from receipt_dynamo.entities.entity_mixins import SerializationMixin
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
    normalize_enum,
    validate_positive_int,
)
from receipt_dynamo.utils.geospatial import calculate_geohash

# Validation thresholds (inherit from ReceiptMetadata for consistency)
MIN_PHONE_DIGITS = 7
MIN_NAME_LENGTH = 2


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
    # Geohash is calculated but GSI4 not yet in Pulumi (deferred for PlaceCluster merchant
    # deduplication). Kept here for future spatial queries when clustering is activated.
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
    timestamp: datetime = field(default_factory=datetime.utcnow)
    places_api_version: str = "v1"

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        # Validate required fields
        assert_valid_uuid(self.image_id)
        validate_positive_int("receipt_id", self.receipt_id)

        # Normalize enum field
        if self.validated_by:
            self.validated_by = normalize_enum(self.validated_by, ValidationMethod)

        # Ensure lists are unique
        if self.merchant_types:
            self.merchant_types = list(set(self.merchant_types))
        if self.matched_fields:
            self.matched_fields = list(set(self.matched_fields))
        if self.hours_summary:
            self.hours_summary = list(set(self.hours_summary))
        if self.photo_references:
            self.photo_references = list(set(self.photo_references))

        # Validate confidence score
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {self.confidence}")

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
                    import logging
                    logging.warning(
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
        normalized_name = self.merchant_name.upper().replace(" ", "_")
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
        """Get GSI3 key (query by validation status)."""
        return {
            "GSI3PK": {"S": "PLACE_VALIDATION"},
            "GSI3SK": {"S": f"STATUS#{self.validation_status}#IMAGE#{self.image_id}"},
        }

    @property
    def gsi4_key(self) -> dict[str, dict[str, str]]:
        """
        Get GSI4 key (spatial queries via geohash).

        Note: GSI4 is deferred in Pulumi (not yet created). The key structure
        is defined here for future use when PlaceCluster merchant deduplication
        is activated and spatial queries are needed.
        """
        if not self.geohash:
            return {}
        return {
            "GSI4PK": {"S": f"GEOHASH#{self.geohash}"},
            "GSI4SK": {"S": f"PLACE#{self.place_id}"},
        }

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
