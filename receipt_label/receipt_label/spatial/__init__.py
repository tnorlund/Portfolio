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

__all__ = [
    "SpatialWord",
    "SpatialLine", 
    "ColumnDetector",
    "RowGrouper",
    "LineItemSpatialDetector",
]