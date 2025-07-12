#!/usr/bin/env python3
"""
Test script for the agent-based receipt labeling system.

This script allows testing the labeling pipeline with:
1. Real DynamoDB data (export/import)
2. Sample test data
3. Performance metrics and accuracy measurements
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import ReceiptLine, ReceiptWord

from receipt_label.agent.receipt_labeler_agent import ReceiptLabelerAgent
from receipt_label.utils.client_manager import ClientConfig, ClientManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def create_sample_receipt() -> tuple[List[ReceiptWord], List[ReceiptLine]]:
    """Create sample receipt data for testing."""
    # Sample receipt from a grocery store
    words = [
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=1,
            word_id=1,
            text="WALMART",
            x=100,
            y=50,
            bounding_box={"x": 50, "y": 40, "width": 100, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=1,
            word_id=2,
            text="SUPERCENTER",
            x=220,
            y=50,
            bounding_box={"x": 170, "y": 40, "width": 100, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=2,
            word_id=3,
            text="123",
            x=50,
            y=80,
            bounding_box={"x": 30, "y": 70, "width": 40, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=2,
            word_id=4,
            text="MAIN",
            x=100,
            y=80,
            bounding_box={"x": 80, "y": 70, "width": 40, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=2,
            word_id=5,
            text="STREET",
            x=150,
            y=80,
            bounding_box={"x": 130, "y": 70, "width": 50, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=3,
            word_id=6,
            text="(555)",
            x=50,
            y=110,
            bounding_box={"x": 30, "y": 100, "width": 50, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=3,
            word_id=7,
            text="123-4567",
            x=110,
            y=110,
            bounding_box={"x": 90, "y": 100, "width": 80, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=4,
            word_id=8,
            text="12/25/2023",
            x=50,
            y=140,
            bounding_box={"x": 30, "y": 130, "width": 80, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=4,
            word_id=9,
            text="14:32:10",
            x=150,
            y=140,
            bounding_box={"x": 130, "y": 130, "width": 60, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=5,
            word_id=10,
            text="APPLES",
            x=50,
            y=180,
            bounding_box={"x": 30, "y": 170, "width": 60, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=5,
            word_id=11,
            text="2",
            x=150,
            y=180,
            bounding_box={"x": 140, "y": 170, "width": 20, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=5,
            word_id=12,
            text="@",
            x=175,
            y=180,
            bounding_box={"x": 165, "y": 170, "width": 20, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=5,
            word_id=13,
            text="$1.99",
            x=200,
            y=180,
            bounding_box={"x": 190, "y": 170, "width": 50, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=5,
            word_id=14,
            text="$3.98",
            x=280,
            y=180,
            bounding_box={"x": 260, "y": 170, "width": 50, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=6,
            word_id=15,
            text="MILK",
            x=50,
            y=210,
            bounding_box={"x": 30, "y": 200, "width": 40, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=6,
            word_id=16,
            text="$4.59",
            x=280,
            y=210,
            bounding_box={"x": 260, "y": 200, "width": 50, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=7,
            word_id=17,
            text="SUBTOTAL",
            x=50,
            y=250,
            bounding_box={"x": 30, "y": 240, "width": 80, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=7,
            word_id=18,
            text="$8.57",
            x=280,
            y=250,
            bounding_box={"x": 260, "y": 240, "width": 50, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=8,
            word_id=19,
            text="TAX",
            x=50,
            y=280,
            bounding_box={"x": 30, "y": 270, "width": 40, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=8,
            word_id=20,
            text="$0.69",
            x=280,
            y=280,
            bounding_box={"x": 260, "y": 270, "width": 50, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=9,
            word_id=21,
            text="TOTAL",
            x=50,
            y=310,
            bounding_box={"x": 30, "y": 300, "width": 50, "height": 20},
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=9,
            word_id=22,
            text="$9.26",
            x=280,
            y=310,
            bounding_box={"x": 260, "y": 300, "width": 50, "height": 20},
        ),
    ]

    # Create corresponding lines
    lines = [
        ReceiptLine(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=1,
            text="WALMART SUPERCENTER",
            bounding_box={"x": 50, "y": 40, "width": 220, "height": 20},
        ),
        ReceiptLine(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=2,
            text="123 MAIN STREET",
            bounding_box={"x": 30, "y": 70, "width": 150, "height": 20},
        ),
        ReceiptLine(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=3,
            text="(555) 123-4567",
            bounding_box={"x": 30, "y": 100, "width": 140, "height": 20},
        ),
        ReceiptLine(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=4,
            text="12/25/2023 14:32:10",
            bounding_box={"x": 30, "y": 130, "width": 160, "height": 20},
        ),
        ReceiptLine(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=5,
            text="APPLES 2 @ $1.99 $3.98",
            bounding_box={"x": 30, "y": 170, "width": 280, "height": 20},
        ),
        ReceiptLine(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=6,
            text="MILK $4.59",
            bounding_box={"x": 30, "y": 200, "width": 280, "height": 20},
        ),
        ReceiptLine(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=7,
            text="SUBTOTAL $8.57",
            bounding_box={"x": 30, "y": 240, "width": 280, "height": 20},
        ),
        ReceiptLine(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=8,
            text="TAX $0.69",
            bounding_box={"x": 30, "y": 270, "width": 280, "height": 20},
        ),
        ReceiptLine(
            receipt_id=1,
            image_id="test-agent-001",
            line_id=9,
            text="TOTAL $9.26",
            bounding_box={"x": 30, "y": 300, "width": 280, "height": 20},
        ),
    ]

    return words, lines


async def test_agent_labeling(
    client_manager: ClientManager,
    receipt_id: str,
    words: List[ReceiptWord],
    lines: List[ReceiptLine],
    store_labels: bool = False,
) -> Dict:
    """Test the agent labeling system."""
    logger.info(f"\n{'='*60}")
    logger.info(f"Testing agent labeling for receipt {receipt_id}")
    logger.info(f"{'='*60}")

    # Create agent
    agent = ReceiptLabelerAgent(client_manager)

    # Test metadata
    test_metadata = {
        "merchant_name": "WALMART",
        "canonical_merchant_name": "Walmart",
        "category": "grocery",
    }

    # Run labeling
    start_time = time.time()

    results = await agent.label_receipt(
        receipt_id=receipt_id,
        receipt_words=words,
        receipt_lines=lines,
        receipt_metadata=test_metadata,
        validate_labels=False,
        store_labels=store_labels,
    )

    elapsed = time.time() - start_time

    # Display results
    if results["success"]:
        logger.info(f"\n✅ Labeling successful!")
        logger.info(f"   - Total words: {len(words)}")
        logger.info(f"   - Labels applied: {results['labels_applied']}")
        logger.info(f"   - Pattern labels: {results['pattern_labels']}")
        logger.info(f"   - GPT labels: {results['gpt_labels']}")
        logger.info(f"   - Coverage rate: {results['coverage_rate']:.1%}")
        logger.info(f"   - GPT called: {results['gpt_called']}")
        if results["gpt_called"]:
            logger.info(f"   - GPT reason: {results['gpt_reason']}")
        logger.info(f"   - Processing time: {elapsed:.2f}s")

        # Show pattern breakdown
        logger.info(f"\nPattern detection results:")
        for pattern_type, count in results["pattern_results"].items():
            logger.info(f"   - {pattern_type}: {count} patterns")

        # Show some example labels if not stored
        if not store_labels and results.get("word_labels"):
            logger.info(f"\nExample labels (first 10):")
            for i, label in enumerate(results["word_labels"][:10]):
                logger.info(
                    f"   - '{label.text}' -> {label.label} "
                    f"(confidence: {label.confidence:.2f}, source: {label.source})"
                )
    else:
        logger.error(f"\n❌ Labeling failed: {results.get('error')}")

    # Get statistics
    stats = agent.get_statistics()
    logger.info(f"\nAgent statistics:")
    logger.info(
        f"   - Decision engine: {json.dumps(stats['decision_engine'], indent=6)}"
    )
    logger.info(
        f"   - Batch processor: {json.dumps(stats['batch_processor'], indent=6)}"
    )

    return results


async def test_with_dynamo_data(
    client_manager: ClientManager, receipt_id: str, store_labels: bool = False
):
    """Test with real data from DynamoDB."""
    logger.info(f"Fetching receipt {receipt_id} from DynamoDB...")

    try:
        # Fetch receipt data
        words = client_manager.dynamo.list_receipt_words_by_receipt(receipt_id)
        lines = client_manager.dynamo.list_receipt_lines_by_receipt(receipt_id)

        if not words:
            logger.error(f"No words found for receipt {receipt_id}")
            return

        logger.info(f"Found {len(words)} words and {len(lines)} lines")

        # Run test
        await test_agent_labeling(
            client_manager, receipt_id, words, lines, store_labels
        )

    except Exception as e:
        logger.error(f"Error fetching receipt data: {e}")


async def batch_test(
    client_manager: ClientManager, image_id: str, limit: int = 5
):
    """Test multiple receipts from an image."""
    logger.info(f"Testing receipts from image {image_id} (limit: {limit})...")

    # Query receipts
    items = client_manager.dynamo.query(
        pk_value=f"IMAGE#{image_id}", sk_prefix="RECEIPT#", limit=limit
    )

    receipt_ids = [
        item["receipt_id"] for item in items if "receipt_id" in item
    ]
    logger.info(f"Found {len(receipt_ids)} receipts")

    for receipt_id in receipt_ids:
        await test_with_dynamo_data(
            client_manager, str(receipt_id), store_labels=False
        )


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test agent-based receipt labeling system"
    )
    parser.add_argument(
        "--receipt-id", help="Test with specific receipt ID from DynamoDB"
    )
    parser.add_argument(
        "--image-id", help="Test with all receipts from an image"
    )
    parser.add_argument(
        "--sample", action="store_true", help="Test with sample receipt data"
    )
    parser.add_argument(
        "--store",
        action="store_true",
        help="Store labels in DynamoDB (default: dry run)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Limit number of receipts for batch testing",
    )

    args = parser.parse_args()

    # Initialize client manager
    try:
        config = ClientConfig.from_env()
        client_manager = ClientManager(config)
    except KeyError as e:
        logger.error(f"Missing required environment variable: {e}")
        logger.info(
            "Required variables: DYNAMODB_TABLE_NAME, OPENAI_API_KEY, PINECONE_API_KEY, PINECONE_INDEX_NAME, PINECONE_HOST"
        )
        sys.exit(1)

    # Run appropriate test
    if args.sample:
        logger.info("Testing with sample receipt...")
        words, lines = create_sample_receipt()
        asyncio.run(
            test_agent_labeling(
                client_manager, "sample-001", words, lines, args.store
            )
        )
    elif args.receipt_id:
        asyncio.run(
            test_with_dynamo_data(client_manager, args.receipt_id, args.store)
        )
    elif args.image_id:
        asyncio.run(batch_test(client_manager, args.image_id, args.limit))
    else:
        parser.error("Please specify --sample, --receipt-id, or --image-id")


if __name__ == "__main__":
    main()
