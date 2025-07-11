"""
Lambda handler for checking receipt embeddings in Pinecone.

This handler verifies that receipt word embeddings exist in Pinecone
before proceeding with merchant-specific pattern matching.
"""

import logging
import os
from typing import Any, Dict

from pinecone import Pinecone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize Pinecone
pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
index_name = os.environ["PINECONE_INDEX_NAME"]
index = pc.Index(index_name)


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    Check if receipt has embeddings in Pinecone.

    Args:
        event: Contains receipt_id and optional merchant_name
        context: Lambda context

    Returns:
        Dictionary with:
        - has_embeddings: Boolean indicating if embeddings exist
        - embedding_count: Number of embeddings found
        - merchant_patterns: List of similar merchant patterns if available
    """
    try:
        receipt_id = event["receipt_id"]
        merchant_name = event.get("metadata", {}).get("merchant_name")

        logger.info("Checking embeddings for receipt: %s", receipt_id)

        # Check if receipt embeddings exist
        # Namespace format: receipt_{receipt_id}
        namespace = f"receipt_{receipt_id}"

        # Query for a sample embedding to verify namespace exists
        try:
            stats = index.describe_index_stats()
            namespace_stats = stats.get("namespaces", {}).get(namespace, {})
            embedding_count = namespace_stats.get("vector_count", 0)

            has_embeddings = embedding_count > 0
        except Exception as e:
            logger.warning("Error checking namespace stats: %s", e)
            has_embeddings = False
            embedding_count = 0

        result = {
            "has_embeddings": has_embeddings,
            "embedding_count": embedding_count,
            "merchant_patterns": [],
        }

        # If merchant name is known, search for similar patterns
        if has_embeddings and merchant_name:
            try:
                # Query for merchant-specific patterns
                # This would typically use the merchant's embedding space
                # For now, we'll return a placeholder
                logger.info(
                    "Searching for patterns for merchant: %s", merchant_name
                )

                # In production, this would query a merchant-specific namespace
                # or use metadata filtering to find relevant patterns
                result["merchant_patterns"] = [
                    {
                        "pattern_type": "date_format",
                        "confidence": 0.95,
                        "example": "MM/DD/YYYY",
                    },
                    {
                        "pattern_type": "total_location",
                        "confidence": 0.90,
                        "example": "bottom_right",
                    },
                ]
            except Exception as e:
                logger.warning("Error searching merchant patterns: %s", e)

        logger.info("Embedding check result: %s", result)
        return result

    except Exception as e:
        logger.error("Error checking embeddings: %s", str(e))
        raise
