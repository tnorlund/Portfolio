#!/usr/bin/env python3
"""
Test script for the MetadataValidatorAgent.

Loads configuration from Pulumi and runs a validation test.
"""

import asyncio
import logging
import os
import sys

# Add the repo root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def setup_environment():
    """Load secrets and outputs from Pulumi and set environment variables."""
    from receipt_dynamo.data._pulumi import load_env, load_secrets
    from pathlib import Path

    # Explicitly set the infra directory for Pulumi
    infra_dir = Path(__file__).parent.parent / "infra"
    env = load_env("dev", working_dir=str(infra_dir))
    secrets = load_secrets("dev", working_dir=str(infra_dir))

    # Set environment variables for receipt_agent
    os.environ["RECEIPT_AGENT_DYNAMO_TABLE_NAME"] = env.get(
        "dynamodb_table_name", "receipts"
    )
    os.environ["RECEIPT_AGENT_AWS_REGION"] = secrets.get(
        "portfolio:aws-region", "us-west-2"
    )

    # ChromaDB - use separate directories for lines and words collections
    # Each collection is stored in its own ChromaDB database directory
    repo_root = os.path.dirname(os.path.dirname(__file__))
    snapshot_base_dir = os.path.join(repo_root, ".chroma_snapshots")
    lines_snapshot_dir = os.path.join(snapshot_base_dir, "lines")
    words_snapshot_dir = os.path.join(snapshot_base_dir, "words")
    local_lines = os.path.join(repo_root, ".chromadb_local", "lines")
    local_words = os.path.join(repo_root, ".chromadb_local", "words")

    # Check for existing snapshots in separate directories
    has_lines_snapshot = os.path.exists(lines_snapshot_dir) and os.listdir(lines_snapshot_dir)
    has_words_snapshot = os.path.exists(words_snapshot_dir) and os.listdir(words_snapshot_dir)
    has_local_lines = os.path.exists(local_lines) and os.listdir(local_lines)
    has_local_words = os.path.exists(local_words) and os.listdir(local_words)

    chroma_dirs_set = False
    if has_lines_snapshot and has_words_snapshot:
        os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = lines_snapshot_dir
        os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = words_snapshot_dir
        print(f"✅ ChromaDB snapshots found:")
        print(f"   Lines: {lines_snapshot_dir}")
        print(f"   Words: {words_snapshot_dir}")
        chroma_dirs_set = True
    elif has_local_lines and has_local_words:
        os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = local_lines
        os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = local_words
        print(f"✅ ChromaDB local found:")
        print(f"   Lines: {local_lines}")
        print(f"   Words: {local_words}")
        chroma_dirs_set = True
    else:
        missing = []
        if not has_lines_snapshot and not has_local_lines:
            missing.append("lines")
        if not has_words_snapshot and not has_local_words:
            missing.append("words")
        if missing:
            print(f"⚠️  Missing ChromaDB collections: {', '.join(missing)}")
            print(f"   Will download from S3...")

    # If we don't have valid ChromaDB directories, download from S3
    if not chroma_dirs_set:
        # Try to download from S3 using bucket name from Pulumi outputs
        # Prefer embedding_chromadb_bucket_name (the one actually used for embeddings)
        # Fall back to chromadb_bucket_name or environment variable
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

                # Download lines collection to its own directory
                if not has_lines_snapshot and not has_local_lines:
                    os.makedirs(lines_snapshot_dir, exist_ok=True)
                    lines_result = download_snapshot_atomic(
                        bucket=bucket_name,
                        collection="lines",
                        local_path=lines_snapshot_dir,
                        verify_integrity=True,
                    )

                    if lines_result.get("status") == "downloaded":
                        print(f"✅ Lines collection downloaded: {lines_result.get('version_id', 'unknown')}")
                        os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = lines_snapshot_dir
                    else:
                        print(f"⚠️  Lines download failed: {lines_result.get('error', 'unknown error')}")
                else:
                    print(f"✅ Lines collection already available")

                # Download words collection to its own directory
                if not has_words_snapshot and not has_local_words:
                    os.makedirs(words_snapshot_dir, exist_ok=True)
                    words_result = download_snapshot_atomic(
                        bucket=bucket_name,
                        collection="words",
                        local_path=words_snapshot_dir,
                        verify_integrity=True,
                    )

                    if words_result.get("status") == "downloaded":
                        print(f"✅ Words collection downloaded: {words_result.get('version_id', 'unknown')}")
                        os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = words_snapshot_dir
                    else:
                        print(f"⚠️  Words download failed: {words_result.get('error', 'unknown error')}")
                else:
                    print(f"✅ Words collection already available")

                # Verify both directories are set
                if os.environ.get("RECEIPT_AGENT_CHROMA_LINES_DIRECTORY") and os.environ.get("RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"):
                    print(f"✅ ChromaDB snapshots ready:")
                    print(f"   Lines: {os.environ.get('RECEIPT_AGENT_CHROMA_LINES_DIRECTORY')}")
                    print(f"   Words: {os.environ.get('RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY')}")
                else:
                    # Fall back to HTTP URL if available
                    chroma_dns = env.get("chroma_service_dns")
                    if chroma_dns:
                        chroma_host = chroma_dns.split(":")[0]
                        os.environ["RECEIPT_AGENT_CHROMA_HTTP_URL"] = f"http://{chroma_host}:8000"
                        print(f"✅ ChromaDB URL: http://{chroma_host}:8000")
                    else:
                        print("⚠️  ChromaDB not available - will fail if needed")
            except Exception as e:
                print(f"⚠️  Failed to download snapshot: {e}")
                import traceback
                traceback.print_exc()
                # Fall back to HTTP URL if available
                chroma_dns = env.get("chroma_service_dns")
                if chroma_dns:
                    chroma_host = chroma_dns.split(":")[0]
                    os.environ["RECEIPT_AGENT_CHROMA_HTTP_URL"] = f"http://{chroma_host}:8000"
                    print(f"✅ ChromaDB URL: http://{chroma_host}:8000")
                else:
                    print("⚠️  ChromaDB not available - will fail if needed")
        else:
            # Fall back to HTTP URL if available
            chroma_dns = env.get("chroma_service_dns")
            if chroma_dns:
                chroma_host = chroma_dns.split(":")[0]
                os.environ["RECEIPT_AGENT_CHROMA_HTTP_URL"] = f"http://{chroma_host}:8000"
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


def get_sample_receipt(dynamo_client):
    """Get a sample receipt with metadata for testing."""
    print("\n🔍 Looking for receipts with metadata...")

    try:
        # Use the proper DynamoClient method to list receipt metadatas
        metadatas, _ = dynamo_client.list_receipt_metadatas(limit=10)

        if metadatas:
            # Find one with a merchant name and UNSURE status (good for validation)
            for m in metadatas:
                if m.merchant_name and m.validation_status == "UNSURE":
                    print(f"✅ Found receipt: image_id={m.image_id[:8]}..., receipt_id={m.receipt_id}")
                    print(f"   Merchant: {m.merchant_name}")
                    print(f"   Place ID: {m.place_id[:20] if m.place_id else 'None'}...")
                    print(f"   Status: {m.validation_status}")
                    return m.image_id, m.receipt_id

            # Fall back to first one with a merchant name
            for m in metadatas:
                if m.merchant_name:
                    print(f"✅ Found receipt: image_id={m.image_id[:8]}..., receipt_id={m.receipt_id}")
                    print(f"   Merchant: {m.merchant_name}")
                    print(f"   Place ID: {m.place_id[:20] if m.place_id else 'None'}...")
                    print(f"   Status: {m.validation_status}")
                    return m.image_id, m.receipt_id

            # Fall back to first one
            m = metadatas[0]
            print(f"✅ Found receipt: image_id={m.image_id[:8]}..., receipt_id={m.receipt_id}")
            print(f"   Merchant: {m.merchant_name or 'N/A'}")
            print(f"   Status: {m.validation_status}")
            return m.image_id, m.receipt_id

        print("⚠️  No receipts with metadata found")
        return None, None

    except Exception as e:
        print(f"❌ Error querying DynamoDB: {e}")
        import traceback
        traceback.print_exc()
        return None, None


async def test_agent(image_id: str, receipt_id: int, env: dict, secrets: dict):
    """Run the metadata validation agent."""
    from receipt_agent.agent.metadata_validator import MetadataValidatorAgent
    from receipt_dynamo.data.dynamo_client import DynamoClient

    print("\n🚀 Initializing MetadataValidatorAgent...")

    # Create DynamoDB client
    dynamo_client = DynamoClient(
        table_name=env.get("dynamodb_table_name", "receipts")
    )

    # Create ChromaDB clients - use separate directories for lines and words
    # Create a wrapper that routes queries to the correct client based on collection_name
    class DualChromaClient:
        """Wrapper client that routes queries to separate lines/words clients."""
        def __init__(self, lines_client, words_client):
            self.lines_client = lines_client
            self.words_client = words_client

        def query(self, collection_name, **kwargs):
            """Route query to the appropriate client based on collection_name."""
            if collection_name == "lines":
                return self.lines_client.query(collection_name="lines", **kwargs)
            elif collection_name == "words":
                return self.words_client.query(collection_name="words", **kwargs)
            else:
                raise ValueError(f"Unknown collection: {collection_name}")

        def get(self, collection_name, **kwargs):
            """Route get to the appropriate client based on collection_name."""
            if collection_name == "lines":
                return self.lines_client.get(collection_name="lines", **kwargs)
            elif collection_name == "words":
                return self.words_client.get(collection_name="words", **kwargs)
            else:
                raise ValueError(f"Unknown collection: {collection_name}")

        def list_collections(self):
            """Return both collections."""
            return ["lines", "words"]

        def get_collection(self, collection_name, **kwargs):
            """Route get_collection to the appropriate client."""
            if collection_name == "lines":
                return self.lines_client.get_collection("lines", **kwargs)
            elif collection_name == "words":
                return self.words_client.get_collection("words", **kwargs)
            else:
                raise ValueError(f"Unknown collection: {collection_name}")

    chroma_client = None
    repo_root = os.path.dirname(os.path.dirname(__file__))

    # Check for separate directories first (new approach)
    lines_dir = os.environ.get("RECEIPT_AGENT_CHROMA_LINES_DIRECTORY")
    words_dir = os.environ.get("RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY")
    http_url = os.environ.get("RECEIPT_AGENT_CHROMA_HTTP_URL")

    # Fallback to combined directory (old approach)
    persist_dir = os.environ.get("RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY")

    if lines_dir and words_dir:
        # Use separate clients for lines and words
        try:
            from receipt_agent.clients.factory import create_chroma_client
            from receipt_agent.config.settings import get_settings

            settings = get_settings()
            lines_client = create_chroma_client(
                persist_directory=lines_dir,
                mode="read",
                settings=settings,
            )
            words_client = create_chroma_client(
                persist_directory=words_dir,
                mode="read",
                settings=settings,
            )
            chroma_client = DualChromaClient(lines_client, words_client)
            print(f"✅ ChromaDB clients created:")
            print(f"   Lines: {lines_dir}")
            print(f"   Words: {words_dir}")
        except Exception as e:
            print(f"⚠️  Failed to create separate ChromaDB clients: {e}")
            import traceback
            traceback.print_exc()
    elif persist_dir:
        # Fallback to combined directory (old approach)
        try:
            from receipt_agent.clients.factory import create_chroma_client
            from receipt_agent.config.settings import get_settings

            settings = get_settings()
            chroma_client = create_chroma_client(
                persist_directory=persist_dir,
                mode="read",
                settings=settings,
            )
            if chroma_client:
                print(f"✅ ChromaDB client connected to: {persist_dir}")
            else:
                print(f"⚠️  ChromaDB client creation returned None")
        except Exception as e:
            print(f"⚠️  Could not connect to ChromaDB: {e}")
            import traceback
            traceback.print_exc()
    elif http_url:
        try:
            from receipt_agent.clients.factory import create_chroma_client
            from receipt_agent.config.settings import get_settings

            settings = get_settings()
            chroma_client = create_chroma_client(
                http_url=http_url,
                mode="read",
                settings=settings,
            )
            if chroma_client:
                print(f"✅ ChromaDB client connected to: {http_url}")
            else:
                print(f"⚠️  ChromaDB client creation returned None")
        except Exception as e:
            print(f"⚠️  Could not connect to ChromaDB: {e}")

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
            print(f"⚠️  Could not create Places client: {e}")

    # Create the agent
    try:
        agent = MetadataValidatorAgent(
            dynamo_client=dynamo_client,
            chroma_client=chroma_client,
            places_api=places_client,
            enable_tracing=bool(secrets.get("portfolio:LANGCHAIN_API_KEY")),
        )
        print("✅ MetadataValidatorAgent created")
    except Exception as e:
        print(f"❌ Failed to create agent: {e}")
        raise

    # Run validation
    print(f"\n🔄 Validating receipt: image_id={image_id[:8]}..., receipt_id={receipt_id}")
    print("-" * 50)

    result = await agent.validate(
        image_id=image_id,
        receipt_id=receipt_id,
    )

    print("-" * 50)
    print("\n📋 Validation Result:")
    print(f"   Status: {result.status.value}")
    print(f"   Confidence: {result.confidence:.2%}")
    print(f"   Reasoning: {result.reasoning}")

    if hasattr(result, "matched_fields") and result.matched_fields:
        print(f"   Matched Fields: {result.matched_fields}")

    if hasattr(result, "similar_receipts") and result.similar_receipts:
        print(f"   Similar Receipts: {len(result.similar_receipts)}")

    return result


def main():
    """Main entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Reduce noise from some loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    print("=" * 60)
    print("🧪 MetadataValidatorAgent Test")
    print("=" * 60)

    # Setup environment
    env, secrets = setup_environment()

    # Create DynamoDB client to find a sample receipt
    from receipt_dynamo.data.dynamo_client import DynamoClient

    dynamo_client = DynamoClient(
        table_name=env.get("dynamodb_table_name", "receipts")
    )

    # Get a sample receipt
    image_id, receipt_id = get_sample_receipt(dynamo_client)

    if not image_id:
        print("\n❌ No receipt found to test. Exiting.")
        return 1

    # Allow override from command line
    if len(sys.argv) >= 3:
        image_id = sys.argv[1]
        receipt_id = int(sys.argv[2])
        print(f"\n📝 Using command line args: image_id={image_id}, receipt_id={receipt_id}")

    # Run the test
    try:
        result = asyncio.run(test_agent(image_id, receipt_id, env, secrets))
        print("\n✅ Test completed successfully!")
        return 0
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

