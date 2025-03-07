"""
Receipt Labeling Package

This package provides functionality for labeling receipt data using various APIs
and validation strategies.
"""

from receipt_label.data.places_api import (
    BatchPlacesProcessor,
    ConfidenceLevel,
    PlacesAPI,
    ValidationResult,
)

__all__ = [
    "BatchPlacesProcessor",
    "ConfidenceLevel",
    "PlacesAPI",
    "ValidationResult",
] 