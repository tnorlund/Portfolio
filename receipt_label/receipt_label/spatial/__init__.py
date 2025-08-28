"""
Spatial analysis utilities for receipt processing.

This module provides comprehensive spatial analysis tools for receipt word labels,
including geometric utilities, relationship analysis, filtering strategies, and
batch processing capabilities. Built following the receipt_label package's
pattern-first design philosophy.
"""

# Geometric utilities for line item detection
from .geometry_utils import (
    SpatialWord,
    SpatialLine,
    ColumnDetector,
    RowGrouper,
    LineItemSpatialDetector,
)

# Spatial relationship analysis
from .relationship_analyzer import (
    AnalysisConfig,
    SpatialRelationshipAnalyzer,
)

# Filtering strategies for optimization
from .filtering_strategies import (
    FilteringStrategy,
    DistanceThresholdStrategy,
    TopKClosestStrategy,
    PercentileStrategy,
    HybridStrategy,
    LabelTypeAwareStrategy,
    FilteringStrategyFactory,
    analyze_filtering_performance,
)

# High-level analysis engine
from .analysis_engine import (
    SpatialAnalysisResult,
    SpatialAnalysisEngine,
    create_production_engine,
    create_high_precision_engine,
    create_fast_processing_engine,
)

# Batch processing utilities
from .batch_processor import (
    BatchProcessingConfig,
    BatchResult,
    SpatialAnalysisBatchProcessor,
    create_production_batch_processor,
    create_fast_batch_processor,
    create_memory_efficient_processor,
)

__all__ = [
    # Geometric utilities
    "SpatialWord",
    "SpatialLine", 
    "ColumnDetector",
    "RowGrouper",
    "LineItemSpatialDetector",
    
    # Relationship analysis
    "AnalysisConfig",
    "SpatialRelationshipAnalyzer",
    
    # Filtering strategies
    "FilteringStrategy",
    "DistanceThresholdStrategy",
    "TopKClosestStrategy", 
    "PercentileStrategy",
    "HybridStrategy",
    "LabelTypeAwareStrategy",
    "FilteringStrategyFactory",
    "analyze_filtering_performance",
    
    # Analysis engine
    "SpatialAnalysisResult",
    "SpatialAnalysisEngine",
    "create_production_engine",
    "create_high_precision_engine", 
    "create_fast_processing_engine",
    
    # Batch processing
    "BatchProcessingConfig",
    "BatchResult",
    "SpatialAnalysisBatchProcessor",
    "create_production_batch_processor",
    "create_fast_batch_processor",
    "create_memory_efficient_processor",
]
