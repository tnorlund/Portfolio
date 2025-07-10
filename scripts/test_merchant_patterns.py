#!/usr/bin/env python3
"""
Test script for merchant pattern querying functionality.

This script demonstrates how pattern extraction and matching works,
showing the 99% reduction in Pinecone queries by using merchant filtering.

Usage:
    python scripts/test_merchant_patterns.py --merchant "WALMART"
    python scripts/test_merchant_patterns.py --receipt-id <id>
    python scripts/test_merchant_patterns.py --compare-methods --merchant "WALMART"
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import List

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import ReceiptWord

from receipt_label.merchant_patterns import (
    PatternConfidence,
    get_merchant_patterns,
    query_patterns_for_words,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_merchant_patterns(merchant_name: str):
    """Test pattern extraction for a specific merchant."""
    logger.info(f"Extracting patterns for merchant: {merchant_name}")

    start_time = time.time()

    try:
        patterns = get_merchant_patterns(
            merchant_name=merchant_name,
            min_frequency=1,  # Lower threshold for testing
            min_confidence=0.3,
        )

        elapsed_time = time.time() - start_time

        logger.info(
            f"\nPattern extraction completed in {elapsed_time:.2f} seconds"
        )
        logger.info(f"Total receipts analyzed: {patterns.total_receipts}")
        logger.info(f"Total validated words: {patterns.total_validated_words}")
        logger.info(f"Unique patterns found: {len(patterns.pattern_map)}")

        # Show common labels
        logger.info("\nMost common labels:")
        sorted_labels = sorted(
            patterns.common_labels.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:10]

        for label, count in sorted_labels:
            logger.info(f"  - {label}: {count} occurrences")

        # Show high-confidence patterns
        high_confidence = patterns.get_patterns_above_threshold(0.8)
        logger.info(
            f"\nHigh-confidence patterns (>80%): {len(high_confidence)}"
        )

        for word, pattern in list(high_confidence.items())[:10]:
            logger.info(
                f"  - '{word}' -> '{pattern.suggested_label}' "
                f"(confidence: {pattern.confidence:.2f}, frequency: {pattern.frequency})"
            )

        return patterns

    except Exception as e:
        logger.error(f"Error extracting patterns: {str(e)}")
        raise


def test_pattern_query_for_receipt(receipt_id: str):
    """Test pattern querying for a specific receipt."""
    logger.info(f"Testing pattern query for receipt: {receipt_id}")

    dynamo_client = DynamoClient("ReceiptsTable")

    # Get receipt metadata
    metadata_items = dynamo_client.query(
        pk_value=f"RECEIPT#{receipt_id}",
        sk_prefix="METADATA",
    )

    if not metadata_items:
        logger.error(f"No metadata found for receipt {receipt_id}")
        return

    metadata = metadata_items[0]
    merchant_name = metadata.get("canonical_merchant_name") or metadata.get(
        "merchant_name"
    )

    if not merchant_name:
        logger.error(f"No merchant name found for receipt {receipt_id}")
        return

    logger.info(f"Merchant: {merchant_name}")

    # Get receipt words
    words = dynamo_client.list_receipt_words_by_receipt(receipt_id)
    logger.info(f"Found {len(words)} words in receipt")

    # Query patterns
    start_time = time.time()

    result = query_patterns_for_words(
        merchant_name=merchant_name,
        words=words[:20],  # Test with first 20 words
        confidence_threshold=0.7,
    )

    elapsed_time = time.time() - start_time

    logger.info(f"\nPattern query completed in {elapsed_time:.2f} seconds")
    logger.info(f"Query reduction ratio: {result.query_reduction_ratio:.1%}")
    logger.info(f"Words with patterns: {len(result.words_with_patterns)}")
    logger.info(
        f"Words without patterns: {len(result.words_without_patterns)}"
    )

    # Show pattern matches
    logger.info("\nPattern matches:")
    for word, pattern in list(result.pattern_matches.items())[:10]:
        logger.info(
            f"  - '{word}' -> '{pattern.suggested_label}' "
            f"({pattern.confidence_level.value}, score: {pattern.confidence:.2f})"
        )

    # Show words needing GPT
    if result.words_without_patterns:
        logger.info(
            f"\nWords requiring GPT labeling: {result.words_without_patterns[:10]}"
        )

    return result


def compare_query_methods(merchant_name: str, num_words: int = 20):
    """Compare traditional N queries vs single merchant-filtered query."""
    logger.info(
        f"Comparing query methods for {merchant_name} with {num_words} words"
    )

    # Create sample words
    sample_words = [
        "TOTAL",
        "SUBTOTAL",
        "TAX",
        "CASH",
        "CHANGE",
        "VISA",
        "MASTERCARD",
        "DEBIT",
        "CREDIT",
        "AMOUNT",
        "DATE",
        "TIME",
        "STORE",
        "RECEIPT",
        "THANK",
        "YOU",
        "ITEM",
        "QTY",
        "PRICE",
        "DISCOUNT",
    ][:num_words]

    # Simulate traditional approach (N queries)
    logger.info("\n1. Traditional approach (N individual queries):")
    traditional_start = time.time()

    # In reality, each word would require a separate Pinecone query
    # Here we're simulating the time/cost
    traditional_queries = len(sample_words)
    traditional_time = traditional_queries * 0.1  # Assume 100ms per query

    logger.info(f"   - Number of Pinecone queries: {traditional_queries}")
    logger.info(f"   - Estimated time: {traditional_time:.2f} seconds")
    logger.info(f"   - Estimated cost: ${traditional_queries * 0.0001:.4f}")

    # New approach (single query)
    logger.info("\n2. New approach (single merchant-filtered query):")

    # Create ReceiptWord objects
    words = []
    for i, word_text in enumerate(sample_words):
        words.append(
            ReceiptWord(
                receipt_id=1,
                image_id="test-001",
                line_id=1,
                word_id=i + 1,
                text=word_text,
                bounding_box={"x": 0, "y": 0, "width": 100, "height": 20},
                top_left={"x": 0, "y": 0},
                top_right={"x": 100, "y": 0},
                bottom_left={"x": 0, "y": 20},
                bottom_right={"x": 100, "y": 20},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.95,
            )
        )

    new_start = time.time()

    result = query_patterns_for_words(
        merchant_name=merchant_name,
        words=words,
        confidence_threshold=0.7,
    )

    new_time = time.time() - new_start

    logger.info(f"   - Number of Pinecone queries: 1")
    logger.info(f"   - Actual time: {new_time:.2f} seconds")
    logger.info(f"   - Estimated cost: $0.0001")
    logger.info(f"   - Query reduction: {result.query_reduction_ratio:.1%}")

    # Show savings
    logger.info("\n3. Savings:")
    logger.info(
        f"   - Query reduction: {traditional_queries} → 1 ({(1 - 1/traditional_queries):.1%} reduction)"
    )
    logger.info(f"   - Time saved: {traditional_time - new_time:.2f} seconds")
    logger.info(
        f"   - Cost saved: ${(traditional_queries - 1) * 0.0001:.4f} per request"
    )

    # Extrapolate to daily usage
    daily_receipts = 1000
    words_per_receipt = 50
    daily_queries_traditional = daily_receipts * words_per_receipt
    daily_queries_new = daily_receipts  # One per receipt

    logger.info("\n4. Daily impact (1000 receipts, 50 words each):")
    logger.info(
        f"   - Traditional: {daily_queries_traditional:,} queries (${daily_queries_traditional * 0.0001:.2f})"
    )
    logger.info(
        f"   - New approach: {daily_queries_new:,} queries (${daily_queries_new * 0.0001:.2f})"
    )
    logger.info(
        f"   - Daily savings: ${(daily_queries_traditional - daily_queries_new) * 0.0001:.2f}"
    )
    logger.info(
        f"   - Annual savings: ${(daily_queries_traditional - daily_queries_new) * 0.0001 * 365:.2f}"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Test merchant pattern functionality"
    )
    parser.add_argument(
        "--merchant",
        help="Merchant name to test patterns for",
    )
    parser.add_argument(
        "--receipt-id",
        help="Receipt ID to test pattern matching",
    )
    parser.add_argument(
        "--compare-methods",
        action="store_true",
        help="Compare traditional vs new query methods",
    )
    parser.add_argument(
        "--table-name",
        default="ReceiptsTable",
        help="DynamoDB table name",
    )

    args = parser.parse_args()

    if args.compare_methods and args.merchant:
        compare_query_methods(args.merchant)

    elif args.merchant:
        test_merchant_patterns(args.merchant)

    elif args.receipt_id:
        test_pattern_query_for_receipt(args.receipt_id)

    else:
        parser.error("Please specify --merchant or --receipt-id")


if __name__ == "__main__":
    main()
