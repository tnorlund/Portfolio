"""Pinecone Integration for Smart Decision Engine.

This module provides Pinecone-based merchant reliability lookup and pattern
validation for the Smart Decision Engine.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from .config import DecisionEngineConfig
from .types import MerchantReliabilityData

logger = logging.getLogger(__name__)


class PineconeDecisionHelper:
    """Helper class for Pinecone queries in decision making.

    Provides merchant reliability data and pattern validation using
    the existing Pinecone infrastructure.
    """

    def __init__(self, pinecone_client, config: DecisionEngineConfig):
        """Initialize with Pinecone client and configuration.

        Args:
            pinecone_client: Existing Pinecone client (from ClientManager)
            config: Decision engine configuration
        """
        self.client = pinecone_client
        self.config = config
        self._reliability_cache: Dict[str, MerchantReliabilityData] = {}
        self._cache_ttl_hours = 24  # Cache merchant data for 24 hours

    async def get_merchant_reliability(
        self, merchant_name: str, refresh_cache: bool = False
    ) -> Optional[MerchantReliabilityData]:
        """Get merchant reliability data from Pinecone.

        Args:
            merchant_name: Name of the merchant to look up
            refresh_cache: Whether to refresh cached data

        Returns:
            MerchantReliabilityData if merchant found, None otherwise
        """
        if not self.config.enable_pinecone_validation:
            logger.debug(
                "Pinecone validation disabled, skipping merchant lookup"
            )
            return None

        if not merchant_name:
            return None

        # Check cache first (unless refresh requested)
        cache_key = merchant_name.lower().strip()
        if not refresh_cache and cache_key in self._reliability_cache:
            cached_data = self._reliability_cache[cache_key]
            # Check if cache is still valid
            if datetime.now() - cached_data.last_updated < timedelta(
                hours=self._cache_ttl_hours
            ):
                logger.debug(
                    f"Using cached reliability data for merchant: {merchant_name}"
                )
                return cached_data

        try:
            # Query Pinecone for merchant-specific data
            reliability_data = await self._query_merchant_data(merchant_name)

            if reliability_data:
                # Cache the result
                self._reliability_cache[cache_key] = reliability_data
                logger.debug(
                    f"Retrieved reliability data for merchant '{merchant_name}': "
                    f"success_rate={reliability_data.pattern_only_success_rate:.2f}, "
                    f"receipts={reliability_data.total_receipts_processed}"
                )

            return reliability_data

        except Exception as e:
            logger.error(
                f"Error querying Pinecone for merchant '{merchant_name}': {e}"
            )
            return None

    async def _query_merchant_data(
        self, merchant_name: str
    ) -> Optional[MerchantReliabilityData]:
        """Query Pinecone for merchant-specific pattern data.

        This method analyzes historical receipt data for the merchant to calculate
        reliability metrics.
        """
        try:
            # Normalize merchant name for consistent querying
            normalized_merchant = self._normalize_merchant_name(merchant_name)

            # Query 1: Get recent receipts for this merchant
            merchant_filter = {
                "merchant_name": normalized_merchant,
                # Only look at receipts from last 90 days for relevance
                "timestamp": {
                    "$gte": (datetime.now() - timedelta(days=90)).isoformat()
                },
            }

            # Query Pinecone for merchant-specific vectors
            query_response = await self._execute_pinecone_query(
                filter=merchant_filter,
                top_k=100,  # Get up to 100 recent receipts
                include_metadata=True,
            )

            if not query_response or not query_response.matches:
                logger.debug(
                    f"No Pinecone data found for merchant: {normalized_merchant}"
                )
                return None

            # Analyze the retrieved data
            return self._analyze_merchant_data(
                normalized_merchant, query_response.matches
            )

        except Exception as e:
            logger.error(
                f"Error in _query_merchant_data for '{merchant_name}': {e}"
            )
            return None

    async def _execute_pinecone_query(
        self,
        filter: Dict[str, Any],
        top_k: int = 100,
        include_metadata: bool = True,
    ) -> Any:
        """Execute Pinecone query with error handling."""
        try:
            # Use a dummy vector for metadata-only filtering
            # In practice, this might use a semantic query vector
            dummy_vector = [0.0] * 1536  # OpenAI embedding dimension

            response = self.client.query(
                vector=dummy_vector,
                filter=filter,
                top_k=top_k,
                include_metadata=include_metadata,
            )

            return response

        except Exception as e:
            logger.error(f"Pinecone query failed: {e}")
            return None

    def _analyze_merchant_data(
        self, merchant_name: str, matches: List[Any]
    ) -> MerchantReliabilityData:
        """Analyze Pinecone matches to calculate merchant reliability."""

        total_receipts = len(
            set(
                match.metadata.get("receipt_id")
                for match in matches
                if match.metadata
            )
        )

        # Count valid vs invalid labels
        valid_labels = defaultdict(int)
        invalid_labels = defaultdict(int)
        all_labels_seen = set()

        for match in matches:
            metadata = match.metadata or {}

            # Track valid labels
            if metadata.get("valid_labels"):
                for label in metadata["valid_labels"]:
                    valid_labels[label] += 1
                    all_labels_seen.add(label)

            # Track invalid labels
            if metadata.get("invalid_labels"):
                for label in metadata["invalid_labels"]:
                    invalid_labels[label] += 1
                    all_labels_seen.add(label)

        # Calculate success rate based on valid vs invalid labels
        total_label_instances = sum(valid_labels.values()) + sum(
            invalid_labels.values()
        )
        if total_label_instances == 0:
            success_rate = 0.0
        else:
            success_rate = sum(valid_labels.values()) / total_label_instances

        # Identify common and rare labels
        common_labels = {
            label
            for label, count in valid_labels.items()
            if count
            >= max(1, total_receipts * 0.5)  # Present in 50%+ of receipts
        }

        rarely_present_labels = {
            label
            for label in all_labels_seen
            if valid_labels[label]
            < max(1, total_receipts * 0.1)  # Present in <10% of receipts
        }

        # Basic receipt structure analysis (can be enhanced in Phase 2)
        typical_structure = {
            "total_receipts_analyzed": total_receipts,
            "common_label_types": list(common_labels),
            "average_labels_per_receipt": total_label_instances
            / max(1, total_receipts),
        }

        return MerchantReliabilityData(
            merchant_name=merchant_name,
            total_receipts_processed=total_receipts,
            pattern_only_success_rate=success_rate,
            common_labels=common_labels,
            rarely_present_labels=rarely_present_labels,
            typical_receipt_structure=typical_structure,
            last_updated=datetime.now(),
        )

    def _normalize_merchant_name(self, merchant_name: str) -> str:
        """Normalize merchant name for consistent Pinecone queries.

        Handles variations like "Walmart", "WAL-MART", "WALMART #1234".
        """
        if not merchant_name:
            return ""

        # Basic normalization - can be enhanced with more sophisticated logic
        normalized = merchant_name.upper().strip()

        # Common merchant name variations
        normalizations = {
            "WAL-MART": "WALMART",
            "WAL MART": "WALMART",
            "MCDONALDS": "MCDONALD'S",
            "MICKEY D'S": "MCDONALD'S",
            "MICKEY DS": "MCDONALD'S",
        }

        for variant, canonical in normalizations.items():
            if variant in normalized:
                normalized = canonical
                break

        # Remove store numbers and common suffixes
        # e.g., "WALMART #1234" -> "WALMART"
        import re

        normalized = re.sub(r"\s*#\d+.*$", "", normalized)
        normalized = re.sub(r"\s*STORE.*$", "", normalized)
        normalized = re.sub(r"\s*LOCATION.*$", "", normalized)

        return normalized.strip()

    async def validate_pattern_confidence(
        self,
        merchant_name: str,
        label_type: str,
        detected_value: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Validate pattern detection confidence using Pinecone historical data.

        Args:
            merchant_name: Merchant name
            label_type: Type of label (e.g., "GRAND_TOTAL", "PRODUCT_NAME")
            detected_value: The value detected by patterns
            context: Additional context (position, nearby words, etc.)

        Returns:
            Confidence boost (0.0 to 1.0) based on historical similarity
        """
        if not self.config.enable_pinecone_validation:
            return 0.0

        try:
            # Query for similar values of this label type for this merchant
            filter_criteria = {
                "merchant_name": self._normalize_merchant_name(merchant_name),
                "valid_labels": {"$in": [label_type]},
            }

            # Execute query to find similar patterns
            response = await self._execute_pinecone_query(
                filter=filter_criteria,
                top_k=20,  # Get top 20 similar instances
                include_metadata=True,
            )

            if not response or not response.matches:
                return 0.0

            # Analyze similarity to boost confidence
            # This is a simplified implementation - Phase 2 can enhance with
            # semantic similarity using actual embeddings
            confidence_boost = min(
                0.3, len(response.matches) / 20.0
            )  # Max 30% boost

            logger.debug(
                f"Pattern validation for {label_type}='{detected_value}' "
                f"at merchant '{merchant_name}': boost={confidence_boost:.2f}"
            )

            return confidence_boost

        except Exception as e:
            logger.error(f"Error in pattern validation: {e}")
            return 0.0

    def clear_cache(self) -> None:
        """Clear the reliability data cache."""
        self._reliability_cache.clear()
        logger.debug("Cleared Pinecone reliability cache")

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics for monitoring."""
        return {
            "cached_merchants": len(self._reliability_cache),
            "cache_ttl_hours": self._cache_ttl_hours,
            "merchants": list(self._reliability_cache.keys()),
        }
