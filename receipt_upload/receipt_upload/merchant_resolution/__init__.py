"""
Merchant resolution module for receipt processing.

This module provides two-tier merchant resolution:
1. Tier 1 (Vector Similarity): Query the DynamoDB line-embeddings index by
   embedding similarity, then compare normalized metadata (phone, address)
   to boost confidence. Handles OCR errors like "Westlake" vs "Mestlake".
2. Tier 2 (Fallback): Use Place ID Finder agent to search Google Places API

Usage:
    from receipt_upload.merchant_resolution import (
        MerchantResolver,
        MerchantResolvingEmbeddingProcessor,
        MerchantResult,
        SimilarityMatch,
    )

    # Use the resolver directly
    resolver = MerchantResolver(dynamo_client, places_client)
    result = resolver.resolve(None, lines, words, image_id, receipt_id)

    # Or use the full embedding processor
    processor = MerchantResolvingEmbeddingProcessor(
        table_name="my-table",
    )
    result = processor.process_embeddings(image_id, receipt_id, lines, words)
"""

from receipt_upload.merchant_resolution.embedding_processor import (
    MerchantResolvingEmbeddingProcessor,
)
from receipt_upload.merchant_resolution.resolver import (
    MerchantResolver,
    MerchantResult,
    SimilarityMatch,
    merchant_name_matches_receipt,
    tokenize_text,
)

__all__ = [
    "MerchantResolver",
    "MerchantResolvingEmbeddingProcessor",
    "MerchantResult",
    "SimilarityMatch",
    "merchant_name_matches_receipt",
    "tokenize_text",
]
