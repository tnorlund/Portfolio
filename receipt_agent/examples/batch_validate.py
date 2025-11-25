#!/usr/bin/env python3
"""
Example: Batch validate metadata for multiple receipts.

This script demonstrates how to use the MetadataValidatorAgent
for batch validation with concurrency control.

Usage:
    python -m examples.batch_validate --merchant "Starbucks" --limit 10

Example:
    python -m examples.batch_validate --merchant "Target" --limit 5 --concurrency 3
"""

import argparse
import asyncio
import logging
import os
import sys
from collections import Counter

# Add parent to path for local development
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from receipt_agent import MetadataValidatorAgent
from receipt_agent.config.settings import get_settings
from receipt_agent.state.models import ValidationStatus


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


async def main(
    merchant_name: str,
    limit: int,
    concurrency: int,
    verbose: bool,
) -> None:
    """Run batch validation for receipts from a merchant."""
    setup_logging(verbose)
    logger = logging.getLogger(__name__)

    settings = get_settings()

    # Import clients
    try:
        from receipt_dynamo.data.dynamo_client import DynamoClient
        from receipt_chroma.data.chroma_client import ChromaClient
    except ImportError as e:
        logger.error(f"Failed to import clients: {e}")
        sys.exit(1)

    # Initialize clients
    dynamo = DynamoClient(table_name=settings.dynamo_table_name)
    chroma = ChromaClient(
        persist_directory=settings.chroma_persist_directory,
        mode="read",
    )

    # Find receipts for this merchant
    logger.info(f"Finding receipts for merchant: {merchant_name}")
    metadatas, _ = dynamo.get_receipt_metadatas_by_merchant(
        merchant_name=merchant_name,
        limit=limit,
    )

    if not metadatas:
        print(f"No receipts found for merchant: {merchant_name}")
        return

    receipts = [(m.image_id, m.receipt_id) for m in metadatas]
    print(f"Found {len(receipts)} receipts to validate")

    # Create agent
    agent = MetadataValidatorAgent(
        dynamo_client=dynamo,
        chroma_client=chroma,
        enable_tracing=True,
    )

    # Run batch validation
    print(f"\nValidating with concurrency={concurrency}...")
    print("-" * 60)

    results = await agent.validate_batch(
        receipts=receipts,
        max_concurrency=concurrency,
    )

    # Summarize results
    status_counts = Counter()
    confidence_sum = 0.0
    issues_found = []

    for (image_id, receipt_id), result in results:
        status_counts[result.status] += 1
        confidence_sum += result.confidence

        if result.status == ValidationStatus.INVALID:
            issues_found.append((image_id, receipt_id, result))

        # Print individual results
        status_emoji = {
            ValidationStatus.VALIDATED: "✅",
            ValidationStatus.INVALID: "❌",
            ValidationStatus.NEEDS_REVIEW: "⚠️",
            ValidationStatus.PENDING: "⏸️",
            ValidationStatus.ERROR: "💥",
        }
        emoji = status_emoji.get(result.status, "❓")
        print(f"  {emoji} {image_id}#{receipt_id}: {result.status.value} ({result.confidence:.0%})")

    # Print summary
    print("\n" + "=" * 60)
    print("BATCH VALIDATION SUMMARY")
    print("=" * 60)
    print(f"Total receipts: {len(results)}")
    print(f"Merchant: {merchant_name}")
    print(f"\nResults by status:")
    for status in ValidationStatus:
        count = status_counts[status]
        if count > 0:
            pct = count / len(results) * 100
            print(f"  {status.value}: {count} ({pct:.1f}%)")

    avg_confidence = confidence_sum / len(results) if results else 0
    print(f"\nAverage confidence: {avg_confidence:.1%}")

    if issues_found:
        print(f"\n⚠️  Found {len(issues_found)} potential issues:")
        for image_id, receipt_id, result in issues_found[:5]:
            print(f"  - {image_id}#{receipt_id}")
            if result.recommendations:
                for rec in result.recommendations[:2]:
                    print(f"    → {rec}")

    print("=" * 60)

    # Cleanup
    chroma.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Batch validate receipt metadata for a merchant"
    )
    parser.add_argument(
        "--merchant", "-m",
        required=True,
        help="Merchant name to validate",
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=10,
        help="Maximum receipts to validate",
    )
    parser.add_argument(
        "--concurrency", "-c",
        type=int,
        default=5,
        help="Maximum concurrent validations",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose logging",
    )

    args = parser.parse_args()

    asyncio.run(main(
        args.merchant,
        args.limit,
        args.concurrency,
        args.verbose,
    ))

