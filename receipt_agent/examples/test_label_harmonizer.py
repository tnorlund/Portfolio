#!/usr/bin/env python3
"""
Example: Test Label Harmonizer

This script demonstrates how to use the LabelHarmonizer to harmonize
receipt word labels by merchant and label type.

Usage:
    python -m receipt_agent.examples.test_label_harmonizer --label-type "GRAND_TOTAL" --limit 5
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

# Add parent to path for local development
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from receipt_agent.tools.label_harmonizer import LabelHarmonizer
from receipt_agent.clients.factory import create_all_clients


def setup_environment():
    """Load secrets and outputs from Pulumi and set environment variables."""
    from receipt_dynamo.data._pulumi import load_env, load_secrets

    # Explicitly set the infra directory for Pulumi
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    infra_dir = os.path.join(repo_root, "infra")
    env = load_env("dev", working_dir=infra_dir)
    secrets = load_secrets("dev", working_dir=infra_dir)

    # Set environment variables for receipt_agent
    os.environ["RECEIPT_AGENT_DYNAMO_TABLE_NAME"] = env.get(
        "dynamodb_table_name", "receipts"
    )
    os.environ["RECEIPT_AGENT_AWS_REGION"] = secrets.get(
        "portfolio:aws-region", "us-west-2"
    )

    # ChromaDB - use separate directories for lines and words collections
    snapshot_base_dir = os.path.join(repo_root, ".chroma_snapshots")
    lines_snapshot_dir = os.path.join(snapshot_base_dir, "lines")
    words_snapshot_dir = os.path.join(snapshot_base_dir, "words")
    local_chroma = os.path.join(repo_root, ".chromadb_local", "words")  # Old local path

    # Prefer local snapshot directories (downloaded from S3)
    chroma_dirs_set = False
    if (os.path.exists(lines_snapshot_dir) and os.listdir(lines_snapshot_dir) and
            os.path.exists(words_snapshot_dir) and os.listdir(words_snapshot_dir)):
        os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = lines_snapshot_dir
        os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = words_snapshot_dir
        print(f"✅ ChromaDB snapshots found:")
        print(f"   Lines: {lines_snapshot_dir}")
        print(f"   Words: {words_snapshot_dir}")
        chroma_dirs_set = True
    elif os.path.exists(local_chroma):
        # Check if old local ChromaDB has both collections needed
        try:
            from receipt_chroma import ChromaClient
            with ChromaClient(persist_directory=local_chroma, mode="read") as client:
                collections = client.list_collections()
                has_lines = "lines" in collections
                has_words = "words" in collections

                if has_lines and has_words:
                    os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = local_chroma
                    os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = local_chroma
                    print(f"✅ ChromaDB local (old format): {local_chroma}")
                    print(f"   Collections: {', '.join(collections)}")
                    chroma_dirs_set = True
                else:
                    print(f"⚠️  Local ChromaDB missing required collections")
                    print(f"   Found: {', '.join(collections) if collections else 'none'}")
                    print(f"   Needed: lines, words")
        except Exception as e:
            print(f"⚠️  Could not check local ChromaDB: {e}")

    # If we don't have valid ChromaDB directories, download from S3
    if not chroma_dirs_set:
        bucket_name = (
            env.get("embedding_chromadb_bucket_name")
            or env.get("chromadb_bucket_name")
            or os.environ.get("CHROMADB_BUCKET")
        )

        if bucket_name:
            print(f"📥 Downloading ChromaDB snapshots from S3 bucket: {bucket_name}")
            print("   (Downloading 'lines' and 'words' collections to separate directories...)")
            try:
                from receipt_chroma import download_snapshot_atomic

                # Download lines collection
                lines_snapshot = download_snapshot_atomic(
                    bucket_name=bucket_name,
                    collection_name="lines",
                    target_dir=lines_snapshot_dir,
                )
                os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = lines_snapshot

                # Download words collection
                words_snapshot = download_snapshot_atomic(
                    bucket_name=bucket_name,
                    collection_name="words",
                    target_dir=words_snapshot_dir,
                )
                os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = words_snapshot

                print(f"✅ ChromaDB snapshots downloaded:")
                print(f"   Lines: {lines_snapshot}")
                print(f"   Words: {words_snapshot}")
            except Exception as e:
                print(f"⚠️  Could not download ChromaDB snapshots: {e}")
                print(f"   Continuing without ChromaDB (similarity search will be disabled)")
        else:
            print(f"⚠️  No ChromaDB bucket configured, continuing without ChromaDB")

    # API Keys
    openai_key = secrets.get("portfolio:OPENAI_API_KEY", "")
    if openai_key:
        os.environ["RECEIPT_AGENT_OPENAI_API_KEY"] = openai_key
        print("✅ OpenAI API key loaded")
    else:
        print("⚠️  OpenAI API key not found")

    google_places_key = secrets.get("portfolio:GOOGLE_PLACES_API_KEY", "")
    if google_places_key:
        os.environ["RECEIPT_AGENT_GOOGLE_PLACES_API_KEY"] = google_places_key
        os.environ["RECEIPT_PLACES_API_KEY"] = google_places_key
        print("✅ Google Places API key loaded")
    else:
        print("⚠️  Google Places API key not found")

    langchain_key = secrets.get("portfolio:LANGCHAIN_API_KEY", "")
    if langchain_key:
        os.environ["LANGCHAIN_API_KEY"] = langchain_key
        os.environ["RECEIPT_AGENT_LANGSMITH_API_KEY"] = langchain_key
        # Enable LangSmith tracing
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"
        os.environ["LANGCHAIN_PROJECT"] = "label-harmonizer"
        print("✅ LangSmith tracing enabled (project: label-harmonizer)")
    else:
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        print("⚠️  LangSmith API key not found - tracing disabled")

    ollama_key = secrets.get("portfolio:OLLAMA_API_KEY", "")
    if ollama_key:
        os.environ["RECEIPT_AGENT_OLLAMA_API_KEY"] = ollama_key
        print("✅ Ollama API key loaded")
    else:
        print("⚠️  Ollama API key not found")

    # Set receipt_places table name too
    os.environ["RECEIPT_PLACES_TABLE_NAME"] = env.get(
        "dynamodb_table_name", "receipts"
    )

    print(f"\n📊 DynamoDB Table: {env.get('dynamodb_table_name', 'receipts')}")


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


async def main(
    label_type: str,
    limit: Optional[int] = None,
    use_similarity: bool = True,
    max_merchants: Optional[int] = None,
    batch_size: int = 1000,
) -> None:
    """Run the label harmonizer."""
    print("=" * 70)
    print("LABEL HARMONIZER TEST")
    print("=" * 70)
    print(f"Label Type: {label_type}")
    print(f"Limit: {limit if limit else 'None (all merchants)'}")
    print(f"Max Merchants: {max_merchants if max_merchants else 'None (all merchants)'}")
    print(f"Batch Size: {batch_size}")
    print(f"Use Similarity: {use_similarity}")
    print()
    print("💡 Memory-efficient: Processing in batches to avoid loading")
    print("   thousands of labels into memory at once.")
    print()

    # Setup environment
    setup_environment()

    # Create clients
    print("Creating clients...")
    clients = create_all_clients()
    dynamo_client = clients["dynamo_client"]
    chroma_client = clients["chroma_client"]
    embed_fn = clients["embed_fn"]

    print("✅ Clients created")
    print()

    # Create harmonizer
    print("Creating LabelHarmonizer...")
    harmonizer = LabelHarmonizer(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
    )
    print("✅ Harmonizer created")
    print()

    # Run harmonization
    print(f"Running harmonization for {label_type}...")
    report = await harmonizer.harmonize_label_type(
        label_type=label_type,
        use_similarity=use_similarity,
        limit=limit,
        max_merchants=max_merchants,
        batch_size=batch_size,
    )

    # Print summary
    print()
    harmonizer.print_summary(report)

    # Show dry-run results
    print()
    print("=" * 70)
    print("DRY RUN - What Would Be Updated")
    print("=" * 70)
    result = await harmonizer.apply_fixes(dry_run=True, min_confidence=70.0, min_group_size=3)
    print(f"Total processed: {result.total_processed}")
    print(f"Would update: {result.total_updated}")
    print(f"Skipped: {result.total_skipped}")
    print(f"Failed: {result.total_failed}")
    if result.errors:
        print(f"Errors: {len(result.errors)}")
        for error in result.errors[:5]:
            print(f"  - {error}")

    print()
    print("=" * 70)
    print("✅ Harmonization complete (dry run - no changes made)")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Label Harmonizer")
    parser.add_argument(
        "--label-type",
        type=str,
        default="GRAND_TOTAL",
        help="CORE_LABEL type to harmonize (default: GRAND_TOTAL)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of merchant groups to process (default: all)",
    )
    parser.add_argument(
        "--no-similarity",
        action="store_true",
        help="Disable ChromaDB similarity clustering",
    )
    parser.add_argument(
        "--max-merchants",
        type=int,
        default=None,
        help="Limit number of merchants to process (memory-efficient, default: all)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Number of labels to process per batch (default: 1000)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    asyncio.run(main(
        label_type=args.label_type,
        limit=args.limit,
        use_similarity=not args.no_similarity,
        max_merchants=args.max_merchants,
        batch_size=args.batch_size,
    ))

