#!/usr/bin/env python3
"""
Run the label evaluator agent on all (or sampled) Sprouts receipts.

This script:
1. Queries DynamoDB for all Sprouts receipts
2. Runs the label evaluator agent on each receipt
3. Aggregates results by issue type and label type
4. Optionally tests different CORE_LABELS combinations

Usage:
    # Run on all Sprouts receipts (may take a while)
    python dev.run_label_evaluator_batch_sprouts.py --merchant Sprouts

    # Run on first 10 Sprouts receipts (faster testing)
    python dev.run_label_evaluator_batch_sprouts.py --merchant Sprouts --limit 10

    # Test with optimized accuracy settings (comprehensive pattern analysis)
    python dev.run_label_evaluator_batch_sprouts.py --merchant Sprouts \\
      --max-pairs 32 --dimension 3

    # Test specific label types only (PRODUCT_NAME, QUANTITY, UNIT_PRICE, LINE_TOTAL)
    python dev.run_label_evaluator_batch_sprouts.py --merchant Sprouts --limit 10 \\
      --filter-labels PRODUCT_NAME QUANTITY UNIT_PRICE LINE_TOTAL

    # Test all CORE_LABELS combinations sequentially
    python dev.run_label_evaluator_batch_sprouts.py --merchant Sprouts --limit 5 \\
      --test-combinations

    # Skip LLM review (faster)
    python dev.run_label_evaluator_batch_sprouts.py --merchant Sprouts --skip-llm-review --limit 10

    # Verbose output
    python dev.run_label_evaluator_batch_sprouts.py --merchant Sprouts --verbose --limit 10
"""

import argparse
import logging
import os
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set

# Add repo root to path
repo_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, repo_root)

from receipt_chroma.data.chroma_client import ChromaClient
from receipt_chroma.s3.snapshot import download_snapshot_atomic
from receipt_dynamo.data._pulumi import load_env, load_secrets
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_agent.agents.label_evaluator import create_label_evaluator_graph

logger = logging.getLogger(__name__)

# 18 CORE_LABELS from the system
CORE_LABELS = [
    "MERCHANT_NAME",
    "STORE_HOURS",
    "PHONE_NUMBER",
    "WEBSITE",
    "LOYALTY_ID",
    "ADDRESS_LINE",
    "DATE",
    "TIME",
    "PAYMENT_METHOD",
    "COUPON",
    "DISCOUNT",
    "PRODUCT_NAME",
    "QUANTITY",
    "UNIT_PRICE",
    "LINE_TOTAL",
    "SUBTOTAL",
    "TAX",
    "GRAND_TOTAL",
]


def get_ollama_api_key(stack: str = "dev") -> str:
    """Get Ollama API key from Pulumi secrets."""
    infra_dir = os.path.join(repo_root, "infra")
    secrets = load_secrets(stack, working_dir=infra_dir)
    api_key = (
        secrets.get("OLLAMA_API_KEY")
        or secrets.get("ollama_api_key")
        or secrets.get("portfolio:OLLAMA_API_KEY")
    )
    if not api_key:
        raise ValueError("OLLAMA_API_KEY not found in Pulumi secrets")
    return api_key


def load_environment(stack: str = "dev") -> tuple:
    """Load environment configuration from Pulumi."""
    try:
        infra_dir = os.path.join(repo_root, "infra")
        env = load_env(stack, working_dir=infra_dir)
        table_name = env.get("dynamodb_table_name")
        chroma_bucket = env.get("chromadb_bucket_name")
        if not table_name or not chroma_bucket:
            raise ValueError(
                f"Missing environment: table_name={table_name}, "
                f"chroma_bucket={chroma_bucket}"
            )
        return table_name, chroma_bucket
    except Exception as e:
        logger.error(f"Failed to load environment: {e}")
        raise


def get_sprouts_receipts(
    dynamo_client: DynamoClient,
    merchant_name: str,
    limit: Optional[int] = None,
) -> List[tuple]:
    """
    Query DynamoDB for all receipts from a specific merchant.

    Returns list of (image_id, receipt_id) tuples.
    """
    logger.info(f"Querying receipts for merchant: {merchant_name}")

    receipts = []
    last_evaluated_key = None

    try:
        # Use DynamoClient API to query receipt metadatas by merchant
        while True:
            metadatas, last_evaluated_key = dynamo_client.get_receipt_metadatas_by_merchant(
                merchant_name=merchant_name,
                limit=100,
                last_evaluated_key=last_evaluated_key,
            )

            for metadata in metadatas:
                receipts.append((metadata.image_id, metadata.receipt_id))

                if limit and len(receipts) >= limit:
                    logger.info(f"Reached limit of {limit} receipts")
                    return receipts[:limit]

            if not last_evaluated_key:
                break

        logger.info(f"Found {len(receipts)} receipts for {merchant_name}")
        return receipts[:limit] if limit else receipts

    except Exception as e:
        logger.error(f"Failed to query receipts for {merchant_name}: {e}")
        raise


def run_label_evaluator(
    dynamo_client: DynamoClient,
    chroma_client: ChromaClient,
    image_id: str,
    receipt_id: int,
    stack: str = "dev",
    skip_llm_review: bool = False,
    max_pair_patterns: int = 4,
    max_relationship_dimension: int = 2,
) -> Dict:
    """Run the label evaluator agent on a receipt and return results."""
    try:
        ollama_api_key = get_ollama_api_key(stack)
    except ValueError as e:
        logger.warning(f"Could not load Ollama API key: {e}")
        ollama_api_key = None

    graph = create_label_evaluator_graph(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        llm_provider="ollama",
        ollama_base_url="https://ollama.com",
        ollama_api_key=ollama_api_key,
        max_pair_patterns=max_pair_patterns,
        max_relationship_dimension=max_relationship_dimension,
    )

    try:
        result = graph.invoke({
            "image_id": image_id,
            "receipt_id": receipt_id,
            "skip_llm_review": skip_llm_review,
        })
        return {
            "success": True,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "issues": result.get("issues_found", []),
            "new_labels": result.get("new_labels", []),
            "review_results": result.get("review_results", []),
        }
    except Exception as e:
        logger.error(f"Error processing receipt {image_id}#{receipt_id}: {e}")
        return {
            "success": False,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "error": str(e),
        }


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run label evaluator agent on Sprouts receipts"
    )
    parser.add_argument(
        "--merchant",
        default="Sprouts",
        help="Merchant name to filter receipts (default: Sprouts)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of receipts to process (default: all)",
    )
    parser.add_argument(
        "--skip-llm-review",
        action="store_true",
        help="Skip LLM review phase (faster)",
    )
    parser.add_argument(
        "--stack",
        default="dev",
        help="Pulumi stack (dev or prod)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output",
    )
    parser.add_argument(
        "--filter-labels",
        nargs="+",
        help="Filter to specific CORE_LABELs (e.g., PRODUCT_NAME QUANTITY UNIT_PRICE)",
    )
    parser.add_argument(
        "--test-combinations",
        action="store_true",
        help="Test all CORE_LABELS combinations",
    )
    parser.add_argument(
        "--max-pairs",
        type=int,
        default=4,
        help="Maximum label pairs/tuples to compute geometry for (default: 4)",
    )
    parser.add_argument(
        "--dimension",
        type=int,
        default=2,
        choices=[2, 3, 4, 5],
        help="Relationship dimension: 2=pairs, 3=triples, 4+=n-tuples (default: 2)",
    )

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Load environment
    try:
        table_name, chroma_bucket = load_environment(args.stack)
        logger.info(f"✓ Loaded {args.stack} environment")
    except Exception as e:
        logger.error(f"Failed to load environment: {e}")
        sys.exit(1)

    # Set environment variables
    os.environ["DYNAMODB_TABLE_NAME"] = table_name

    # Create clients
    try:
        dynamo_client = DynamoClient(table_name)
        logger.info("✓ Created DynamoDB client")
    except Exception as e:
        logger.error(f"Failed to create DynamoDB client: {e}")
        sys.exit(1)

    # Download ChromaDB snapshot
    temp_dir = tempfile.mkdtemp(prefix="eval_agent_batch_")
    logger.info(f"Downloading ChromaDB snapshot to: {temp_dir}")

    try:
        result = download_snapshot_atomic(
            bucket=chroma_bucket,
            collection="words",
            local_path=temp_dir,
            verify_integrity=False,
        )

        if result.get("status") != "downloaded":
            logger.error(f"Failed to download snapshot: {result}")
            sys.exit(1)

        logger.info(f"✓ Downloaded version: {result.get('version_id')}")

        # Get receipts to process
        receipts = get_sprouts_receipts(
            dynamo_client,
            args.merchant,
            limit=args.limit,
        )

        if not receipts:
            logger.error(f"No receipts found for {args.merchant}")
            sys.exit(1)

        logger.info(f"\nProcessing {len(receipts)} receipts from {args.merchant}...")

        # Open ChromaDB client and process receipts
        with ChromaClient(persist_directory=temp_dir, mode="read") as chroma_client:
            logger.info("✓ Opened ChromaDB client")

            all_results = []
            issue_types_counter: Dict[str, int] = defaultdict(int)
            label_types_counter: Dict[str, int] = defaultdict(int)
            success_count = 0
            failed_count = 0

            for i, (image_id, receipt_id) in enumerate(receipts, 1):
                logger.info(f"\n[{i}/{len(receipts)}] Processing receipt {receipt_id}...")

                result = run_label_evaluator(
                    dynamo_client=dynamo_client,
                    chroma_client=chroma_client,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    stack=args.stack,
                    skip_llm_review=args.skip_llm_review,
                    max_pair_patterns=args.max_pairs,
                    max_relationship_dimension=args.dimension,
                )

                all_results.append(result)

                if result["success"]:
                    success_count += 1
                    issues = result.get("issues", [])

                    # Count issue types
                    for issue in issues:
                        issue_type = getattr(issue, "issue_type", "unknown")
                        issue_types_counter[issue_type] += 1

                        # Count label types
                        current_label = getattr(issue, "current_label", None)
                        if current_label:
                            label_types_counter[current_label] += 1

                    logger.info(f"  ✓ Found {len(issues)} issues")
                else:
                    failed_count += 1
                    logger.error(f"  ✗ Failed: {result.get('error')}")

            # Print summary
            print("\n" + "="*70)
            print("BATCH PROCESSING SUMMARY")
            print("="*70)
            print(f"Merchant:          {args.merchant}")
            print(f"Total receipts:    {len(receipts)}")
            print(f"Successful:        {success_count}")
            print(f"Failed:            {failed_count}")

            if issue_types_counter:
                print(f"\nIssue Types Found:")
                for issue_type, count in sorted(
                    issue_types_counter.items(),
                    key=lambda x: x[1],
                    reverse=True,
                ):
                    print(f"  {issue_type}: {count}")

            if label_types_counter:
                print(f"\nLabel Types with Issues:")
                for label_type, count in sorted(
                    label_types_counter.items(),
                    key=lambda x: x[1],
                    reverse=True,
                ):
                    print(f"  {label_type}: {count}")

            print("="*70)

    except Exception as e:
        logger.error(f"Error during execution: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    finally:
        # Clean up
        logger.info(f"Cleaning up {temp_dir}")
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
