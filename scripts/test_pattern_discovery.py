#!/usr/bin/env python3
"""Local development script for testing pattern discovery.

This script allows you to test and iterate on the pattern discovery logic
without deploying to Lambda. It automatically loads configuration from
Pulumi stack outputs and secrets.

Usage:
    # Run with a merchant name
    python scripts/test_pattern_discovery.py "Sprouts Farmers Market"

    # Just build and print the prompt (no LLM call)
    python scripts/test_pattern_discovery.py "Sprouts Farmers Market" --prompt-only

    # Use Chroma for validated label examples (hybrid approach)
    python scripts/test_pattern_discovery.py "Sprouts Farmers Market" --use-chroma
"""

import argparse
import json
import logging
import os
import sys

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def load_config():
    """Load configuration from Pulumi stack outputs and secrets."""
    from receipt_dynamo.data._pulumi import load_env, load_secrets

    outputs = load_env("dev")
    secrets = load_secrets("dev")

    config = {
        "dynamodb_table_name": outputs.get("dynamodb_table_name"),
        "ollama_api_key": secrets.get("portfolio:OLLAMA_API_KEY"),
        "langchain_api_key": secrets.get("portfolio:LANGCHAIN_API_KEY"),
        "openai_api_key": secrets.get("portfolio:OPENAI_API_KEY"),
        "chromadb_bucket": outputs.get("embedding_chromadb_bucket_name"),
    }

    # Set environment variables for the pattern discovery module
    if config["ollama_api_key"]:
        os.environ["OLLAMA_API_KEY"] = config["ollama_api_key"]
    if config["langchain_api_key"]:
        os.environ["LANGCHAIN_API_KEY"] = config["langchain_api_key"]
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = "label-evaluator-traced"
    if config["openai_api_key"]:
        os.environ["RECEIPT_AGENT_OPENAI_API_KEY"] = config["openai_api_key"]

    # Default Ollama settings (same as Lambda)
    os.environ.setdefault("OLLAMA_BASE_URL", "https://ollama.com")
    os.environ.setdefault("OLLAMA_MODEL", "gpt-oss:120b-cloud")

    return config


def load_chroma_client(chromadb_bucket: str):
    """Load ChromaDB client and embed function from S3 snapshot."""
    from receipt_agent.utils.chroma_helpers import load_dual_chroma_from_s3

    logger.info(f"Loading ChromaDB from s3://{chromadb_bucket}")
    chroma_client, embed_fn = load_dual_chroma_from_s3(chromadb_bucket)
    return chroma_client, embed_fn


def main():
    parser = argparse.ArgumentParser(
        description="Test pattern discovery locally"
    )
    parser.add_argument(
        "merchant_name",
        help="Name of the merchant to analyze",
    )
    parser.add_argument(
        "--prompt-only",
        action="store_true",
        help="Only build and print the prompt, don't call LLM",
    )
    parser.add_argument(
        "--use-chroma",
        action="store_true",
        help="Use ChromaDB for validated label examples (hybrid approach)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Maximum number of receipts to fetch (default: 3)",
    )
    parser.add_argument(
        "--env",
        default="dev",
        choices=["dev", "prod"],
        help="Pulumi environment (default: dev)",
    )
    args = parser.parse_args()

    # Load config from Pulumi
    logger.info("Loading configuration from Pulumi...")
    config = load_config()

    if not config["dynamodb_table_name"]:
        logger.error("Could not load dynamodb_table_name from Pulumi outputs")
        sys.exit(1)

    logger.info(f"Using DynamoDB table: {config['dynamodb_table_name']}")

    # Check for required config
    if not args.prompt_only and not config["ollama_api_key"]:
        logger.error("OLLAMA_API_KEY not found in Pulumi secrets")
        sys.exit(1)

    # Import directly to avoid chromadb import chain issues with Python 3.14
    # We import the module file directly instead of going through __init__.py
    import importlib.util

    pattern_discovery_path = os.path.join(
        PROJECT_ROOT,
        "receipt_agent/receipt_agent/agents/label_evaluator/pattern_discovery.py",
    )
    spec = importlib.util.spec_from_file_location(
        "pattern_discovery", pattern_discovery_path
    )
    pattern_discovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pattern_discovery)

    # Now import what we need
    PatternDiscoveryConfig = pattern_discovery.PatternDiscoveryConfig
    build_discovery_prompt = pattern_discovery.build_discovery_prompt
    build_receipt_structure = pattern_discovery.build_receipt_structure
    discover_patterns_with_llm = pattern_discovery.discover_patterns_with_llm
    query_label_examples_simple = pattern_discovery.query_label_examples_simple

    # Import DynamoClient (this should work fine)
    from receipt_dynamo import DynamoClient

    # Initialize DynamoDB client
    dynamo_client = DynamoClient(table_name=config["dynamodb_table_name"])

    # Optionally load ChromaDB for label examples
    label_examples = None
    if args.use_chroma:
        if not config["chromadb_bucket"]:
            logger.error("ChromaDB bucket not found in Pulumi outputs")
            sys.exit(1)
        if not config["openai_api_key"]:
            logger.error(
                "OpenAI API key not found in Pulumi secrets (needed for Chroma)"
            )
            sys.exit(1)

        try:
            chroma_client, embed_fn = load_chroma_client(
                config["chromadb_bucket"]
            )
            logger.info(
                f"Querying Chroma for label examples from: {args.merchant_name}"
            )
            label_examples = query_label_examples_simple(
                chroma_client,
                args.merchant_name,
                embed_fn=embed_fn,
                max_total=50,
            )
            if label_examples.total_examples > 0:
                logger.info(
                    f"Found {label_examples.total_examples} label examples from Chroma"
                )
                print("\n" + "=" * 80)
                print("CHROMA LABEL EXAMPLES:")
                print("=" * 80)
                print(label_examples.format_for_prompt())
                print("=" * 80 + "\n")
            else:
                logger.warning(
                    "No label examples found in Chroma for this merchant"
                )
        except Exception as e:
            logger.warning(f"Failed to load Chroma: {e}")
            logger.info("Continuing without Chroma examples...")

    # Build receipt structure with smart line selection
    logger.info(f"Building receipt structure for: {args.merchant_name}")
    receipts_data = build_receipt_structure(
        dynamo_client,
        args.merchant_name,
        limit=args.limit,
        focus_on_line_items=True,
        max_lines=80,
    )

    if not receipts_data:
        logger.error(
            f"No receipt data found for merchant: {args.merchant_name}"
        )
        sys.exit(1)

    logger.info(f"Found {len(receipts_data)} receipts")
    for receipt in receipts_data:
        logger.info(
            f"  - {receipt['receipt_id']}: {receipt['line_count']} lines "
            f"(of {receipt.get('total_lines', 'unknown')} total)"
        )

    # Build prompt with optional Chroma examples
    logger.info("Building discovery prompt...")
    prompt = build_discovery_prompt(
        args.merchant_name,
        receipts_data,
        label_examples=label_examples,
    )

    print("\n" + "=" * 80)
    print("PROMPT:")
    print("=" * 80)
    print(prompt)
    print("=" * 80 + "\n")

    if args.prompt_only:
        logger.info("--prompt-only specified, skipping LLM call")
        return

    # Call LLM
    logger.info("Calling LLM for pattern discovery...")
    llm_config = PatternDiscoveryConfig.from_env()
    patterns = discover_patterns_with_llm(prompt, llm_config)

    if patterns:
        print("\n" + "=" * 80)
        print("DISCOVERED PATTERNS:")
        print("=" * 80)
        print(json.dumps(patterns, indent=2))
        print("=" * 80 + "\n")
    else:
        logger.error("LLM pattern discovery failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
