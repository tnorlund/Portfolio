"""
Receipt Places - Google Places API client with DynamoDB caching.

This package provides a Google Places API client that automatically
caches responses in DynamoDB to minimize API costs.
"""

from receipt_places.client import PlacesClient
from receipt_places.cache import CacheManager
from receipt_places.config import PlacesConfig

__version__ = "0.1.0"

__all__ = [
    "PlacesClient",
    "CacheManager",
    "PlacesConfig",
]

