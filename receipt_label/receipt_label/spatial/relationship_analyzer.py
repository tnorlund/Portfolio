"""
Spatial relationship analysis for receipt word labels.

This module provides core functionality for analyzing spatial relationships
between validated receipt word labels, enabling pattern-based validation and
quality assessment.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_dynamo.entities.receipt_word_label_spatial_analysis import (
    ReceiptWordLabelSpatialAnalysis,
    SpatialRelationship,
)

logger = logging.getLogger(__name__)


@dataclass
class AnalysisConfig:
    """Configuration for spatial relationship analysis."""

    # Distance filtering
    max_distance: float = 0.5
    min_relationships: int = 5
    max_relationships: int = 25

    # Aspect ratio correction
    use_aspect_ratio_correction: bool = True
    default_aspect_ratio: float = 3.4  # Based on coordinate system analysis
    use_dynamic_aspect_ratio: bool = True  # Fetch from receipt dimensions

    # Separate X/Y thresholds for asymmetric receipts
    max_x_distance: float = 0.2
    max_y_distance: float = 0.68  # 0.2 * 3.4 to account for Y-axis compression

    # Analysis settings
    analysis_version: str = "1.1"  # Updated for aspect ratio correction
    only_valid_labels: bool = True

    # Performance settings
    enable_caching: bool = True
    log_statistics: bool = True


class SpatialRelationshipAnalyzer:
    """
    Analyzes spatial relationships between validated receipt word labels.

    This analyzer follows the pattern-first design philosophy of the
    receipt_label package, focusing on extracting meaningful spatial patterns
    from validated label relationships.
    """

    def __init__(
        self, config: Optional[AnalysisConfig] = None, dynamo_client=None
    ):
        """Initialize the spatial relationship analyzer.

        Args:
            config: Configuration for analysis behavior
            dynamo_client: DynamoDB client for fetching receipt dimensions
                (optional)
        """
        self.config = config or AnalysisConfig()
        self.dynamo_client = dynamo_client
        self._distance_cache = {} if self.config.enable_caching else None
        self._aspect_ratio_cache = {}  # Cache receipt aspect ratios
        self._analysis_stats = {
            "total_analyses": 0,
            "total_relationships": 0,
            "avg_relationships_per_analysis": 0.0,
        }

        logger.info(
            "Initialized SpatialRelationshipAnalyzer with config: "
            "max_distance=%s, "
            "aspect_ratio_correction=%s, "
            "default_aspect_ratio=%s, "
            "min_relationships=%s, "
            "max_relationships=%s",
            self.config.max_distance,
            self.config.use_aspect_ratio_correction,
            self.config.default_aspect_ratio,
            self.config.min_relationships,
            self.config.max_relationships,
        )

    def analyze_receipt_spatial_relationships(
        self,
        words: List[ReceiptWord],
        labels: List[ReceiptWordLabel],
        image_id: str,
        receipt_id: int,
    ) -> List[ReceiptWordLabelSpatialAnalysis]:
        """
        Analyze spatial relationships for all valid labels on a receipt.

        Args:
            words: List of receipt words with geometric data
            labels: List of corresponding word labels
            image_id: Receipt image UUID
            receipt_id: Receipt ID

        Returns:
            List of spatial analysis entities with relationship data
        """
        # Filter to valid labels if configured
        valid_pairs = self._get_valid_word_label_pairs(words, labels)

        if len(valid_pairs) < 2:
            logger.debug(
                "Receipt %s has %s valid labels, "
                "skipping spatial analysis (minimum 2 required)",
                receipt_id,
                len(valid_pairs),
            )
            return []

        logger.debug(
            "Analyzing spatial relationships for %s valid labels "
            "on receipt %s",
            len(valid_pairs),
            receipt_id,
        )

        spatial_analyses = []

        for source_word, source_label in valid_pairs:
            relationships = self._compute_spatial_relationships(
                source_word=source_word,
                valid_pairs=valid_pairs,
            )

            if relationships:
                analysis = self._create_spatial_analysis(
                    source_word,
                    source_label,
                    relationships,
                    image_id,
                    receipt_id,
                )
                spatial_analyses.append(analysis)

        # Update statistics
        self._update_statistics(spatial_analyses)

        logger.info(
            "Created %s spatial analyses for receipt %s",
            len(spatial_analyses),
            receipt_id,
        )

        return spatial_analyses

    def _get_valid_word_label_pairs(
        self, words: List[ReceiptWord], labels: List[ReceiptWordLabel]
    ) -> List[Tuple[ReceiptWord, ReceiptWordLabel]]:
        """Filter to valid word-label pairs."""
        # Create lookup for labels by word coordinates
        label_lookup = {}
        for label in labels:
            # Only include valid labels if configured
            if (
                not self.config.only_valid_labels
                or label.validation_status == ValidationStatus.VALID.value
            ):
                key = (label.line_id, label.word_id)
                label_lookup[key] = label

        # Match words with their valid labels
        valid_pairs = []
        for word in words:
            key = (word.line_id, word.word_id)
            if key in label_lookup:
                valid_pairs.append((word, label_lookup[key]))

        return valid_pairs

    def _compute_spatial_relationships(
        self,
        source_word: ReceiptWord,
        valid_pairs: List[Tuple[ReceiptWord, ReceiptWordLabel]],
    ) -> List[SpatialRelationship]:
        """Compute spatial relationships using optimized distance filtering."""
        # Compute distances to all other words
        word_distances = []

        for target_word, target_label in valid_pairs:
            if (source_word.line_id, source_word.word_id) != (
                target_word.line_id,
                target_word.word_id,
            ):
                distance, angle = self._get_corrected_distance_and_angle(
                    source_word=source_word,
                    target_word=target_word,
                    image_id=source_word.image_id,
                    receipt_id=source_word.receipt_id,
                )
                word_distances.append(
                    (distance, target_word, target_label, angle)
                )

        # Apply hybrid distance + count filtering strategy
        relationships = []

        # 1. Filter by distance threshold for spatial relevance
        close_words = [
            (d, w, l, a)
            for d, w, l, a in word_distances
            if d <= self.config.max_distance
        ]

        # 2. Fallback: if too few close words, take closest ones for
        # consistency
        if len(close_words) < self.config.min_relationships:
            close_words = sorted(word_distances, key=lambda x: x[0])[
                : self.config.min_relationships
            ]

        # 3. Limit: if too many, take closest to stay under DynamoDB limits
        elif len(close_words) > self.config.max_relationships:
            close_words = sorted(close_words, key=lambda x: x[0])[
                : self.config.max_relationships
            ]

        # Create spatial relationships
        for distance, target_word, target_label, angle in close_words:
            relationships.append(
                SpatialRelationship(
                    to_label=target_label.label,
                    to_line_id=target_word.line_id,
                    to_word_id=target_word.word_id,
                    distance=distance,
                    angle=angle,
                )
            )

        return relationships

    def _get_corrected_distance_and_angle(
        self,
        source_word: ReceiptWord,
        target_word: ReceiptWord,
        receipt_id: int,
        image_id: str = None,
    ) -> Tuple[float, float]:
        """Get aspect-ratio corrected distance and angle between words."""
        # Create cache key for corrected distances
        source_key = (source_word.line_id, source_word.word_id)
        target_key = (target_word.line_id, target_word.word_id)
        cache_key = (
            (source_key, target_key, receipt_id)
            if self.config.use_aspect_ratio_correction
            else (source_key, target_key)
        )

        # Check cache first
        if self.config.enable_caching and cache_key in self._distance_cache:
            return self._distance_cache[cache_key]

        # Get raw distance and angle from ReceiptWord's built-in method
        raw_distance, angle = (
            source_word.distance_and_angle_from__receipt_word(target_word)
        )

        # Apply aspect ratio correction if enabled
        if not self.config.use_aspect_ratio_correction:
            corrected_distance = raw_distance
        else:
            # Use image_id from source_word if not provided
            if image_id is None:
                image_id = source_word.image_id
            aspect_ratio = self._get_receipt_aspect_ratio(image_id, receipt_id)
            corrected_distance = self._apply_aspect_ratio_correction(
                source_word, target_word, aspect_ratio
            )

        # Cache the result
        if self.config.enable_caching:
            self._distance_cache[cache_key] = (corrected_distance, angle)

        return corrected_distance, angle

    def _get_receipt_aspect_ratio(
        self, image_id: str, receipt_id: int
    ) -> float:
        """Get receipt aspect ratio from cache or fetch from DynamoDB."""
        cache_key = (image_id, receipt_id)
        if cache_key in self._aspect_ratio_cache:
            return self._aspect_ratio_cache[cache_key]

        # If dynamic aspect ratio is disabled or no client, use default
        if not self.config.use_dynamic_aspect_ratio or not self.dynamo_client:
            aspect_ratio = self.config.default_aspect_ratio
        else:
            try:
                # Try to fetch receipt dimensions from DynamoDB using correct
                # method signature
                receipt = self.dynamo_client.get_receipt(image_id, receipt_id)
                if (
                    receipt
                    and hasattr(receipt, "height")
                    and hasattr(receipt, "width")
                ):
                    height = receipt.height
                    width = receipt.width
                    aspect_ratio = (
                        height / width
                        if width > 0
                        else self.config.default_aspect_ratio
                    )
                else:
                    aspect_ratio = self.config.default_aspect_ratio
                    logger.debug(
                        "No dimensions found for receipt %s/%s, using default "
                        "aspect ratio %s",
                        image_id,
                        receipt_id,
                        aspect_ratio,
                    )
            except Exception as e:
                logger.warning(
                    "Failed to fetch receipt dimensions for %s/%s: %s",
                    image_id,
                    receipt_id,
                    e,
                )
                aspect_ratio = self.config.default_aspect_ratio

        # Cache the result
        self._aspect_ratio_cache[cache_key] = aspect_ratio
        return aspect_ratio

    def _apply_aspect_ratio_correction(
        self,
        source_word: ReceiptWord,
        target_word: ReceiptWord,
        aspect_ratio: float,
    ) -> float:
        """Apply aspect ratio correction to distance calculation."""
        # Get centroids using ReceiptWord's built-in method
        x1, y1 = source_word.calculate_centroid()
        x2, y2 = target_word.calculate_centroid()

        # Apply aspect ratio correction to Y coordinates
        # Since receipts are normalized to (1,1) but are typically taller
        # than wide, we need to expand the Y-axis by the aspect ratio
        y1_corrected = y1 * aspect_ratio
        y2_corrected = y2 * aspect_ratio

        # Recalculate distance with corrected coordinates
        corrected_distance = (
            (x2 - x1) ** 2 + (y2_corrected - y1_corrected) ** 2
        ) ** 0.5

        return corrected_distance

    def _create_spatial_analysis(
        self,
        source_word: ReceiptWord,
        source_label: ReceiptWordLabel,
        relationships: List[SpatialRelationship],
        image_id: str,
        receipt_id: int,
    ) -> ReceiptWordLabelSpatialAnalysis:
        """Create a spatial analysis entity."""
        source_centroid = source_word.calculate_centroid()

        return ReceiptWordLabelSpatialAnalysis(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=source_word.line_id,
            word_id=source_word.word_id,
            from_label=source_label.label,
            from_position={"x": source_centroid[0], "y": source_centroid[1]},
            spatial_relationships=relationships,
            timestamp_added=datetime.now(),
            analysis_version=self.config.analysis_version,
        )

    def _update_statistics(
        self, spatial_analyses: List[ReceiptWordLabelSpatialAnalysis]
    ):
        """Update internal statistics."""
        if not self.config.log_statistics:
            return

        self._analysis_stats["total_analyses"] += len(spatial_analyses)
        total_rels = sum(
            len(analysis.spatial_relationships)
            for analysis in spatial_analyses
        )
        self._analysis_stats["total_relationships"] += total_rels

        if self._analysis_stats["total_analyses"] > 0:
            self._analysis_stats["avg_relationships_per_analysis"] = (
                self._analysis_stats["total_relationships"]
                / self._analysis_stats["total_analyses"]
            )

    def get_statistics(self) -> Dict[str, float]:
        """Get analysis statistics."""
        return self._analysis_stats.copy()

    def clear_cache(self):
        """Clear the distance calculation cache."""
        if self._distance_cache is not None:
            self._distance_cache.clear()
            logger.debug("Cleared distance calculation cache")

    def estimate_item_size_kb(self, relationships_count: int) -> float:
        """
        Estimate DynamoDB item size in KB for a given relationship count.

        Based on empirical measurements from testing.
        """
        # Empirical formula: base overhead + per-relationship cost
        base_size = 0.9  # KB for base item structure
        per_relationship = 0.12  # KB per relationship

        return base_size + (relationships_count * per_relationship)
