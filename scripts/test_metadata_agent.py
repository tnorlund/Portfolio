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

    env = load_env("dev")
    secrets = load_secrets("dev")

    # Set environment variables for receipt_agent
    os.environ["RECEIPT_AGENT_DYNAMO_TABLE_NAME"] = env.get(
        "dynamodb_table_name", "receipts"
    )
    os.environ["RECEIPT_AGENT_AWS_REGION"] = secrets.get(
        "portfolio:aws-region", "us-west-2"
    )

    # ChromaDB - use local path if available, otherwise HTTP URL
    local_chroma = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), ".chromadb_local", "words"
    )
    if os.path.exists(local_chroma):
        os.environ["RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY"] = local_chroma
        print(f"✅ ChromaDB local: {local_chroma}")
    else:
        chroma_dns = env.get("chroma_service_dns")
        if chroma_dns:
            # Remove any existing port from DNS name
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

    # Try to create ChromaDB client - prefer local
    chroma_client = None
    local_chroma = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), ".chromadb_local", "words"
    )
    if os.path.exists(local_chroma):
        try:
            from receipt_chroma.data.chroma_client import ChromaClient

            chroma_client = ChromaClient(
                persist_directory=local_chroma,
                mode="read",
            )
            print(f"✅ ChromaDB client connected to local: {local_chroma}")
        except Exception as e:
            print(f"⚠️  Could not connect to local ChromaDB: {e}")
    elif env.get("chroma_service_dns"):
        chroma_dns = env.get("chroma_service_dns")
        try:
            from receipt_chroma.data.chroma_client import ChromaClient

            # Remove any existing port from DNS name
            chroma_host = chroma_dns.split(":")[0]
            chroma_client = ChromaClient(
                http_url=f"http://{chroma_host}:8000",
                mode="read",
            )
            print(f"✅ ChromaDB client connected to http://{chroma_host}:8000")
        except Exception as e:
            print(f"⚠️  Could not connect to ChromaDB: {e}")

    # Create Places client (optional)
    places_client = None
    google_key = secrets.get("portfolio:GOOGLE_PLACES_API_KEY", "")
    if google_key:
        try:
            from receipt_places import PlacesClient

            places_client = PlacesClient(api_key=google_key)
            print("✅ Places client created")
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

