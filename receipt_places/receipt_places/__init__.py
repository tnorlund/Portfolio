"""
Receipt Places - Google Places API client with DynamoDB caching.

This package provides a Google Places API client that automatically
caches responses in DynamoDB to minimize API costs. All results are
typed via Pydantic models with data quality validation.
"""

from receipt_places.cache import CacheManager
from receipt_places.client import PlacesClient
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
    # Core client & config
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
