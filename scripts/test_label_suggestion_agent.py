#!/usr/bin/env python3
"""
Test script for label suggestion agent.

This script tests the label suggestion agent locally on a single receipt.

Usage:
    python scripts/test_label_suggestion_agent.py <image_id> <receipt_id> [--table-name TABLE] [--use-llm]

Example:
    python scripts/test_label_suggestion_agent.py img_abc123 42 --table-name ReceiptsTable-dev
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from receipt_agent.clients.factory import create_chroma_client, create_embed_fn
from receipt_agent.config.settings import get_settings
from receipt_agent.graph.label_suggestion_workflow import suggest_labels_for_receipt
from receipt_dynamo.data.dynamo_client import DynamoClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def setup_environment():
    """Load secrets and outputs from Pulumi and set environment variables."""
    from receipt_dynamo.data._pulumi import load_env, load_secrets

    infra_dir = project_root / "infra"
    env = load_env("dev", working_dir=str(infra_dir))
    secrets = load_secrets("dev", working_dir=str(infra_dir))

    # DynamoDB
    table_name = env.get("dynamodb_table_name") or env.get("receipts_table_name")
    if table_name:
        os.environ["RECEIPT_AGENT_DYNAMO_TABLE_NAME"] = table_name
        os.environ["DYNAMODB_TABLE_NAME"] = table_name
        logger.info(f"📊 DynamoDB Table: {table_name}")
    else:
        logger.warning("⚠️  DynamoDB table name not found in Pulumi outputs")

    # ChromaDB - try to find snapshots
    chroma_snapshots = project_root / ".chroma_snapshots"
    lines_dir = chroma_snapshots / "lines"
    words_dir = chroma_snapshots / "words"

    if lines_dir.exists() and words_dir.exists():
        os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = str(lines_dir)
        os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = str(words_dir)
        logger.info(f"✅ ChromaDB snapshots found: {lines_dir}")
    else:
        # Try alternative locations
        alt_lines = project_root / "receipt_chroma" / "snapshots" / "lines"
        alt_words = project_root / "receipt_chroma" / "snapshots" / "words"
        if alt_lines.exists() and alt_words.exists():
            os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = str(alt_lines)
            os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = str(alt_words)
            logger.info(f"✅ ChromaDB snapshots found: {alt_lines}")
        else:
            logger.warning("⚠️  ChromaDB snapshots not found - will try to use default paths")

    # API Keys
    openai_key = secrets.get("portfolio:OPENAI_API_KEY")
    if openai_key:
        os.environ["OPENAI_API_KEY"] = openai_key
        os.environ["RECEIPT_AGENT_OPENAI_API_KEY"] = openai_key
        logger.info("✅ OpenAI API key loaded")
    else:
        logger.warning("⚠️  OpenAI API key not found")

    ollama_key = secrets.get("portfolio:OLLAMA_API_KEY")
    if ollama_key:
        os.environ["OLLAMA_API_KEY"] = ollama_key
        os.environ["RECEIPT_AGENT_OLLAMA_API_KEY"] = ollama_key
        logger.info("✅ Ollama API key loaded")
    else:
        logger.warning("⚠️  Ollama API key not found")

    ollama_base_url = secrets.get("portfolio:OLLAMA_BASE_URL", "https://api.ollama.com/v1")
    os.environ["RECEIPT_AGENT_OLLAMA_BASE_URL"] = ollama_base_url

    ollama_model = secrets.get("portfolio:OLLAMA_MODEL", "gpt-oss")
    os.environ["RECEIPT_AGENT_OLLAMA_MODEL"] = ollama_model

    # LangSmith tracing
    langsmith_key = secrets.get("portfolio:LANGCHAIN_API_KEY", "")
    if langsmith_key:
        os.environ["LANGCHAIN_API_KEY"] = langsmith_key
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = "label-suggestion-agent"
        logger.info("✅ LangSmith tracing enabled")
    else:
        logger.warning("⚠️  LangSmith API key not found - tracing disabled")

    return env, secrets




def create_llm():
    """Create LLM for ambiguous cases (optional)."""
    try:
        from langchain_ollama import ChatOllama

        api_key = os.environ.get("OLLAMA_API_KEY") or os.environ.get("RECEIPT_AGENT_OLLAMA_API_KEY")
        base_url = os.environ.get("OLLAMA_BASE_URL", "https://api.ollama.com/v1")
        model = os.environ.get("OLLAMA_MODEL", "gpt-oss")

        if not api_key:
            logger.warning("OLLAMA_API_KEY not set - LLM will not be available")
            return None

        return ChatOllama(
            base_url=base_url,
            model=model,
            client_kwargs={
                "headers": {"Authorization": f"Bearer {api_key}"},
                "timeout": 120,
            },
            temperature=0.0,
        )
    except ImportError:
        logger.warning("langchain_ollama not available - LLM will not be used")
        return None


async def main():
    # Setup environment from Pulumi first
    logger.info("Loading environment from Pulumi...")
    env, secrets = setup_environment()

    parser = argparse.ArgumentParser(description="Test label suggestion agent")
    parser.add_argument("image_id", help="Image ID")
    parser.add_argument("receipt_id", type=int, help="Receipt ID")
    parser.add_argument(
        "--table-name",
        default=os.environ.get("DYNAMODB_TABLE_NAME") or os.environ.get("RECEIPT_AGENT_DYNAMO_TABLE_NAME"),
        help="DynamoDB table name (default: from Pulumi or env)",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Use LLM for ambiguous cases (default: False, ChromaDB only)",
    )
    parser.add_argument(
        "--chroma-path",
        default=None,
        help="Path to local ChromaDB snapshot (optional)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run - don't actually create labels",
    )

    args = parser.parse_args()

    if not args.table_name:
        logger.error("DynamoDB table name required. Set --table-name or configure in Pulumi.")
        sys.exit(1)

    logger.info(f"Testing label suggestion agent for receipt {args.receipt_id}")
    logger.info(f"Image ID: {args.image_id}")
    logger.info(f"Table: {args.table_name}")
    logger.info(f"Use LLM: {args.use_llm}")
    logger.info(f"Dry run: {args.dry_run}")

    # Create clients
    logger.info("Creating DynamoDB client...")
    dynamo_client = DynamoClient(table_name=args.table_name)

    logger.info("Loading settings...")
    settings = get_settings()

    logger.info("Creating ChromaDB client...")
    if args.chroma_path:
        chroma_client = create_chroma_client(
            persist_directory=args.chroma_path,
            mode="read",
            settings=settings,
        )
    else:
        # Use settings to determine ChromaDB location
        chroma_client = create_chroma_client(
            mode="read",
            settings=settings,
        )

    if not chroma_client:
        logger.error("Failed to create ChromaDB client")
        logger.error("Make sure ChromaDB snapshots are available or set --chroma-path")
        sys.exit(1)

    logger.info("Creating embedding function...")
    embed_fn = create_embed_fn(settings=settings)

    if not embed_fn:
        logger.error("Failed to create embedding function")
        logger.error("Make sure OPENAI_API_KEY is set")
        sys.exit(1)

    # Create LLM if requested
    llm = None
    if args.use_llm:
        logger.info("Creating LLM...")
        llm = create_llm()
        if not llm:
            logger.warning("LLM not available - will use ChromaDB only")

    # Run suggestion workflow
    logger.info("Running label suggestion workflow...")
    try:
        result = await suggest_labels_for_receipt(
            image_id=args.image_id,
            receipt_id=args.receipt_id,
            dynamo_client=dynamo_client,
            chroma_client=chroma_client,
            embed_fn=embed_fn,
            llm=llm,
            settings=settings,
            max_llm_calls=10 if args.use_llm else 0,
            dry_run=args.dry_run,
        )

        # Print results
        print("\n" + "="*80)
        print("LABEL SUGGESTION RESULTS")
        print("="*80)
        print(f"Status: {result.get('status')}")
        print(f"Unlabeled words: {result.get('unlabeled_words_count', 0)}")
        print(f"Suggestions made: {result.get('suggestions_count', 0)}")
        print(f"LLM calls: {result.get('llm_calls', 0)}")
        print(f"Skipped (no candidates): {result.get('skipped_no_candidates', 0)}")
        print(f"Skipped (low confidence): {result.get('skipped_low_confidence', 0)}")

        # Show performance metrics
        perf = result.get('performance', {})
        if perf:
            total_time = perf.get('total_time_seconds', 0)
            time_per_word = perf.get('time_per_word_seconds', 0)
            avg_dynamo_get = perf.get('avg_dynamo_get_time', 0)
            avg_dynamo_list = perf.get('avg_dynamo_list_time', 0)
            avg_chroma_get = perf.get('avg_chroma_get_time', 0)
            avg_embedding = perf.get('avg_embedding_time', 0)
            avg_chroma_query = perf.get('avg_chroma_query_time', 0)
            total_queries = perf.get('total_chroma_queries', 0)
            print(f"\n⚡ Performance:")
            print(f"  Total time: {total_time:.2f}s")
            print(f"  Time per word: {time_per_word:.3f}s")
            print(f"  Breakdown:")
            print(f"    - DynamoDB get word: {avg_dynamo_get:.3f}s avg")
            print(f"    - DynamoDB list words: {avg_dynamo_list:.3f}s avg")
            print(f"    - ChromaDB get (stored embedding): {avg_chroma_get:.3f}s avg")
            print(f"    - On-the-fly embedding: {avg_embedding:.3f}s avg")
            print(f"    - ChromaDB similarity query: {avg_chroma_query:.3f}s avg")
            print(f"  Total ChromaDB queries: {total_queries}")
            if total_time > 0:
                unlabeled_count = result.get('unlabeled_words_count', 0)
                words_per_second = unlabeled_count / total_time if unlabeled_count > 0 else 0
                print(f"  Throughput: {words_per_second:.1f} words/second")

        # Show details of skipped words
        skipped_details = result.get('skipped_words_details', [])
        if skipped_details:
            print(f"\n📋 Skipped Words Analysis ({len(skipped_details)} words):")
            print("-" * 80)

            # Group by reason
            by_reason = {}
            for detail in skipped_details:
                reason = detail.get('reason', 'unknown')
                if reason not in by_reason:
                    by_reason[reason] = []
                by_reason[reason].append(detail)

            for reason, words in by_reason.items():
                print(f"\n{reason.upper().replace('_', ' ')}: {len(words)} words")
                print("  Examples:")
                for word_detail in words[:10]:  # Show first 10 examples
                    word_text = word_detail.get('word_text', 'N/A')
                    total_sim = word_detail.get('total_similar_words', 0)
                    with_valid = word_detail.get('words_with_valid_labels', 0)
                    without_valid = word_detail.get('words_without_valid_labels', 0)
                    print(f"    - '{word_text}': {total_sim} similar words found, "
                          f"{with_valid} with VALID labels, {without_valid} without")
                if len(words) > 10:
                    print(f"    ... and {len(words) - 10} more")

            # Summary statistics
            total_similar = sum(d.get('total_similar_words', 0) for d in skipped_details)
            total_with_valid = sum(d.get('words_with_valid_labels', 0) for d in skipped_details)
            total_without_valid = sum(d.get('words_without_valid_labels', 0) for d in skipped_details)

            print(f"\n📊 Summary:")
            print(f"  Total similar words found across all queries: {total_similar}")
            print(f"  Words with VALID labels: {total_with_valid}")
            print(f"  Words without VALID labels: {total_without_valid}")
            if total_similar > 0:
                pct_with_valid = (total_with_valid / total_similar) * 100
                print(f"  Percentage with VALID labels: {pct_with_valid:.1f}%")

        if "submit_result" in result:
            submit_result = result["submit_result"]
            print(f"\nCreated labels: {submit_result.get('created_count', 0)}")
            print(f"Errors: {submit_result.get('error_count', 0)}")

            if submit_result.get("created_labels"):
                print("\nCreated labels:")
                for label in submit_result["created_labels"]:
                    print(f"  - Word {label['word_id']} (line {label['line_id']}): "
                          f"{label['label_type']} (confidence: {label['confidence']:.2f})")

        if result.get("error"):
            print(f"\nError: {result['error']}")

        print("="*80)

    except Exception as e:
        logger.error(f"Error running label suggestion workflow: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

