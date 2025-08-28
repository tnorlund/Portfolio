"""
Filtering strategies for spatial relationship optimization.

This module provides various strategies for filtering spatial relationships to
optimize storage efficiency while maintaining analytical quality, following the
pattern-first design philosophy of the receipt_label package.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Protocol, Tuple

from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

logger = logging.getLogger(__name__)


# Type alias for word-distance tuples
WordDistanceTuple = Tuple[
    float, ReceiptWord, ReceiptWordLabel, float
]  # distance, word, label, angle


class FilteringStrategy(Protocol):
    """Protocol for spatial relationship filtering strategies."""

    def filter_relationships(
        self, word_distances: List[WordDistanceTuple]
    ) -> List[WordDistanceTuple]:
        """Filter relationships based on strategy-specific criteria."""
        ...

    def get_strategy_name(self) -> str:
        """Get human-readable strategy name."""
        ...


@dataclass
class DistanceThresholdStrategy:
    """
    Filter relationships by distance threshold.

    This strategy follows the empirical finding that relationships within
    distance 0.5 capture ~75% of spatially relevant connections while
    maintaining reasonable storage sizes.
    """

    max_distance: float = 0.5
    min_relationships: int = 5
    max_relationships: int = 25

    def filter_relationships(
        self, word_distances: List[WordDistanceTuple]
    ) -> List[WordDistanceTuple]:
        """Filter by distance threshold with fallback logic."""
        if not word_distances:
            return []

        # Primary filter: distance threshold
        close_words = [
            item
            for item in word_distances
            if item[0] <= self.max_distance  # item[0] is distance
        ]

        # Fallback: ensure minimum relationships for consistency
        if len(close_words) < self.min_relationships:
            close_words = sorted(word_distances, key=lambda x: x[0])[
                : self.min_relationships
            ]

        # Limit: cap at maximum to stay under DynamoDB constraints
        elif len(close_words) > self.max_relationships:
            close_words = sorted(close_words, key=lambda x: x[0])[
                : self.max_relationships
            ]

        return close_words

    def get_strategy_name(self) -> str:
        return f"DistanceThreshold(max_dist={self.max_distance})"


@dataclass
class TopKClosestStrategy:
    """
    Filter to keep only the K closest relationships.

    This strategy ensures consistent storage size regardless of receipt complexity
    but may miss some spatially relevant distant relationships.
    """

    k: int = 10

    def filter_relationships(
        self, word_distances: List[WordDistanceTuple]
    ) -> List[WordDistanceTuple]:
        """Keep only the K closest relationships."""
        if not word_distances:
            return []

        # Sort by distance and take top K
        sorted_distances = sorted(word_distances, key=lambda x: x[0])
        return sorted_distances[: self.k]

    def get_strategy_name(self) -> str:
        return f"TopKClosest(k={self.k})"


@dataclass
class PercentileStrategy:
    """
    Filter relationships based on distance percentiles.

    This adaptive strategy adjusts to the specific distance distribution
    of each receipt, ensuring consistent relative spatial coverage.
    """

    percentile: float = 75.0  # Keep relationships up to 75th percentile
    min_relationships: int = 5
    max_relationships: int = 25

    def filter_relationships(
        self, word_distances: List[WordDistanceTuple]
    ) -> List[WordDistanceTuple]:
        """Filter by distance percentile threshold."""
        if not word_distances:
            return []

        # Calculate distance threshold at specified percentile
        distances = [item[0] for item in word_distances]
        distances.sort()

        percentile_index = int(len(distances) * self.percentile / 100)
        if percentile_index >= len(distances):
            percentile_index = len(distances) - 1

        distance_threshold = distances[percentile_index]

        # Filter by percentile threshold
        filtered_words = [
            item for item in word_distances if item[0] <= distance_threshold
        ]

        # Apply min/max constraints
        if len(filtered_words) < self.min_relationships:
            filtered_words = sorted(word_distances, key=lambda x: x[0])[
                : self.min_relationships
            ]
        elif len(filtered_words) > self.max_relationships:
            filtered_words = sorted(filtered_words, key=lambda x: x[0])[
                : self.max_relationships
            ]

        return filtered_words

    def get_strategy_name(self) -> str:
        return f"Percentile({self.percentile}th)"


@dataclass
class HybridStrategy:
    """
    Hybrid strategy combining distance threshold with count limits.

    This is the recommended production strategy based on empirical testing,
    providing optimal balance of spatial relevance and storage efficiency.
    """

    distance_threshold: float = 0.5
    min_relationships: int = 5
    max_relationships: int = 25
    fallback_percentile: float = 50.0  # If distance filter fails, use median

    def filter_relationships(
        self, word_distances: List[WordDistanceTuple]
    ) -> List[WordDistanceTuple]:
        """Apply hybrid filtering with intelligent fallback."""
        if not word_distances:
            return []

        # Primary: Distance threshold filter
        close_words = [
            item
            for item in word_distances
            if item[0] <= self.distance_threshold
        ]

        # Fallback 1: If too few close words, use percentile approach
        if len(close_words) < self.min_relationships:
            distances = [item[0] for item in word_distances]
            distances.sort()

            percentile_index = int(
                len(distances) * self.fallback_percentile / 100
            )
            if percentile_index >= len(distances):
                percentile_index = len(distances) - 1

            percentile_threshold = distances[percentile_index]

            # Expand to include more words up to percentile or min count
            close_words = [
                item
                for item in word_distances
                if item[0]
                <= max(self.distance_threshold, percentile_threshold)
            ]

            # Final fallback: take closest N if still too few
            if len(close_words) < self.min_relationships:
                close_words = sorted(word_distances, key=lambda x: x[0])[
                    : self.min_relationships
                ]

        # Limit: Cap at maximum relationships
        if len(close_words) > self.max_relationships:
            close_words = sorted(close_words, key=lambda x: x[0])[
                : self.max_relationships
            ]

        return close_words

    def get_strategy_name(self) -> str:
        return f"Hybrid(dist={self.distance_threshold}, min={self.min_relationships}, max={self.max_relationships})"


@dataclass
class LabelTypeAwareStrategy:
    """
    Strategy that considers label types when filtering relationships.

    This advanced strategy prioritizes relationships to important label types
    (GRAND_TOTAL, MERCHANT_NAME, etc.) while still respecting distance constraints.
    """

    distance_threshold: float = 0.5
    min_relationships: int = 5
    max_relationships: int = 25

    # Priority label types (higher priority = more important)
    priority_labels: dict = None

    def __post_init__(self):
        if self.priority_labels is None:
            self.priority_labels = {
                "GRAND_TOTAL": 10,
                "MERCHANT_NAME": 9,
                "DATE": 8,
                "TAX": 7,
                "SUBTOTAL": 6,
                "PAYMENT_METHOD": 5,
                "LINE_TOTAL": 4,
                "UNIT_PRICE": 3,
                "QUANTITY": 2,
                # All others default to 1
            }

    def filter_relationships(
        self, word_distances: List[WordDistanceTuple]
    ) -> List[WordDistanceTuple]:
        """Filter with label type awareness."""
        if not word_distances:
            return []

        # Add priority scores to relationships
        prioritized_distances = []
        for distance, word, label, angle in word_distances:
            priority = self.priority_labels.get(label.label, 1)
            # Composite score: lower distance + higher priority = better
            composite_score = distance / priority
            prioritized_distances.append(
                (composite_score, distance, word, label, angle)
            )

        # Sort by composite score (lower is better)
        prioritized_distances.sort(key=lambda x: x[0])

        # Filter by distance threshold
        filtered_relationships = []
        for (
            composite_score,
            distance,
            word,
            label,
            angle,
        ) in prioritized_distances:
            if (
                distance <= self.distance_threshold
                or len(filtered_relationships) < self.min_relationships
            ):
                filtered_relationships.append((distance, word, label, angle))

                if len(filtered_relationships) >= self.max_relationships:
                    break

        return filtered_relationships

    def get_strategy_name(self) -> str:
        return f"LabelTypeAware(dist={self.distance_threshold})"


class FilteringStrategyFactory:
    """Factory for creating filtering strategies."""

    @staticmethod
    def create_strategy(strategy_name: str, **kwargs) -> FilteringStrategy:
        """
        Create a filtering strategy by name.

        Args:
            strategy_name: Name of strategy ("distance", "topk", "percentile", "hybrid", "label_aware")
            **kwargs: Strategy-specific parameters

        Returns:
            Configured filtering strategy
        """
        strategy_name = strategy_name.lower()

        if strategy_name == "distance":
            return DistanceThresholdStrategy(**kwargs)
        elif strategy_name == "topk":
            return TopKClosestStrategy(**kwargs)
        elif strategy_name == "percentile":
            return PercentileStrategy(**kwargs)
        elif strategy_name == "hybrid":
            return HybridStrategy(**kwargs)
        elif strategy_name == "label_aware":
            return LabelTypeAwareStrategy(**kwargs)
        else:
            raise ValueError(
                f"Unknown filtering strategy: {strategy_name}. "
                f"Available: distance, topk, percentile, hybrid, label_aware"
            )

    @staticmethod
    def get_recommended_strategy() -> FilteringStrategy:
        """Get the recommended production strategy based on empirical testing."""
        return HybridStrategy(
            distance_threshold=0.5,
            min_relationships=5,
            max_relationships=25,
            fallback_percentile=50.0,
        )

    @staticmethod
    def get_available_strategies() -> List[str]:
        """Get list of available strategy names."""
        return ["distance", "topk", "percentile", "hybrid", "label_aware"]


def analyze_filtering_performance(
    word_distances: List[WordDistanceTuple],
    strategies: List[FilteringStrategy],
) -> dict:
    """
    Analyze performance of different filtering strategies on the same data.

    Args:
        word_distances: Input relationships to filter
        strategies: List of strategies to compare

    Returns:
        Performance analysis results
    """
    results = {}

    for strategy in strategies:
        strategy_name = strategy.get_strategy_name()

        try:
            filtered_relationships = strategy.filter_relationships(
                word_distances
            )

            # Calculate metrics
            relationship_count = len(filtered_relationships)
            if filtered_relationships:
                avg_distance = (
                    sum(item[0] for item in filtered_relationships)
                    / relationship_count
                )
                max_distance = max(item[0] for item in filtered_relationships)
                min_distance = min(item[0] for item in filtered_relationships)
            else:
                avg_distance = max_distance = min_distance = 0.0

            # Estimate storage size (empirical formula)
            estimated_size_kb = 0.9 + (relationship_count * 0.12)

            results[strategy_name] = {
                "relationship_count": relationship_count,
                "avg_distance": avg_distance,
                "max_distance": max_distance,
                "min_distance": min_distance,
                "estimated_size_kb": estimated_size_kb,
                "under_400kb_limit": estimated_size_kb < 400,
            }

        except Exception as e:
            results[strategy_name] = {"error": str(e)}

    return results
