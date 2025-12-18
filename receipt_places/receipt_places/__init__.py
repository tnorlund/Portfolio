"""
Receipt Places - Google Places API client with DynamoDB caching.

This package provides a Google Places API client that automatically
caches responses in DynamoDB to minimize API costs. All results are
typed via Pydantic models with data quality validation.

Supports both:
- Legacy Places API (default, backward compatible)
- New Places API v1 (set RECEIPT_PLACES_USE_V1_API=true to enable)

Example:
    ```python
    from receipt_places import create_places_client

    # Uses legacy API by default
    client = create_places_client()

    # Search by phone
    place = client.search_by_phone("555-123-4567")
    ```
"""

from receipt_places.cache import CacheManager
from receipt_places.client import PlacesClient, create_places_client
from receipt_places.config import PlacesConfig
from receipt_places.parsers import APIError, ParseError
from receipt_places.types import (
    Candidate,
    Geometry,
    LatLng,
    OpeningHours,
    OpeningHoursPeriod,
    Photo,
    Place,
    PlusCode,
    Prediction,
    Viewport,
)
from receipt_places.validators import (
    DataQualityError,
    MissingExpectedFieldsError,
    PlacesValidationError,
    validate_has_identifier,
    validate_place_complete,
    validate_place_expected_fields,
    validate_place_sanity,
)

__version__ = "0.1.0"

__all__ = [
    # Core client & config (use create_places_client() for flexibility)
    "create_places_client",
    "PlacesClient",
    "CacheManager",
    "PlacesConfig",
    # Typed models
    "Place",
    "LatLng",
    "Geometry",
    "Viewport",
    "Photo",
    "PlusCode",
    "OpeningHours",
    "OpeningHoursPeriod",
    "Candidate",
    "Prediction",
    # Parsers & errors
    "APIError",
    "ParseError",
    # Validators & errors
    "PlacesValidationError",
    "MissingExpectedFieldsError",
    "DataQualityError",
    "validate_place_complete",
    "validate_place_expected_fields",
    "validate_place_sanity",
    "validate_has_identifier",
]
