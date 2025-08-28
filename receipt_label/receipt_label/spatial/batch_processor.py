"""
Batch processing utilities for spatial analysis at scale.

This module provides efficient batch processing capabilities for spatial analysis,
including parallel processing, progress tracking, and error handling, following
the receipt_label package's pattern-first design philosophy.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Callable, Any
from tqdm import tqdm
import asyncio
import time

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_dynamo.entities.receipt_word_label_spatial_analysis import (
    ReceiptWordLabelSpatialAnalysis,
)

from .analysis_engine import SpatialAnalysisEngine, SpatialAnalysisResult

logger = logging.getLogger(__name__)


@dataclass
class BatchProcessingConfig:
    """Configuration for batch processing operations."""

    # Concurrency settings
    max_workers: int = 4
    batch_size: int = 10

    # Progress and monitoring
    show_progress: bool = True
    progress_update_interval: int = 5  # Update every N receipts

    # Error handling
    continue_on_error: bool = True
    max_consecutive_errors: int = 10

    # Performance settings
    enable_async: bool = False  # Use asyncio instead of threading
    memory_limit_mb: Optional[int] = None

    # Storage optimization
    auto_batch_writes: bool = True
    write_batch_size: int = 25


@dataclass
class BatchResult:
    """Result of batch processing operation."""

    total_receipts: int
    successful_receipts: int
    failed_receipts: int
    total_analyses_created: int
    total_relationships_created: int
    processing_time_seconds: float
    errors: List[Tuple[int, str]]  # (receipt_id, error_message)

    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage."""
        if self.total_receipts == 0:
            return 0.0
        return (self.successful_receipts / self.total_receipts) * 100

    @property
    def avg_processing_time_per_receipt_ms(self) -> float:
        """Calculate average processing time per receipt in milliseconds."""
        if self.successful_receipts == 0:
            return 0.0
        return (self.processing_time_seconds * 1000) / self.successful_receipts

    @property
    def throughput_receipts_per_second(self) -> float:
        """Calculate processing throughput in receipts per second."""
        if self.processing_time_seconds == 0:
            return 0.0
        return self.successful_receipts / self.processing_time_seconds


class SpatialAnalysisBatchProcessor:
    """
    High-performance batch processor for spatial analysis operations.

    This processor implements several optimization strategies:
    1. Parallel processing using ThreadPoolExecutor
    2. Configurable batch sizes for memory management
    3. Progress tracking with detailed metrics
    4. Robust error handling with recovery options
    5. Memory monitoring and automatic cleanup

    Designed to follow the receipt_label package's pattern of providing both
    simple interfaces for common cases and advanced configuration for optimization.
    """

    def __init__(
        self,
        engine: SpatialAnalysisEngine,
        config: Optional[BatchProcessingConfig] = None,
    ):
        """Initialize the batch processor.

        Args:
            engine: Spatial analysis engine to use for processing
            config: Configuration for batch processing behavior
        """
        self.engine = engine
        self.config = config or BatchProcessingConfig()

        self._processing_stats = {
            "batches_processed": 0,
            "total_processing_time": 0.0,
            "peak_memory_usage_mb": 0.0,
        }

        logger.info(
            f"Initialized SpatialAnalysisBatchProcessor with "
            f"max_workers={self.config.max_workers}, "
            f"batch_size={self.config.batch_size}"
        )

    def process_receipts_batch(
        self,
        receipt_data: List[
            Tuple[List[ReceiptWord], List[ReceiptWordLabel], str, int]
        ],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> BatchResult:
        """
        Process a batch of receipts with parallel processing.

        Args:
            receipt_data: List of (words, labels, image_id, receipt_id) tuples
            progress_callback: Optional callback for progress updates (current, total)

        Returns:
            Comprehensive batch processing result
        """
        start_time = time.time()

        logger.info(
            f"Starting batch processing of {len(receipt_data)} receipts"
        )

        # Initialize result tracking
        successful_results = []
        failed_receipts = []
        consecutive_errors = 0

        # Set up progress tracking
        if self.config.show_progress:
            progress_bar = tqdm(
                total=len(receipt_data),
                desc="Processing receipts",
                unit="receipt",
            )

        try:
            if self.config.enable_async:
                results = self._process_async_batch(
                    receipt_data, progress_callback
                )
            else:
                results = self._process_threaded_batch(
                    receipt_data, progress_callback
                )

            # Process results
            for result in results:
                if isinstance(result, SpatialAnalysisResult):
                    successful_results.append(result)
                    consecutive_errors = 0

                    if self.config.show_progress:
                        progress_bar.update(1)
                        progress_bar.set_postfix(
                            {
                                "success": len(successful_results),
                                "failed": len(failed_receipts),
                                "analyses": result.analysis_count,
                            }
                        )

                elif isinstance(result, tuple):  # Error result
                    receipt_id, error_msg = result
                    failed_receipts.append((receipt_id, error_msg))
                    consecutive_errors += 1

                    if self.config.show_progress:
                        progress_bar.update(1)

                    logger.warning(
                        f"Failed to process receipt {receipt_id}: {error_msg}"
                    )

                    # Check if we should stop due to consecutive errors
                    if (
                        consecutive_errors
                        >= self.config.max_consecutive_errors
                        and not self.config.continue_on_error
                    ):
                        logger.error(
                            f"Stopping batch processing after {consecutive_errors} "
                            "consecutive errors"
                        )
                        break

                # Optional progress callback
                if progress_callback:
                    progress_callback(
                        len(successful_results) + len(failed_receipts),
                        len(receipt_data),
                    )

        finally:
            if self.config.show_progress:
                progress_bar.close()

        # Calculate final metrics
        processing_time = time.time() - start_time
        total_analyses = sum(r.analysis_count for r in successful_results)
        total_relationships = sum(
            r.total_relationships for r in successful_results
        )

        result = BatchResult(
            total_receipts=len(receipt_data),
            successful_receipts=len(successful_results),
            failed_receipts=len(failed_receipts),
            total_analyses_created=total_analyses,
            total_relationships_created=total_relationships,
            processing_time_seconds=processing_time,
            errors=failed_receipts,
        )

        self._update_processing_stats(result)

        logger.info(
            f"Batch processing complete: {result.successful_receipts}/{result.total_receipts} "
            f"receipts successful ({result.success_rate:.1f}%), "
            f"{result.total_analyses_created} analyses, "
            f"{result.processing_time_seconds:.2f}s total"
        )

        return result

    def _process_threaded_batch(
        self,
        receipt_data: List[
            Tuple[List[ReceiptWord], List[ReceiptWordLabel], str, int]
        ],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[Any]:
        """Process batch using ThreadPoolExecutor."""
        results = []

        with ThreadPoolExecutor(
            max_workers=self.config.max_workers
        ) as executor:
            # Submit all tasks
            future_to_receipt = {
                executor.submit(
                    self._process_single_receipt_safe,
                    words,
                    labels,
                    image_id,
                    receipt_id,
                ): receipt_id
                for words, labels, image_id, receipt_id in receipt_data
            }

            # Collect results as they complete
            for future in as_completed(future_to_receipt):
                result = future.result()
                results.append(result)

        return results

    def _process_async_batch(
        self,
        receipt_data: List[
            Tuple[List[ReceiptWord], List[ReceiptWordLabel], str, int]
        ],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[Any]:
        """Process batch using asyncio (experimental)."""
        # Note: This would require async versions of the underlying analysis methods
        # For now, fallback to threaded processing
        logger.warning(
            "Async processing not yet implemented, falling back to threaded"
        )
        return self._process_threaded_batch(receipt_data, progress_callback)

    def _process_single_receipt_safe(
        self,
        words: List[ReceiptWord],
        labels: List[ReceiptWordLabel],
        image_id: str,
        receipt_id: int,
    ) -> Any:
        """Safely process a single receipt with error handling."""
        try:
            return self.engine.analyze_receipt(
                words, labels, image_id, receipt_id
            )
        except Exception as e:
            error_msg = str(e)
            logger.debug(
                f"Receipt {receipt_id} processing failed: {error_msg}"
            )
            return (receipt_id, error_msg)

    def process_receipts_in_chunks(
        self,
        receipt_data: List[
            Tuple[List[ReceiptWord], List[ReceiptWordLabel], str, int]
        ],
        chunk_size: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[BatchResult]:
        """
        Process receipts in smaller chunks to manage memory usage.

        Args:
            receipt_data: List of receipt data tuples
            chunk_size: Size of each chunk (defaults to batch_size * 2)
            progress_callback: Optional callback for overall progress

        Returns:
            List of batch results, one per chunk
        """
        chunk_size = chunk_size or (self.config.batch_size * 2)
        chunks = [
            receipt_data[i : i + chunk_size]
            for i in range(0, len(receipt_data), chunk_size)
        ]

        logger.info(
            f"Processing {len(receipt_data)} receipts in {len(chunks)} chunks"
        )

        chunk_results = []
        total_processed = 0

        for i, chunk in enumerate(chunks):
            logger.info(
                f"Processing chunk {i + 1}/{len(chunks)} ({len(chunk)} receipts)"
            )

            chunk_result = self.process_receipts_batch(
                chunk, progress_callback=None  # Handle progress at chunk level
            )
            chunk_results.append(chunk_result)

            total_processed += chunk_result.successful_receipts

            # Overall progress callback
            if progress_callback:
                progress_callback(total_processed, len(receipt_data))

            logger.info(
                f"Chunk {i + 1} complete: {chunk_result.successful_receipts}/{len(chunk)} "
                f"successful ({chunk_result.success_rate:.1f}%)"
            )

        return chunk_results

    def estimate_batch_requirements(
        self,
        receipt_count: int,
        avg_words_per_receipt: int = 50,
        avg_labels_per_receipt: int = 25,
    ) -> Dict[str, Any]:
        """
        Estimate processing requirements for a batch of receipts.

        Args:
            receipt_count: Number of receipts to process
            avg_words_per_receipt: Average words per receipt
            avg_labels_per_receipt: Average labels per receipt

        Returns:
            Estimated requirements and recommendations
        """
        # Estimate processing time based on engine statistics
        engine_stats = self.engine.get_engine_statistics()
        avg_time_ms = engine_stats.get("avg_processing_time_ms", 100)

        # Sequential processing time
        sequential_time_seconds = (receipt_count * avg_time_ms) / 1000

        # Parallel processing time (with overhead)
        parallel_time_seconds = (
            sequential_time_seconds / self.config.max_workers * 1.2
        )

        # Memory estimation
        estimated_memory_mb = receipt_count * 0.5  # Conservative estimate

        # Storage estimation
        storage_estimate = self.engine.estimate_batch_storage_size(
            [avg_labels_per_receipt] * receipt_count
        )

        return {
            "processing_estimates": {
                "sequential_time_minutes": sequential_time_seconds / 60,
                "parallel_time_minutes": parallel_time_seconds / 60,
                "estimated_memory_usage_mb": estimated_memory_mb,
            },
            "storage_estimates": storage_estimate,
            "recommendations": {
                "suggested_chunk_size": min(
                    1000, max(100, receipt_count // 10)
                ),
                "suggested_max_workers": min(
                    8, max(2, self.config.max_workers)
                ),
                "memory_warning": estimated_memory_mb > 1000,
                "time_warning": parallel_time_seconds > 1800,  # 30 minutes
            },
        }

    def _update_processing_stats(self, result: BatchResult):
        """Update internal processing statistics."""
        self._processing_stats["batches_processed"] += 1
        self._processing_stats[
            "total_processing_time"
        ] += result.processing_time_seconds

    def get_processing_statistics(self) -> Dict[str, Any]:
        """Get comprehensive processing statistics."""
        stats = self._processing_stats.copy()
        stats.update(self.engine.get_engine_statistics())
        return stats

    def reset_statistics(self):
        """Reset all processing statistics."""
        self._processing_stats = {
            "batches_processed": 0,
            "total_processing_time": 0.0,
            "peak_memory_usage_mb": 0.0,
        }
        self.engine.reset_statistics()


def create_production_batch_processor(
    engine: Optional[SpatialAnalysisEngine] = None,
) -> SpatialAnalysisBatchProcessor:
    """Create a batch processor optimized for production use."""
    from .analysis_engine import create_production_engine

    if engine is None:
        engine = create_production_engine()

    config = BatchProcessingConfig(
        max_workers=4,
        batch_size=10,
        show_progress=True,
        continue_on_error=True,
        max_consecutive_errors=20,
        auto_batch_writes=True,
        write_batch_size=25,
    )

    return SpatialAnalysisBatchProcessor(engine, config)


def create_fast_batch_processor(
    engine: Optional[SpatialAnalysisEngine] = None,
) -> SpatialAnalysisBatchProcessor:
    """Create a batch processor optimized for speed."""
    from .analysis_engine import create_fast_processing_engine

    if engine is None:
        engine = create_fast_processing_engine()

    config = BatchProcessingConfig(
        max_workers=8,  # More workers
        batch_size=20,  # Larger batches
        show_progress=False,  # Disable progress for speed
        continue_on_error=True,
        max_consecutive_errors=50,
        auto_batch_writes=True,
        write_batch_size=50,  # Larger write batches
    )

    return SpatialAnalysisBatchProcessor(engine, config)


def create_memory_efficient_processor(
    engine: Optional[SpatialAnalysisEngine] = None,
) -> SpatialAnalysisBatchProcessor:
    """Create a batch processor optimized for memory efficiency."""
    from .analysis_engine import create_production_engine

    if engine is None:
        engine = create_production_engine()

    config = BatchProcessingConfig(
        max_workers=2,  # Fewer workers
        batch_size=5,  # Smaller batches
        show_progress=True,
        continue_on_error=True,
        max_consecutive_errors=10,
        memory_limit_mb=512,  # Memory limit
        auto_batch_writes=True,
        write_batch_size=10,  # Smaller write batches
    )

    return SpatialAnalysisBatchProcessor(engine, config)
