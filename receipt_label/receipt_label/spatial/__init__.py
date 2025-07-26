"""
Spatial analysis utilities for receipt processing.

This module provides geometric and spatial analysis tools for pattern-first
line item detection, extending the proven 4-field approach with spatial intelligence.
"""

from .geometry_utils import (
    SpatialWord,
    SpatialLine,
    ColumnDetector,
    RowGrouper,
    LineItemSpatialDetector,
)
from .negative_space_detector import (
    NegativeSpaceDetector,
    WhitespaceRegion,
    LineItemBoundary,
    ColumnStructure,
)

__all__ = [
    "SpatialWord",
    "SpatialLine",
    "ColumnDetector",
    "RowGrouper",
    "LineItemSpatialDetector",
    "NegativeSpaceDetector",
    "WhitespaceRegion",
    "LineItemBoundary",
    "ColumnStructure",
]
