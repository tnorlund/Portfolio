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

from receipt_label.embedding.line.realtime import embed_receipt_lines_realtime
from receipt_label.embedding.word.realtime import (
    embed_receipt_words_realtime,
    embed_words_realtime,
)
from receipt_label.merchant_validation.handler import create_validation_handler

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import ReceiptWord

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
    merchant_name = "WALMART"

    start_time = time.time()

    try:
        word_embeddings = embed_words_realtime(words, merchant_name)

        elapsed_time = time.time() - start_time

        logger.info(f"Embedding completed in {elapsed_time:.2f} seconds")
        logger.info(
            f"Embedded {len(word_embeddings)} words (filtered {len(words) - len(word_embeddings)} noise words)"
        )

        for word, embedding in word_embeddings:
            logger.info(f"  - {word.text}: {len(embedding)} dimensions")

        return word_embeddings

    except Exception as e:
        logger.error(f"Error during embedding: {str(e)}")
        raise


def test_full_receipt_embedding(receipt_id: str, table_name: str = "ReceiptsTable"):
    """Test embedding a full receipt from DynamoDB with merchant validation."""
    logger.info(f"Testing full receipt embedding for receipt_id: {receipt_id}")

    start_time = time.time()

    try:
        # Get receipt data from DynamoDB
        dynamo_client = DynamoClient(table_name)
        receipt_words = dynamo_client.list_receipt_words_by_receipt(receipt_id)
        receipt_lines = dynamo_client.list_receipt_lines_by_receipt(receipt_id)

        if not receipt_words:
            raise ValueError(f"No words found for receipt {receipt_id}")

        # Run merchant validation
        logger.info(f"Running merchant validation for receipt {receipt_id}")
        validation_handler = create_validation_handler()

        # Convert receipt_id to integer for validation
        receipt_id_int = int(receipt_id)

        merchant_metadata, status_info = validation_handler.validate_receipt_merchant(
            image_id=receipt_words[0].image_id,
            receipt_id=receipt_id_int,
            receipt_lines=receipt_lines,
            receipt_words=receipt_words,
        )

        logger.info(
            f"Merchant validation completed: {status_info.get('status', 'unknown')}"
        )

        # Get canonical merchant name
        canonical_merchant_name = (
            (
                merchant_metadata.canonical_merchant_name
                or merchant_metadata.merchant_name
            )
            if merchant_metadata
            else None
        )

        # Perform real-time embedding
        embedding_results = {}

        # Embed words
        logger.info(f"Embedding words for receipt {receipt_id}")
        word_embeddings = embed_receipt_words_realtime(
            receipt_id, canonical_merchant_name
        )
        embedding_results["words"] = {
            "count": len(word_embeddings),
            "merchant_name": canonical_merchant_name,
        }
        logger.info(f"Successfully embedded {len(word_embeddings)} words")

        # Optionally embed lines
        # line_embeddings = embed_receipt_lines_realtime(
        #     receipt_id, canonical_merchant_name
        # )
        # embedding_results["lines"] = {
        #     "count": len(line_embeddings),
        #     "merchant_name": canonical_merchant_name,
        # }

        elapsed_time = time.time() - start_time

        logger.info(f"Receipt processing completed in {elapsed_time:.2f} seconds")

        # Show merchant validation results
        if merchant_metadata:
            logger.info(f"Merchant: {merchant_metadata.merchant_name}")
            logger.info(f"Place ID: {merchant_metadata.place_id}")
            logger.info(f"Validated by: {merchant_metadata.validated_by}")

        # Show embedding results
        if "words" in embedding_results:
            word_count = embedding_results["words"].get("count", 0)
            logger.info(f"Embedded {word_count} words")

        if "lines" in embedding_results:
            line_count = embedding_results["lines"].get("count", 0)
            logger.info(f"Embedded {line_count} lines")

        return merchant_metadata, embedding_results

    except Exception as e:
        logger.error(f"Error processing receipt: {str(e)}")
        raise


def test_with_image_id(image_id: str, table_name: str = "ReceiptsTable"):
    """Test embedding all receipts from an image."""
    logger.info(f"Testing embeddings for image_id: {image_id}")

    dynamo_client = DynamoClient(table_name)

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
        test_full_receipt_embedding(str(receipt_id), table_name)


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
        test_full_receipt_embedding(args.receipt_id, args.table_name)

    elif args.image_id:
        # Test with all receipts from an image
        test_with_image_id(args.image_id, args.table_name)

    else:
        parser.error("Please specify --receipt-id, --image-id, or --test-sample")


if __name__ == "__main__":
    main()
