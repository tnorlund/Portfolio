#!/usr/bin/env python3
"""
Example: Batch validate metadata for multiple receipts.

This script demonstrates how to use the MetadataValidatorAgent
for batch validation with concurrency control.

Usage:
    python -m receipt_agent.examples.batch_validate --merchant "Starbucks" --limit 10

Example:
    python -m receipt_agent.examples.batch_validate --merchant "Target" --limit 5 --concurrency 3
"""

import argparse
import asyncio
import logging
import os
import sys
from collections import Counter
from pathlib import Path

# Add parent to path for local development
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from receipt_agent.api import MetadataValidatorAgent
from receipt_agent.state.models import ValidationStatus


def setup_environment():
    """Load secrets and outputs from Pulumi and set environment variables."""
    from receipt_dynamo.data._pulumi import load_env, load_secrets

    # Explicitly set the infra directory for Pulumi
    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
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
    local_chroma = os.path.join(
        repo_root, ".chromadb_local", "words"
    )  # Old local path

    # Prefer local snapshot directories (downloaded from S3)
    chroma_dirs_set = False
    if (
        os.path.exists(lines_snapshot_dir)
        and os.listdir(lines_snapshot_dir)
        and os.path.exists(words_snapshot_dir)
        and os.listdir(words_snapshot_dir)
    ):
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

            with ChromaClient(
                persist_directory=local_chroma, mode="read"
            ) as client:
                collections = client.list_collections()
                has_lines = "lines" in collections
                has_words = "words" in collections

                if has_lines and has_words:
                    os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = (
                        local_chroma
                    )
                    os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = (
                        local_chroma
                    )
                    print(f"✅ ChromaDB local (old format): {local_chroma}")
                    print(f"   Collections: {', '.join(collections)}")
                    chroma_dirs_set = True
                else:
                    print(f"⚠️  Local ChromaDB missing required collections")
                    print(
                        f"   Found: {', '.join(collections) if collections else 'none'}"
                    )
                    print(f"   Needed: lines, words")
                    print(f"   Will download from S3...")
        except Exception as e:
            print(f"⚠️  Could not check local ChromaDB: {e}")
            print(f"   Will download from S3...")

    # If we don't have valid ChromaDB directories, download from S3
    if not chroma_dirs_set:
        bucket_name = (
            env.get("embedding_chromadb_bucket_name")
            or env.get("chromadb_bucket_name")
            or os.environ.get("CHROMADB_BUCKET")
        )

        if bucket_name:
            print(
                f"📥 Downloading ChromaDB snapshots from S3 bucket: {bucket_name}"
            )
            print(
                "   (Downloading 'lines' and 'words' collections to separate directories...)"
            )
            try:
                import shutil
                import tempfile

                from receipt_chroma import download_snapshot_atomic

                os.makedirs(lines_snapshot_dir, exist_ok=True)
                os.makedirs(words_snapshot_dir, exist_ok=True)

                lines_temp = tempfile.mkdtemp(prefix="chroma_lines_")
                words_temp = tempfile.mkdtemp(prefix="chroma_words_")

                try:
                    # Download lines collection to temp directory
                    lines_result = download_snapshot_atomic(
                        bucket=bucket_name,
                        collection="lines",
                        local_path=lines_temp,
                        verify_integrity=True,
                    )

                    if lines_result.get("status") == "downloaded":
                        print(
                            f"✅ Lines collection downloaded: {lines_result.get('version_id', 'unknown')}"
                        )
                        # Copy to final destination
                        if os.path.exists(lines_snapshot_dir):
                            shutil.rmtree(lines_snapshot_dir)
                        shutil.copytree(lines_temp, lines_snapshot_dir)
                        print(
                            f"   Copied lines collection to {lines_snapshot_dir}"
                        )
                    else:
                        print(
                            f"⚠️  Lines download failed: {lines_result.get('error', 'unknown error')}"
                        )

                    # Download words collection to temp directory
                    words_result = download_snapshot_atomic(
                        bucket=bucket_name,
                        collection="words",
                        local_path=words_temp,
                        verify_integrity=True,
                    )

                    if words_result.get("status") == "downloaded":
                        print(
                            f"✅ Words collection downloaded: {words_result.get('version_id', 'unknown')}"
                        )
                        # Copy to final destination
                        if os.path.exists(words_snapshot_dir):
                            shutil.rmtree(words_snapshot_dir)
                        shutil.copytree(words_temp, words_snapshot_dir)
                        print(
                            f"   Copied words collection to {words_snapshot_dir}"
                        )
                    else:
                        print(
                            f"⚠️  Words download failed: {words_result.get('error', 'unknown error')}"
                        )

                    # Verify both collections are available
                    if (
                        lines_result.get("status") == "downloaded"
                        and words_result.get("status") == "downloaded"
                    ):
                        # Verify the collections exist in their respective directories
                        try:
                            from receipt_chroma import ChromaClient

                            with ChromaClient(
                                persist_directory=lines_snapshot_dir,
                                mode="read",
                            ) as lines_client:
                                lines_collections = (
                                    lines_client.list_collections()
                                )
                            with ChromaClient(
                                persist_directory=words_snapshot_dir,
                                mode="read",
                            ) as words_client:
                                words_collections = (
                                    words_client.list_collections()
                                )

                            if (
                                "lines" in lines_collections
                                and "words" in words_collections
                            ):
                                os.environ[
                                    "RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"
                                ] = lines_snapshot_dir
                                os.environ[
                                    "RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"
                                ] = words_snapshot_dir
                                print(f"✅ ChromaDB snapshots ready:")
                                print(
                                    f"   Lines: {lines_snapshot_dir} (Collections: {', '.join(lines_collections)})"
                                )
                                print(
                                    f"   Words: {words_snapshot_dir} (Collections: {', '.join(words_collections)})"
                                )
                            else:
                                print(
                                    f"⚠️  Collections not found after download (lines: {', '.join(lines_collections)}, words: {', '.join(words_collections)})"
                                )
                        except Exception as e:
                            print(
                                f"⚠️  Could not verify collections after download: {e}"
                            )
                            import traceback

                            traceback.print_exc()
                    else:
                        print(
                            f"⚠️  One or both downloads failed, falling back to HTTP URL if available"
                        )
                        chroma_dns = env.get("chroma_service_dns")
                        if chroma_dns:
                            chroma_host = chroma_dns.split(":")[0]
                            os.environ["RECEIPT_AGENT_CHROMA_HTTP_URL"] = (
                                f"http://{chroma_host}:8000"
                            )
                            print(
                                f"✅ ChromaDB URL: http://{chroma_host}:8000"
                            )
                        else:
                            print(
                                "⚠️  ChromaDB not available - will fail if needed"
                            )
                finally:
                    shutil.rmtree(lines_temp, ignore_errors=True)
                    shutil.rmtree(words_temp, ignore_errors=True)

            except Exception as e:
                print(f"⚠️  Failed to download snapshot: {e}")
                import traceback

                traceback.print_exc()
                # Fall back to HTTP URL if available
                chroma_dns = env.get("chroma_service_dns")
                if chroma_dns:
                    chroma_host = chroma_dns.split(":")[0]
                    os.environ["RECEIPT_AGENT_CHROMA_HTTP_URL"] = (
                        f"http://{chroma_host}:8000"
                    )
                    print(f"✅ ChromaDB URL: http://{chroma_host}:8000")
                else:
                    print("⚠️  ChromaDB not available - will fail if needed")
        else:
            # Fall back to HTTP URL if available
            chroma_dns = env.get("chroma_service_dns")
            if chroma_dns:
                chroma_host = chroma_dns.split(":")[0]
                os.environ["RECEIPT_AGENT_CHROMA_HTTP_URL"] = (
                    f"http://{chroma_host}:8000"
                )
                print(f"✅ ChromaDB URL: http://{chroma_host}:8000")
            else:
                print("⚠️  ChromaDB not found - will fail if needed")

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
        print("✅ LangSmith API key loaded")
    else:
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

    return env, secrets


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Reduce noise from some loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


async def main(
    merchant_name: str,
    limit: int,
    concurrency: int,
    verbose: bool,
) -> None:
    """Run batch validation for receipts from a merchant."""
    setup_logging(verbose)
    logger = logging.getLogger(__name__)

    print("=" * 60)
    print("🧪 Batch Metadata Validation")
    print("=" * 60)

    # Setup environment (load Pulumi config, download ChromaDB, etc.)
    env, secrets = setup_environment()

    # Import clients
    try:
        from receipt_dynamo.data.dynamo_client import DynamoClient
    except ImportError as e:
        logger.error(f"Failed to import clients: {e}")
        sys.exit(1)

    # Initialize DynamoDB client
    dynamo = DynamoClient(
        table_name=env.get("dynamodb_table_name", "receipts")
    )

    # Create ChromaDB client using factory (handles separate lines/words directories)
    chroma_client = None
    try:
        from receipt_agent.clients.factory import create_chroma_client
        from receipt_agent.config.settings import get_settings

        settings = get_settings()
        chroma_client = create_chroma_client(
            mode="read",
            settings=settings,
        )
        if chroma_client:
            print("✅ ChromaDB client created")
        else:
            print("⚠️  ChromaDB client creation returned None")
    except Exception as e:
        logger.warning(f"Could not create ChromaDB client: {e}")
        import traceback

        traceback.print_exc()

    # Create Places client (optional)
    places_client = None
    google_key = secrets.get("portfolio:GOOGLE_PLACES_API_KEY", "")
    if google_key:
        try:
            from receipt_agent.clients.factory import create_places_client
            from receipt_agent.config.settings import get_settings

            settings = get_settings()
            places_client = create_places_client(
                api_key=google_key,
                table_name=env.get("dynamodb_table_name", "receipts"),
                settings=settings,
            )
            if places_client:
                print("✅ Places client created")
            else:
                print("⚠️  Places client creation returned None")
        except Exception as e:
            logger.warning(f"Could not create Places client: {e}")

    # Find receipts for this merchant
    logger.info(f"Finding receipts for merchant: {merchant_name}")
    metadatas, _ = dynamo.get_receipt_metadatas_by_merchant(
        merchant_name=merchant_name,
        limit=limit,
    )

    if not metadatas:
        print(f"\n❌ No receipts found for merchant: {merchant_name}")
        return

    receipts = [(m.image_id, m.receipt_id) for m in metadatas]
    print(f"\n✅ Found {len(receipts)} receipts to validate")

    # Create agent
    print("\n🚀 Initializing MetadataValidatorAgent...")
    agent = MetadataValidatorAgent(
        dynamo_client=dynamo,
        chroma_client=chroma_client,
        places_api=places_client,
        enable_tracing=bool(secrets.get("portfolio:LANGCHAIN_API_KEY")),
    )
    print("✅ MetadataValidatorAgent created")

    # Run batch validation
    print(f"\n🔄 Validating with concurrency={concurrency}...")
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
        print(
            f"  {emoji} {image_id[:8]}...#{receipt_id}: {result.status.value} ({result.confidence:.0%})"
        )

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
            print(f"  - {image_id[:8]}...#{receipt_id}")
            if result.recommendations:
                for rec in result.recommendations[:2]:
                    print(f"    → {rec}")

    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Batch validate receipt metadata for a merchant"
    )
    parser.add_argument(
        "--merchant",
        "-m",
        required=True,
        help="Merchant name to validate",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=10,
        help="Maximum receipts to validate",
    )
    parser.add_argument(
        "--concurrency",
        "-c",
        type=int,
        default=5,
        help="Maximum concurrent validations",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging",
    )

    args = parser.parse_args()

    asyncio.run(
        main(
            args.merchant,
            args.limit,
            args.concurrency,
            args.verbose,
        )
    )
