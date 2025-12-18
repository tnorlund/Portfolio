#!/usr/bin/env python3
"""
Run the label evaluator agent on a receipt.

This script:
1. Downloads the latest ChromaDB words snapshot from S3
2. Creates the label evaluator LangGraph workflow
3. Runs evaluation on a specified receipt
4. Displays detected issues and LLM review results

Usage:
    # Run on default test receipt
    python dev.run_label_evaluator_agent.py

    # Run on specific receipt
    python dev.run_label_evaluator_agent.py --image-id <uuid> --receipt-id <int>

    # Skip LLM review (faster)
    python dev.run_label_evaluator_agent.py --skip-llm-review

    # Use prod environment
    python dev.run_label_evaluator_agent.py --stack prod

    # Verbose output
    python dev.run_label_evaluator_agent.py --verbose
"""

import argparse
import logging
import logging.handlers
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Add repo root to path
repo_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, repo_root)

from receipt_chroma.data.chroma_client import ChromaClient
from receipt_chroma.s3.snapshot import download_snapshot_atomic
from receipt_dynamo.data._pulumi import load_env, load_secrets
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_agent.agents.label_evaluator import create_label_evaluator_graph

logger = logging.getLogger(__name__)


def get_ollama_api_key(stack: str = "dev") -> str:
    """Get Ollama API key from Pulumi secrets."""
    infra_dir = os.path.join(repo_root, "infra")
    secrets = load_secrets(stack, working_dir=infra_dir)
    # Try different possible key names
    api_key = (
        secrets.get("OLLAMA_API_KEY")
        or secrets.get("ollama_api_key")
        or secrets.get("portfolio:OLLAMA_API_KEY")
    )
    if not api_key:
        available_keys = [k for k in secrets.keys() if "ollama" in k.lower() or "api" in k.lower()]
        raise ValueError(
            f"OLLAMA_API_KEY not found in Pulumi secrets. Available keys: {available_keys[:10]}"
        )
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


def run_label_evaluator(
    dynamo_client: DynamoClient,
    chroma_client: ChromaClient,
    image_id: str,
    receipt_id: int,
    stack: str = "dev",
    skip_llm_review: bool = False,
    verbose: bool = False,
) -> None:
    """Run the label evaluator agent on a receipt."""

    # Create the graph
    logger.info("Creating label evaluator graph...")

    # Get Ollama API key
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
    )
    logger.info("✓ Graph created")

    # Invoke the agent
    logger.info(f"\nRunning label evaluator on receipt: {image_id} (receipt_id={receipt_id})")
    print("\n" + "="*70)
    print("LABEL EVALUATOR AGENT RUN")
    print("="*70)

    try:
        result = graph.invoke({
            "image_id": image_id,
            "receipt_id": receipt_id,
            "skip_llm_review": skip_llm_review,
        })

        print(f"\n✓ Agent completed successfully!")

        # Display results
        issues = result.get("issues_found", [])
        new_labels = result.get("new_labels", [])
        review_results = result.get("review_results", [])
        error = result.get("error")

        print(f"\n" + "-"*70)
        print("RESULTS")
        print("-"*70)
        print(f"Issues detected:     {len(issues)}")
        print(f"New labels created:  {len(new_labels)}")
        print(f"LLM reviews:         {len(review_results)}")
        if error:
            print(f"Error:               {error}")

        # Show issues by type
        if issues:
            print(f"\n" + "-"*70)
            print("ISSUES BY TYPE")
            print("-"*70)

            issue_types = {}
            for issue in issues:
                issue_type = issue.issue_type
                if issue_type not in issue_types:
                    issue_types[issue_type] = []
                issue_types[issue_type].append(issue)

            for issue_type, type_issues in sorted(issue_types.items()):
                print(f"\n{issue_type.upper()} ({len(type_issues)} issues):")
                for issue in type_issues[:3]:  # Show first 3 of each type
                    print(f"  - '{issue.word.text}' (line={issue.word.line_id}, word={issue.word.word_id})")
                    print(f"    Current label: {issue.current_label or 'none'}")
                    print(f"    Status: {issue.suggested_status}")
                    print(f"    Reasoning: {issue.reasoning}")

                if len(type_issues) > 3:
                    print(f"  ... and {len(type_issues) - 3} more")

        # Show LLM review results
        if review_results:
            print(f"\n" + "-"*70)
            print("LLM REVIEW RESULTS")
            print("-"*70)

            for i, review in enumerate(review_results[:3], 1):
                issue = review.issue
                print(f"\n{i}. '{issue.word.text}'")
                print(f"   Original issue: {issue.issue_type}")
                print(f"   LLM decision: {review.decision}")
                if not review.review_completed:
                    print(f"   Error: {review.review_error}")
                else:
                    print(f"   Reasoning: {review.reasoning}")
                    if review.suggested_label:
                        print(f"   Suggested label: {review.suggested_label}")

            if len(review_results) > 3:
                print(f"\n... and {len(review_results) - 3} more review results")

        print(f"\n" + "="*70)

    except Exception as e:
        print(f"\n❌ Error running agent: {e}")
        if verbose:
            import traceback
            traceback.print_exc()
        raise


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run the label evaluator agent on a receipt"
    )
    parser.add_argument(
        "--stack",
        default="dev",
        help="Pulumi stack (dev or prod)",
    )
    parser.add_argument(
        "--image-id",
        default="955da9a2-74f8-418e-be0b-be462b21af1f",
        help="Image ID to evaluate (default: test receipt)",
    )
    parser.add_argument(
        "--receipt-id",
        type=int,
        default=2,
        help="Receipt ID to evaluate (default: 2)",
    )
    parser.add_argument(
        "--skip-llm-review",
        action="store_true",
        help="Skip LLM review phase (faster)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output with tracebacks",
    )

    args = parser.parse_args()

    # Setup logging to both console and file
    log_level = logging.DEBUG if args.verbose else logging.INFO

    # Create logs directory if it doesn't exist
    logs_dir = Path(repo_root) / "logs" / "label_evaluator"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Create log file with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"evaluation_{timestamp}.log"

    # Configure logging with both console and file handlers
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # File handler
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(log_level)
    file_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # Log the output path
    logger.info(f"Logging to: {log_file}")

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
    temp_dir = tempfile.mkdtemp(prefix="eval_agent_")
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

        # Open ChromaDB client and run agent
        with ChromaClient(persist_directory=temp_dir, mode="read") as chroma_client:
            logger.info("✓ Opened ChromaDB client")

            # Run the agent
            run_label_evaluator(
                dynamo_client=dynamo_client,
                chroma_client=chroma_client,
                image_id=args.image_id,
                receipt_id=args.receipt_id,
                stack=args.stack,
                skip_llm_review=args.skip_llm_review,
                verbose=args.verbose,
            )

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
        logger.info(f"\n✓ Evaluation complete. Full logs saved to: {log_file}")


if __name__ == "__main__":
    main()
