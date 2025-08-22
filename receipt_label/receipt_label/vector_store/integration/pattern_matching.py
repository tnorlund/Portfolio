"""
Pattern matching integration with vector stores.

This module provides pattern matching capabilities using vector stores,
enabling storage and retrieval of merchant-specific patterns, product names,
and other receipt-related patterns.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..client.base import VectorStoreInterface

logger = logging.getLogger(__name__)


class PatternMatcher:
    """
    Pattern matching system integrated with vector stores.

    This class provides functionality for storing, retrieving, and matching
    patterns using vector similarity search, enabling intelligent pattern
    recognition for receipt processing.
    """

    def __init__(
        self,
        vector_client: VectorStoreInterface,
        patterns_collection: str = "receipt_patterns",
        similarity_threshold: float = 0.8,
    ):
        """
        Initialize the pattern matcher.

        Args:
            vector_client: Vector store client instance
            patterns_collection: Collection name for storing patterns
            similarity_threshold: Default similarity threshold for matches
        """
        self.client = vector_client
        self.patterns_collection = patterns_collection
        self.similarity_threshold = similarity_threshold

    def store_pattern(
        self,
        pattern_id: str,
        pattern_text: str,
        pattern_type: str,
        merchant_name: Optional[str] = None,
        confidence: float = 1.0,
        additional_metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Store a pattern in the vector store.

        Args:
            pattern_id: Unique identifier for the pattern
            pattern_text: The pattern text to store
            pattern_type: Type of pattern (e.g., "product_name", "merchant_name", "total")
            merchant_name: Optional merchant name for merchant-specific patterns
            confidence: Confidence level of the pattern (0.0 to 1.0)
            additional_metadata: Optional additional metadata

        Returns:
            True if stored successfully, False otherwise
        """
        try:
            # Build metadata
            metadata = {
                "pattern_type": pattern_type,
                "confidence": confidence,
                "created_at": self._get_timestamp(),
            }

            if merchant_name:
                metadata["merchant_name"] = merchant_name

            if additional_metadata:
                metadata.update(additional_metadata)

            # Store the pattern
            self.client.upsert_vectors(
                collection_name=self.patterns_collection,
                ids=[pattern_id],
                documents=[pattern_text],
                metadatas=[metadata],
            )

            logger.debug("Stored pattern '%s': %s", pattern_id, pattern_text)
            return True

        except Exception as e:
            logger.error("Error storing pattern '%s': %s", pattern_id, e)
            return False

    def find_similar_patterns(
        self,
        query_text: str,
        pattern_type: Optional[str] = None,
        merchant_name: Optional[str] = None,
        n_results: int = 10,
        similarity_threshold: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Find patterns similar to the query text.

        Args:
            query_text: Text to search for similar patterns
            pattern_type: Optional pattern type filter
            merchant_name: Optional merchant name filter
            n_results: Maximum number of results to return
            similarity_threshold: Similarity threshold (uses default if None)

        Returns:
            List of matching patterns with metadata and similarity scores
        """
        try:
            if not self.client.collection_exists(self.patterns_collection):
                logger.debug(
                    "Patterns collection does not exist: %s",
                    self.patterns_collection,
                )
                return []

            # Build filter conditions
            where_filter = {}
            if pattern_type:
                where_filter["pattern_type"] = pattern_type
            if merchant_name:
                where_filter["merchant_name"] = merchant_name

            # Query for similar patterns
            query_results = self.client.query(
                collection_name=self.patterns_collection,
                query_texts=[query_text],
                n_results=n_results,
                where=where_filter if where_filter else None,
                include=["metadatas", "documents", "distances"],
            )

            if (
                not query_results.get("distances")
                or not query_results["distances"][0]
            ):
                return []

            # Process results and apply similarity threshold
            threshold = similarity_threshold or self.similarity_threshold
            matches = []

            for i, distance in enumerate(query_results["distances"][0]):
                similarity = 1.0 - distance  # Convert distance to similarity

                if similarity >= threshold:
                    match = {
                        "pattern_id": query_results["ids"][0][i],
                        "pattern_text": query_results["documents"][0][i],
                        "similarity": similarity,
                        "distance": distance,
                        "metadata": query_results["metadatas"][0][i],
                    }
                    matches.append(match)

            # Sort by similarity (highest first)
            matches.sort(key=lambda x: x["similarity"], reverse=True)

            logger.debug(
                "Found %d similar patterns for query: %s",
                len(matches),
                query_text,
            )
            return matches

        except Exception as e:
            logger.error("Error finding similar patterns: %s", e)
            return []

    def get_best_pattern_match(
        self,
        query_text: str,
        pattern_type: Optional[str] = None,
        merchant_name: Optional[str] = None,
        similarity_threshold: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get the best matching pattern for the query text.

        Args:
            query_text: Text to search for patterns
            pattern_type: Optional pattern type filter
            merchant_name: Optional merchant name filter
            similarity_threshold: Similarity threshold (uses default if None)

        Returns:
            Best matching pattern or None if no good matches found
        """
        matches = self.find_similar_patterns(
            query_text=query_text,
            pattern_type=pattern_type,
            merchant_name=merchant_name,
            n_results=1,
            similarity_threshold=similarity_threshold,
        )

        return matches[0] if matches else None

    def store_merchant_patterns(
        self,
        merchant_name: str,
        patterns: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Store multiple patterns for a merchant.

        Args:
            merchant_name: Name of the merchant
            patterns: List of pattern dictionaries with keys:
                     - text: Pattern text
                     - type: Pattern type
                     - confidence: Optional confidence (default 1.0)
                     - metadata: Optional additional metadata

        Returns:
            Dict with storage results
        """
        stored_count = 0
        failed_count = 0
        failed_patterns = []

        for i, pattern in enumerate(patterns):
            pattern_text = pattern.get("text", "")
            pattern_type = pattern.get("type", "unknown")
            confidence = pattern.get("confidence", 1.0)
            metadata = pattern.get("metadata", {})

            if not pattern_text:
                failed_count += 1
                failed_patterns.append({"index": i, "reason": "empty_text"})
                continue

            # Generate pattern ID
            pattern_id = (
                f"{merchant_name}_{pattern_type}_{i}_{self._get_timestamp()}"
            )

            success = self.store_pattern(
                pattern_id=pattern_id,
                pattern_text=pattern_text,
                pattern_type=pattern_type,
                merchant_name=merchant_name,
                confidence=confidence,
                additional_metadata=metadata,
            )

            if success:
                stored_count += 1
            else:
                failed_count += 1
                failed_patterns.append({"index": i, "reason": "storage_error"})

        result = {
            "merchant_name": merchant_name,
            "total_patterns": len(patterns),
            "stored_successfully": stored_count,
            "failed_to_store": failed_count,
            "success_rate": stored_count / len(patterns) if patterns else 0.0,
        }

        if failed_patterns:
            result["failed_patterns"] = failed_patterns

        logger.info(
            "Stored merchant patterns for '%s': %d/%d successful",
            merchant_name,
            stored_count,
            len(patterns),
        )

        return result

    def get_merchant_patterns(
        self,
        merchant_name: str,
        pattern_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get all patterns for a specific merchant.

        Args:
            merchant_name: Name of the merchant
            pattern_type: Optional pattern type filter
            limit: Maximum number of patterns to return

        Returns:
            List of merchant patterns
        """
        try:
            if not self.client.collection_exists(self.patterns_collection):
                return []

            # Build filter
            where_filter = {"merchant_name": merchant_name}
            if pattern_type:
                where_filter["pattern_type"] = pattern_type

            # Query all patterns for the merchant
            # Note: This is a workaround since most vector stores don't have a direct
            # "get all with filter" method. We use a query with a generic term.
            query_results = self.client.query(
                collection_name=self.patterns_collection,
                query_texts=[merchant_name],  # Use merchant name as query
                n_results=limit,
                where=where_filter,
                include=["metadatas", "documents"],
            )

            if not query_results.get("ids") or not query_results["ids"][0]:
                return []

            patterns = []
            for i, pattern_id in enumerate(query_results["ids"][0]):
                pattern = {
                    "pattern_id": pattern_id,
                    "pattern_text": query_results["documents"][0][i],
                    "metadata": query_results["metadatas"][0][i],
                }
                patterns.append(pattern)

            return patterns

        except Exception as e:
            logger.error("Error getting merchant patterns: %s", e)
            return []

    def validate_text_against_patterns(
        self,
        text: str,
        pattern_type: str,
        merchant_name: Optional[str] = None,
        similarity_threshold: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Validate text against known patterns.

        Args:
            text: Text to validate
            pattern_type: Type of pattern to validate against
            merchant_name: Optional merchant name for merchant-specific validation
            similarity_threshold: Similarity threshold for validation

        Returns:
            Dict with validation results
        """
        try:
            best_match = self.get_best_pattern_match(
                query_text=text,
                pattern_type=pattern_type,
                merchant_name=merchant_name,
                similarity_threshold=similarity_threshold,
            )

            if best_match:
                return {
                    "is_valid": True,
                    "confidence": best_match["similarity"],
                    "matched_pattern": best_match["pattern_text"],
                    "pattern_id": best_match["pattern_id"],
                    "pattern_metadata": best_match["metadata"],
                }
            else:
                return {
                    "is_valid": False,
                    "reason": "no_matching_patterns",
                    "confidence": 0.0,
                }

        except Exception as e:
            logger.error("Error validating text against patterns: %s", e)
            return {
                "is_valid": False,
                "reason": "validation_error",
                "error": str(e),
                "confidence": 0.0,
            }

    def update_pattern_confidence(
        self,
        pattern_id: str,
        new_confidence: float,
        usage_count: Optional[int] = None,
    ) -> bool:
        """
        Update the confidence score of a pattern based on usage feedback.

        Args:
            pattern_id: ID of the pattern to update
            new_confidence: New confidence score (0.0 to 1.0)
            usage_count: Optional usage count to track pattern popularity

        Returns:
            True if updated successfully, False otherwise
        """
        try:
            # Get existing pattern
            existing = self.client.get_by_ids(
                collection_name=self.patterns_collection,
                ids=[pattern_id],
                include=["metadatas", "documents"],
            )

            if not existing.get("ids") or pattern_id not in existing["ids"]:
                logger.warning(
                    "Pattern not found for confidence update: %s", pattern_id
                )
                return False

            # Update metadata
            idx = existing["ids"].index(pattern_id)
            metadata = existing["metadatas"][idx].copy()
            metadata["confidence"] = new_confidence
            metadata["last_updated"] = self._get_timestamp()

            if usage_count is not None:
                metadata["usage_count"] = usage_count

            # Re-store the pattern with updated metadata
            self.client.upsert_vectors(
                collection_name=self.patterns_collection,
                ids=[pattern_id],
                documents=[existing["documents"][idx]],
                metadatas=[metadata],
            )

            logger.debug(
                "Updated pattern confidence '%s': %.2f",
                pattern_id,
                new_confidence,
            )
            return True

        except Exception as e:
            logger.error("Error updating pattern confidence: %s", e)
            return False

    def delete_patterns(
        self,
        pattern_ids: List[str],
    ) -> Dict[str, Any]:
        """
        Delete patterns from the vector store.

        Args:
            pattern_ids: List of pattern IDs to delete

        Returns:
            Dict with deletion results
        """
        try:
            self.client.delete(
                collection_name=self.patterns_collection,
                ids=pattern_ids,
            )

            logger.info("Deleted %d patterns", len(pattern_ids))
            return {
                "status": "success",
                "deleted_count": len(pattern_ids),
                "deleted_pattern_ids": pattern_ids,
            }

        except Exception as e:
            logger.error("Error deleting patterns: %s", e)
            return {
                "status": "error",
                "error": str(e),
                "deleted_count": 0,
            }

    def get_pattern_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about stored patterns.

        Returns:
            Dict with pattern statistics
        """
        try:
            if not self.client.collection_exists(self.patterns_collection):
                return {
                    "total_patterns": 0,
                    "collection_exists": False,
                }

            total_count = self.client.count(self.patterns_collection)

            # Get a sample of patterns to analyze types and merchants
            sample_results = self.client.query(
                collection_name=self.patterns_collection,
                query_texts=["sample"],  # Generic query
                n_results=min(1000, total_count),  # Sample up to 1000 patterns
                include=["metadatas"],
            )

            pattern_types = set()
            merchants = set()
            avg_confidence = 0.0
            confidence_count = 0

            if (
                sample_results.get("metadatas")
                and sample_results["metadatas"][0]
            ):
                for metadata in sample_results["metadatas"][0]:
                    if isinstance(metadata, dict):
                        if "pattern_type" in metadata:
                            pattern_types.add(metadata["pattern_type"])
                        if "merchant_name" in metadata:
                            merchants.add(metadata["merchant_name"])
                        if "confidence" in metadata:
                            avg_confidence += metadata["confidence"]
                            confidence_count += 1

            return {
                "total_patterns": total_count,
                "collection_exists": True,
                "unique_pattern_types": len(pattern_types),
                "pattern_types": list(pattern_types),
                "unique_merchants": len(merchants),
                "sample_merchants": list(merchants)[:10],  # First 10 merchants
                "average_confidence": (
                    avg_confidence / confidence_count
                    if confidence_count > 0
                    else 0.0
                ),
                "similarity_threshold": self.similarity_threshold,
            }

        except Exception as e:
            logger.error("Error getting pattern statistics: %s", e)
            return {
                "total_patterns": 0,
                "collection_exists": False,
                "error": str(e),
            }

    def _get_timestamp(self) -> str:
        """Get current timestamp as ISO string."""
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()
