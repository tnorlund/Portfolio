"""
High-level spatial analysis engine for receipt processing.

This module provides a comprehensive spatial analysis engine that integrates
all spatial analysis components following the pattern-first design philosophy
of the receipt_label package.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_dynamo.entities.receipt_word_label_spatial_analysis import (
    ReceiptWordLabelSpatialAnalysis,
)

from .filtering_strategies import FilteringStrategy, FilteringStrategyFactory
from .relationship_analyzer import AnalysisConfig, SpatialRelationshipAnalyzer

logger = logging.getLogger(__name__)


@dataclass
class SpatialAnalysisResult:
    """Result of spatial analysis for a receipt."""
    
    receipt_id: int
    image_id: str
    analyses: List[ReceiptWordLabelSpatialAnalysis]
    statistics: Dict[str, float]
    processing_time_ms: float
    filtering_strategy_used: str
    
    @property
    def analysis_count(self) -> int:
        """Number of spatial analyses created."""
        return len(self.analyses)
    
    @property
    def total_relationships(self) -> int:
        """Total number of spatial relationships across all analyses."""
        return sum(len(analysis.spatial_relationships) for analysis in self.analyses)
    
    @property
    def average_relationships_per_analysis(self) -> float:
        """Average number of relationships per analysis."""
        if self.analysis_count == 0:
            return 0.0
        return self.total_relationships / self.analysis_count


class SpatialAnalysisEngine:
    """
    High-level engine for spatial analysis of receipt word labels.
    
    This engine orchestrates the complete spatial analysis pipeline:
    1. Validates input data
    2. Applies filtering strategies
    3. Computes spatial relationships
    4. Creates analysis entities
    5. Provides comprehensive metrics
    
    Designed to follow the receipt_label package's pattern-first approach
    by using empirically-tested filtering strategies before expensive operations.
    """
    
    def __init__(
        self, 
        filtering_strategy: Optional[FilteringStrategy] = None,
        analysis_config: Optional[AnalysisConfig] = None,
    ):
        """Initialize the spatial analysis engine.
        
        Args:
            filtering_strategy: Strategy for filtering spatial relationships.
                               Defaults to the recommended hybrid strategy.
            analysis_config: Configuration for spatial analysis behavior.
                           Defaults to production-optimized settings.
        """
        self.filtering_strategy = (
            filtering_strategy or FilteringStrategyFactory.get_recommended_strategy()
        )
        self.analysis_config = analysis_config or AnalysisConfig()
        
        self.analyzer = SpatialRelationshipAnalyzer(self.analysis_config)
        
        self._engine_stats = {
            "total_receipts_processed": 0,
            "total_analyses_created": 0,
            "total_relationships_created": 0,
            "avg_processing_time_ms": 0.0,
        }
        
        logger.info(
            f"Initialized SpatialAnalysisEngine with strategy: "
            f"{self.filtering_strategy.get_strategy_name()}"
        )
    
    def analyze_receipt(
        self,
        words: List[ReceiptWord],
        labels: List[ReceiptWordLabel],
        image_id: str,
        receipt_id: int,
    ) -> SpatialAnalysisResult:
        """
        Perform comprehensive spatial analysis for a receipt.
        
        Args:
            words: List of receipt words with geometric data
            labels: List of corresponding word labels
            image_id: Receipt image UUID
            receipt_id: Receipt ID
            
        Returns:
            Complete spatial analysis result with metrics
        """
        start_time = datetime.now()
        
        try:
            # Validate inputs
            self._validate_inputs(words, labels, image_id, receipt_id)
            
            # Perform core spatial analysis
            analyses = self.analyzer.analyze_receipt_spatial_relationships(
                words, labels, image_id, receipt_id
            )
            
            # Apply filtering strategy to optimize storage
            optimized_analyses = self._apply_filtering_optimization(analyses)
            
            # Calculate processing time
            processing_time_ms = (datetime.now() - start_time).total_seconds() * 1000
            
            # Create result with comprehensive metrics
            result = SpatialAnalysisResult(
                receipt_id=receipt_id,
                image_id=image_id,
                analyses=optimized_analyses,
                statistics=self._calculate_result_statistics(optimized_analyses),
                processing_time_ms=processing_time_ms,
                filtering_strategy_used=self.filtering_strategy.get_strategy_name(),
            )
            
            # Update engine statistics
            self._update_engine_statistics(result)
            
            logger.info(
                f"Completed spatial analysis for receipt {receipt_id}: "
                f"{result.analysis_count} analyses, {result.total_relationships} relationships, "
                f"{processing_time_ms:.1f}ms"
            )
            
            return result
            
        except Exception as e:
            logger.error(
                f"Failed to analyze receipt {receipt_id}: {e}",
                exc_info=True
            )
            raise
    
    def analyze_multiple_receipts(
        self,
        receipt_data: List[Tuple[List[ReceiptWord], List[ReceiptWordLabel], str, int]],
    ) -> List[SpatialAnalysisResult]:
        """
        Analyze multiple receipts in sequence.
        
        Args:
            receipt_data: List of (words, labels, image_id, receipt_id) tuples
            
        Returns:
            List of spatial analysis results
        """
        results = []
        
        logger.info(f"Starting batch spatial analysis for {len(receipt_data)} receipts")
        
        for i, (words, labels, image_id, receipt_id) in enumerate(receipt_data):
            try:
                result = self.analyze_receipt(words, labels, image_id, receipt_id)
                results.append(result)
                
                if (i + 1) % 10 == 0:
                    logger.info(f"Processed {i + 1}/{len(receipt_data)} receipts")
                    
            except Exception as e:
                logger.error(f"Failed to process receipt {receipt_id}: {e}")
                continue
        
        logger.info(
            f"Completed batch analysis: {len(results)}/{len(receipt_data)} successful"
        )
        
        return results
    
    def _validate_inputs(
        self,
        words: List[ReceiptWord],
        labels: List[ReceiptWordLabel],
        image_id: str,
        receipt_id: int,
    ):
        """Validate input parameters."""
        if not words:
            raise ValueError(f"Receipt {receipt_id}: No words provided")
        
        if not labels:
            raise ValueError(f"Receipt {receipt_id}: No labels provided")
        
        if not image_id or not isinstance(image_id, str):
            raise ValueError(f"Receipt {receipt_id}: Invalid image_id")
        
        if receipt_id is None or not isinstance(receipt_id, int):
            raise ValueError("Invalid receipt_id")
        
        # Validate word-label correspondence
        word_keys = {(w.line_id, w.word_id) for w in words}
        label_keys = {(l.line_id, l.word_id) for l in labels}
        
        if not label_keys.issubset(word_keys):
            missing_words = label_keys - word_keys
            raise ValueError(
                f"Receipt {receipt_id}: Labels reference non-existent words: {missing_words}"
            )
    
    def _apply_filtering_optimization(
        self, 
        analyses: List[ReceiptWordLabelSpatialAnalysis]
    ) -> List[ReceiptWordLabelSpatialAnalysis]:
        """Apply filtering strategy to optimize analyses for storage."""
        if not analyses:
            return analyses
        
        optimized_analyses = []
        
        for analysis in analyses:
            # Convert relationships to filterable format
            word_distances = []
            for rel in analysis.spatial_relationships:
                # Create dummy objects for filtering interface compatibility
                # In practice, this would use the actual word/label objects
                word_distances.append((
                    rel.distance,
                    None,  # target_word (not needed for filtering)
                    rel,   # Use relationship as label proxy
                    rel.angle
                ))
            
            # Apply filtering strategy
            filtered_distances = self.filtering_strategy.filter_relationships(word_distances)
            
            # Rebuild relationships list
            filtered_relationships = [item[2] for item in filtered_distances]  # item[2] is the relationship
            
            # Create optimized analysis
            optimized_analysis = ReceiptWordLabelSpatialAnalysis(
                image_id=analysis.image_id,
                receipt_id=analysis.receipt_id,
                line_id=analysis.line_id,
                word_id=analysis.word_id,
                from_label=analysis.from_label,
                from_position=analysis.from_position,
                spatial_relationships=filtered_relationships,
                timestamp_added=analysis.timestamp_added,
                analysis_version=analysis.analysis_version,
            )
            
            optimized_analyses.append(optimized_analysis)
        
        return optimized_analyses
    
    def _calculate_result_statistics(
        self, 
        analyses: List[ReceiptWordLabelSpatialAnalysis]
    ) -> Dict[str, float]:
        """Calculate comprehensive statistics for the analysis result."""
        if not analyses:
            return {
                "analysis_count": 0,
                "total_relationships": 0,
                "avg_relationships_per_analysis": 0.0,
                "min_relationships": 0.0,
                "max_relationships": 0.0,
                "estimated_storage_kb": 0.0,
            }
        
        relationship_counts = [len(a.spatial_relationships) for a in analyses]
        total_relationships = sum(relationship_counts)
        
        # Estimate storage size using analyzer's formula
        estimated_storage_kb = sum(
            self.analyzer.estimate_item_size_kb(count) 
            for count in relationship_counts
        )
        
        return {
            "analysis_count": len(analyses),
            "total_relationships": total_relationships,
            "avg_relationships_per_analysis": total_relationships / len(analyses),
            "min_relationships": float(min(relationship_counts)),
            "max_relationships": float(max(relationship_counts)),
            "estimated_storage_kb": estimated_storage_kb,
        }
    
    def _update_engine_statistics(self, result: SpatialAnalysisResult):
        """Update engine-level statistics."""
        self._engine_stats["total_receipts_processed"] += 1
        self._engine_stats["total_analyses_created"] += result.analysis_count
        self._engine_stats["total_relationships_created"] += result.total_relationships
        
        # Update average processing time using running average
        n = self._engine_stats["total_receipts_processed"]
        old_avg = self._engine_stats["avg_processing_time_ms"]
        self._engine_stats["avg_processing_time_ms"] = (
            (old_avg * (n - 1) + result.processing_time_ms) / n
        )
    
    def get_engine_statistics(self) -> Dict[str, float]:
        """Get comprehensive engine statistics."""
        stats = self._engine_stats.copy()
        stats.update(self.analyzer.get_statistics())
        stats["filtering_strategy"] = self.filtering_strategy.get_strategy_name()
        return stats
    
    def reset_statistics(self):
        """Reset all statistics counters."""
        self._engine_stats = {
            "total_receipts_processed": 0,
            "total_analyses_created": 0,
            "total_relationships_created": 0,
            "avg_processing_time_ms": 0.0,
        }
        self.analyzer.clear_cache()
    
    def estimate_batch_storage_size(
        self, 
        analyses_per_receipt: List[int]
    ) -> Dict[str, float]:
        """
        Estimate storage requirements for a batch of receipts.
        
        Args:
            analyses_per_receipt: List of analysis counts per receipt
            
        Returns:
            Storage size estimates
        """
        # Assume average relationships per analysis based on filtering strategy
        if hasattr(self.filtering_strategy, 'max_relationships'):
            avg_relationships = (
                self.filtering_strategy.min_relationships + 
                self.filtering_strategy.max_relationships
            ) / 2
        else:
            avg_relationships = 15  # Conservative estimate
        
        total_analyses = sum(analyses_per_receipt)
        estimated_total_kb = total_analyses * self.analyzer.estimate_item_size_kb(avg_relationships)
        
        return {
            "total_receipts": len(analyses_per_receipt),
            "total_analyses": total_analyses,
            "estimated_total_storage_kb": estimated_total_kb,
            "estimated_total_storage_mb": estimated_total_kb / 1024,
            "avg_storage_per_receipt_kb": estimated_total_kb / len(analyses_per_receipt) if analyses_per_receipt else 0,
        }


def create_production_engine() -> SpatialAnalysisEngine:
    """Create a production-optimized spatial analysis engine."""
    return SpatialAnalysisEngine(
        filtering_strategy=FilteringStrategyFactory.get_recommended_strategy(),
        analysis_config=AnalysisConfig(
            max_distance=0.5,
            min_relationships=5,
            max_relationships=25,
            analysis_version="1.0",
            only_valid_labels=True,
            enable_caching=True,
            log_statistics=True,
        ),
    )


def create_high_precision_engine() -> SpatialAnalysisEngine:
    """Create an engine optimized for high precision analysis."""
    from .filtering_strategies import LabelTypeAwareStrategy
    
    return SpatialAnalysisEngine(
        filtering_strategy=LabelTypeAwareStrategy(
            distance_threshold=0.6,  # Slightly larger threshold
            min_relationships=8,     # More relationships for precision
            max_relationships=30,
        ),
        analysis_config=AnalysisConfig(
            max_distance=0.6,
            min_relationships=8,
            max_relationships=30,
            analysis_version="1.0-precision",
            only_valid_labels=True,
            enable_caching=True,
            log_statistics=True,
        ),
    )


def create_fast_processing_engine() -> SpatialAnalysisEngine:
    """Create an engine optimized for fast processing."""
    from .filtering_strategies import TopKClosestStrategy
    
    return SpatialAnalysisEngine(
        filtering_strategy=TopKClosestStrategy(k=15),
        analysis_config=AnalysisConfig(
            max_distance=0.4,       # Smaller threshold for speed
            min_relationships=3,    # Fewer relationships
            max_relationships=15,
            analysis_version="1.0-fast",
            only_valid_labels=True,
            enable_caching=True,
            log_statistics=False,   # Disable stats for speed
        ),
    )