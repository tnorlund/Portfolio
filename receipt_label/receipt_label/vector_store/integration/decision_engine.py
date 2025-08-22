"""
Vector store integration for decision engines.

This module provides integration between vector stores and decision engines,
migrating functionality from the old chroma_integration.py file.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from ..client.base import VectorStoreInterface

logger = logging.getLogger(__name__)


class MerchantReliabilityData:
    """Data structure for merchant reliability metrics."""

    def __init__(
        self,
        merchant_name: str,
        pattern_only_success_rate: float = 0.0,
        total_receipts_processed: int = 0,
        successful_pattern_matches: int = 0,
        failed_pattern_matches: int = 0,
        last_updated: Optional[datetime] = None,
        common_patterns: Optional[Dict[str, Any]] = None,
    ):
        self.merchant_name = merchant_name
        self.pattern_only_success_rate = pattern_only_success_rate
        self.total_receipts_processed = total_receipts_processed
        self.successful_pattern_matches = successful_pattern_matches
        self.failed_pattern_matches = failed_pattern_matches
        self.last_updated = last_updated or datetime.now()
        self.common_patterns = common_patterns or {}


class VectorDecisionEngine:
    """
    Vector store integration for decision engines.

    This class provides decision engine functionality using vector stores,
    including merchant reliability lookup and pattern validation.
    """

    def __init__(
        self,
        vector_client: VectorStoreInterface,
        collection_name: str = "merchant_patterns",
        enable_vector_validation: bool = True,
        cache_ttl_hours: int = 24,
    ):
        """
        Initialize the vector decision engine.

        Args:
            vector_client: Vector store client instance
            collection_name: Collection name for merchant patterns
            enable_vector_validation: Whether to enable vector-based validation
            cache_ttl_hours: Cache TTL for merchant data (default: 24 hours)
        """
        self.client = vector_client
        self.collection_name = collection_name
        self.enable_vector_validation = enable_vector_validation
        self.cache_ttl_hours = cache_ttl_hours

        # Internal cache for merchant reliability data
        self._reliability_cache: Dict[str, MerchantReliabilityData] = {}

    async def get_merchant_reliability(
        self,
        merchant_name: str,
        refresh_cache: bool = False,
    ) -> Optional[MerchantReliabilityData]:
        """
        Get merchant reliability data from the vector store.

        Args:
            merchant_name: Name of the merchant to look up
            refresh_cache: Whether to refresh cached data

        Returns:
            MerchantReliabilityData if merchant found, None otherwise
        """
        if not self.enable_vector_validation:
            logger.debug(
                "Vector validation disabled, skipping merchant lookup"
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
                hours=self.cache_ttl_hours
            ):
                logger.debug(
                    "Using cached reliability data for merchant: %s",
                    merchant_name,
                )
                return cached_data

        try:
            # Query vector store for merchant-specific data
            reliability_data = await self._query_merchant_data(merchant_name)

            if reliability_data:
                # Cache the result
                self._reliability_cache[cache_key] = reliability_data
                logger.debug(
                    "Retrieved reliability data for merchant '%s': "
                    "success_rate=%.2f, receipts=%d",
                    merchant_name,
                    reliability_data.pattern_only_success_rate,
                    reliability_data.total_receipts_processed,
                )

            return reliability_data

        except Exception as e:
            logger.error(
                "Error querying vector store for merchant '%s': %s",
                merchant_name,
                e,
            )
            return None

    async def _query_merchant_data(
        self,
        merchant_name: str,
    ) -> Optional[MerchantReliabilityData]:
        """
        Query vector store for merchant-specific pattern data.

        This method analyzes historical receipt data for the merchant to calculate
        reliability metrics.

        Args:
            merchant_name: Name of the merchant

        Returns:
            MerchantReliabilityData if found, None otherwise
        """
        try:
            if not self.client.collection_exists(self.collection_name):
                logger.debug(
                    "Merchant patterns collection does not exist: %s",
                    self.collection_name,
                )
                return None

            # Query for merchant-specific patterns using metadata filtering
            query_results = self.client.query(
                collection_name=self.collection_name,
                query_texts=[merchant_name],
                n_results=50,  # Get top 50 matches
                where={"merchant_name": merchant_name},
                include=["metadatas", "documents", "distances"],
            )

            if not query_results.get("ids") or not query_results["ids"][0]:
                logger.debug(
                    "No patterns found for merchant: %s", merchant_name
                )
                return None

            # Analyze the results to calculate reliability metrics
            return self._analyze_merchant_patterns(
                merchant_name, query_results
            )

        except Exception as e:
            logger.error("Error in merchant data query: %s", e)
            return None

    def _analyze_merchant_patterns(
        self,
        merchant_name: str,
        query_results: Dict[str, Any],
    ) -> Optional[MerchantReliabilityData]:
        """
        Analyze query results to calculate merchant reliability metrics.

        Args:
            merchant_name: Name of the merchant
            query_results: Results from vector store query

        Returns:
            MerchantReliabilityData with calculated metrics
        """
        try:
            metadatas = query_results.get("metadatas", [])
            if not metadatas or not metadatas[0]:
                return None

            # Aggregate pattern success/failure data
            successful_matches = 0
            failed_matches = 0
            total_receipts = 0
            pattern_types = defaultdict(int)

            for metadata_list in metadatas:
                for metadata in metadata_list:
                    if not isinstance(metadata, dict):
                        continue

                    # Count successful pattern matches
                    if metadata.get("pattern_successful"):
                        successful_matches += 1
                    else:
                        failed_matches += 1

                    # Track receipt counts
                    receipt_count = metadata.get("receipt_count", 1)
                    total_receipts += receipt_count

                    # Track pattern types
                    pattern_type = metadata.get("pattern_type", "unknown")
                    pattern_types[pattern_type] += 1

            # Calculate success rate
            total_attempts = successful_matches + failed_matches
            success_rate = (
                successful_matches / total_attempts
                if total_attempts > 0
                else 0.0
            )

            # Build common patterns info
            common_patterns = {
                "pattern_types": dict(pattern_types),
                "most_common_pattern": (
                    max(pattern_types, key=pattern_types.get)
                    if pattern_types
                    else None
                ),
                "total_pattern_types": len(pattern_types),
            }

            return MerchantReliabilityData(
                merchant_name=merchant_name,
                pattern_only_success_rate=success_rate,
                total_receipts_processed=total_receipts,
                successful_pattern_matches=successful_matches,
                failed_pattern_matches=failed_matches,
                last_updated=datetime.now(),
                common_patterns=common_patterns,
            )

        except Exception as e:
            logger.error("Error analyzing merchant patterns: %s", e)
            return None

    async def store_merchant_pattern(
        self,
        merchant_name: str,
        pattern_text: str,
        pattern_type: str,
        success: bool,
        receipt_count: int = 1,
        additional_metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Store a merchant pattern result in the vector store.

        Args:
            merchant_name: Name of the merchant
            pattern_text: The pattern text that was matched
            pattern_type: Type of pattern (e.g., "total", "date", "merchant_name")
            success: Whether the pattern match was successful
            receipt_count: Number of receipts this pattern applies to
            additional_metadata: Optional additional metadata

        Returns:
            True if stored successfully, False otherwise
        """
        try:
            if not self.enable_vector_validation:
                return False

            # Create metadata for the pattern
            metadata = {
                "merchant_name": merchant_name,
                "pattern_type": pattern_type,
                "pattern_successful": success,
                "receipt_count": receipt_count,
                "timestamp": datetime.now().isoformat(),
            }

            if additional_metadata:
                metadata.update(additional_metadata)

            # Generate a unique ID for this pattern
            pattern_id = (
                f"{merchant_name}_{pattern_type}_{datetime.now().timestamp()}"
            )

            # Store in vector store
            self.client.upsert_vectors(
                collection_name=self.collection_name,
                ids=[pattern_id],
                documents=[pattern_text],
                metadatas=[metadata],
            )

            logger.debug(
                "Stored pattern for merchant '%s': %s",
                merchant_name,
                pattern_type,
            )
            return True

        except Exception as e:
            logger.error("Error storing merchant pattern: %s", e)
            return False

    async def validate_pattern_against_merchant(
        self,
        merchant_name: str,
        pattern_text: str,
        pattern_type: str,
        similarity_threshold: float = 0.8,
    ) -> Dict[str, Any]:
        """
        Validate a pattern against known merchant patterns.

        Args:
            merchant_name: Name of the merchant
            pattern_text: Pattern text to validate
            pattern_type: Type of pattern to validate
            similarity_threshold: Similarity threshold for matches

        Returns:
            Dict with validation results
        """
        try:
            if not self.enable_vector_validation:
                return {
                    "validated": False,
                    "reason": "vector_validation_disabled",
                }

            if not self.client.collection_exists(self.collection_name):
                return {"validated": False, "reason": "no_pattern_collection"}

            # Query for similar patterns from this merchant
            query_results = self.client.query(
                collection_name=self.collection_name,
                query_texts=[pattern_text],
                n_results=10,
                where={
                    "merchant_name": merchant_name,
                    "pattern_type": pattern_type,
                },
                include=["metadatas", "documents", "distances"],
            )

            if (
                not query_results.get("distances")
                or not query_results["distances"][0]
            ):
                return {"validated": False, "reason": "no_similar_patterns"}

            # Find the best match
            distances = query_results["distances"][0]
            best_distance = min(distances)
            best_similarity = (
                1.0 - best_distance
            )  # Convert distance to similarity

            if best_similarity >= similarity_threshold:
                best_idx = distances.index(best_distance)
                best_metadata = query_results["metadatas"][0][best_idx]

                return {
                    "validated": True,
                    "similarity": best_similarity,
                    "confidence": best_similarity,
                    "matching_pattern": query_results["documents"][0][
                        best_idx
                    ],
                    "pattern_successful": best_metadata.get(
                        "pattern_successful", False
                    ),
                    "historical_success_rate": best_metadata.get(
                        "pattern_successful", 0
                    ),
                }
            else:
                return {
                    "validated": False,
                    "reason": "similarity_below_threshold",
                    "best_similarity": best_similarity,
                    "threshold": similarity_threshold,
                }

        except Exception as e:
            logger.error("Error validating pattern: %s", e)
            return {
                "validated": False,
                "reason": "query_error",
                "error": str(e),
            }

    def clear_merchant_cache(
        self, merchant_name: Optional[str] = None
    ) -> None:
        """
        Clear merchant reliability cache.

        Args:
            merchant_name: Specific merchant to clear, or None to clear all
        """
        if merchant_name:
            cache_key = merchant_name.lower().strip()
            self._reliability_cache.pop(cache_key, None)
            logger.debug("Cleared cache for merchant: %s", merchant_name)
        else:
            self._reliability_cache.clear()
            logger.debug("Cleared all merchant cache")

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dict with cache statistics
        """
        return {
            "cached_merchants": len(self._reliability_cache),
            "cache_ttl_hours": self.cache_ttl_hours,
            "merchants": list(self._reliability_cache.keys()),
        }
