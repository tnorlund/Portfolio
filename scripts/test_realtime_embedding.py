#!/usr/bin/env python3
"""
Test script for real-time embedding functionality.

This script demonstrates how to use the real-time embedding module
to process receipts immediately without waiting for batch processing.

Usage:
    python scripts/test_realtime_embedding.py --receipt-id <id>
    python scripts/test_realtime_embedding.py --image-id <id>
    python scripts/test_realtime_embedding.py --test-sample
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import List, Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import ReceiptWord

from receipt_label.embedding.realtime.embed import (
    EmbeddingContext,
    embed_receipt_realtime,
    embed_words_realtime_simple,
)
from receipt_label.embedding.word.realtime import embed_words_realtime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_sample_words() -> List[ReceiptWord]:
    """Create sample receipt words for testing."""
    return [
        ReceiptWord(
            receipt_id=1,
            image_id="test-realtime-001",
            line_id=1,
            word_id=1,
            text="WALMART",
            bounding_box={"x": 0, "y": 0, "width": 100, "height": 20},
            top_left={"x": 0, "y": 0},
            top_right={"x": 100, "y": 0},
            bottom_left={"x": 0, "y": 20},
            bottom_right={"x": 100, "y": 20},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
            embedding_status=EmbeddingStatus.NONE,
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-realtime-001",
            line_id=1,
            word_id=2,
            text="SUPERCENTER",
            bounding_box={"x": 120, "y": 0, "width": 120, "height": 20},
            top_left={"x": 120, "y": 0},
            top_right={"x": 240, "y": 0},
            bottom_left={"x": 120, "y": 20},
            bottom_right={"x": 240, "y": 20},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.94,
            embedding_status=EmbeddingStatus.NONE,
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-realtime-001",
            line_id=2,
            word_id=1,
            text="TOTAL",
            bounding_box={"x": 0, "y": 40, "width": 60, "height": 20},
            top_left={"x": 0, "y": 40},
            top_right={"x": 60, "y": 40},
            bottom_left={"x": 0, "y": 60},
            bottom_right={"x": 60, "y": 60},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.98,
            embedding_status=EmbeddingStatus.NONE,
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-realtime-001",
            line_id=2,
            word_id=2,
            text="$15.99",
            bounding_box={"x": 80, "y": 40, "width": 60, "height": 20},
            top_left={"x": 80, "y": 40},
            top_right={"x": 140, "y": 40},
            bottom_left={"x": 80, "y": 60},
            bottom_right={"x": 140, "y": 60},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.97,
            embedding_status=EmbeddingStatus.NONE,
        ),
        # Add some noise words to test filtering
        ReceiptWord(
            receipt_id=1,
            image_id="test-realtime-001",
            line_id=3,
            word_id=1,
            text="---",
            bounding_box={"x": 0, "y": 80, "width": 200, "height": 5},
            top_left={"x": 0, "y": 80},
            top_right={"x": 200, "y": 80},
            bottom_left={"x": 0, "y": 85},
            bottom_right={"x": 200, "y": 85},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.65,
            embedding_status=EmbeddingStatus.NONE,
            is_noise=True,
        ),
    ]


def test_embedding_with_context():
    """Test embedding with merchant context."""
    logger.info("Testing real-time embedding with merchant context...")

    words = create_sample_words()

    # Create context with merchant information
    context = EmbeddingContext(
        receipt_id="test-receipt-001",
        image_id="test-image-001",
        merchant_name="Walmart",
        canonical_merchant_name="WALMART",
        merchant_category="Retail",
        validation_status="MATCHED",
        requires_immediate_response=True,
        is_user_facing=True,
    )

    start_time = time.time()

    try:
        embeddings = embed_words_realtime(words, context)

        elapsed_time = time.time() - start_time

        logger.info(f"Embedding completed in {elapsed_time:.2f} seconds")
        logger.info(
            f"Embedded {len(embeddings)} words (filtered {len(words) - len(embeddings)} noise words)"
        )

        for word_text, embedding in embeddings.items():
            logger.info(f"  - {word_text}: {len(embedding)} dimensions")

        return embeddings

    except Exception as e:
        logger.error(f"Error during embedding: {str(e)}")
        raise


def test_full_receipt_embedding(receipt_id: str):
    """Test embedding a full receipt from DynamoDB."""
    logger.info(f"Testing full receipt embedding for receipt_id: {receipt_id}")

    start_time = time.time()

    try:
        word_embedding_pairs = embed_receipt_realtime(receipt_id)

        elapsed_time = time.time() - start_time

        logger.info(
            f"Receipt embedding completed in {elapsed_time:.2f} seconds"
        )
        logger.info(f"Processed {len(word_embedding_pairs)} words")

        # Show sample results
        for i, (word, embedding) in enumerate(word_embedding_pairs[:5]):
            logger.info(
                f"  - Word {i+1}: '{word.text}' -> {len(embedding)} dimensions"
            )

        if len(word_embedding_pairs) > 5:
            logger.info(
                f"  ... and {len(word_embedding_pairs) - 5} more words"
            )

        return word_embedding_pairs

    except Exception as e:
        logger.error(f"Error embedding receipt: {str(e)}")
        raise


def test_with_image_id(image_id: str):
    """Test embedding all receipts from an image."""
    logger.info(f"Testing embeddings for image_id: {image_id}")

    dynamo_client = DynamoClient("ReceiptsTable")

    # Get all receipts for this image
    receipts = dynamo_client.query(
        pk_value=f"IMAGE#{image_id}",
        sk_prefix="RECEIPT#",
    )

    receipt_ids = set()
    for item in receipts:
        if "receipt_id" in item:
            receipt_ids.add(item["receipt_id"])

    logger.info(f"Found {len(receipt_ids)} receipts for image {image_id}")

    for receipt_id in receipt_ids:
        logger.info(f"\nProcessing receipt {receipt_id}...")
        test_full_receipt_embedding(str(receipt_id))


def main():
    parser = argparse.ArgumentParser(
        description="Test real-time embedding functionality"
    )
    parser.add_argument(
        "--receipt-id",
        help="Receipt ID to test with",
    )
    parser.add_argument(
        "--image-id",
        help="Image ID to test all receipts from",
    )
    parser.add_argument(
        "--test-sample",
        action="store_true",
        help="Test with sample data (no DynamoDB required)",
    )
    parser.add_argument(
        "--table-name",
        default="ReceiptsTable",
        help="DynamoDB table name",
    )

    args = parser.parse_args()

    if args.test_sample:
        # Test with sample data
        logger.info("Running test with sample data...")
        embeddings = test_embedding_with_context()
        logger.info(f"\nTest completed successfully!")

    elif args.receipt_id:
        # Test with real receipt
        test_full_receipt_embedding(args.receipt_id)

    elif args.image_id:
        # Test with all receipts from an image
        test_with_image_id(args.image_id)

    else:
        parser.error(
            "Please specify --receipt-id, --image-id, or --test-sample"
        )


if __name__ == "__main__":
    main()
