#!/usr/bin/env python3
"""
Example: Validate metadata for a single receipt.

This script demonstrates how to use the MetadataValidatorAgent
to validate receipt metadata using ChromaDB and DynamoDB.

Usage:
    python -m examples.validate_single_receipt <image_id> <receipt_id>

Example:
    python -m examples.validate_single_receipt abc-123-def 1
"""

import argparse
import asyncio
import logging
import os
import sys

# Add parent to path for local development
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from receipt_agent import MetadataValidatorAgent
from receipt_agent.config.settings import get_settings


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


async def main(image_id: str, receipt_id: int, verbose: bool = False) -> None:
    """Run validation for a single receipt."""
    setup_logging(verbose)
    logger = logging.getLogger(__name__)

    settings = get_settings()
    logger.info(f"Using Ollama model: {settings.ollama_model}")

    # Import the actual clients
    try:
        from receipt_dynamo.data.dynamo_client import DynamoClient
        from receipt_chroma.data.chroma_client import ChromaClient
    except ImportError as e:
        logger.error(
            f"Failed to import clients: {e}\n"
            "Make sure receipt_dynamo and receipt_chroma are installed."
        )
        sys.exit(1)

    # Initialize clients
    logger.info("Initializing clients...")

    dynamo = DynamoClient(table_name=settings.dynamo_table_name)
    logger.info(f"DynamoDB client initialized for table: {settings.dynamo_table_name}")

    chroma_path = settings.chroma_persist_directory
    if not chroma_path:
        logger.error("RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY not set")
        sys.exit(1)

    chroma = ChromaClient(persist_directory=chroma_path, mode="read")
    logger.info(f"ChromaDB client initialized at: {chroma_path}")

    # Optional: Google Places API
    places_api = None
    places_key = settings.google_places_api_key.get_secret_value()
    if places_key:
        try:
            from receipt_label.data.places_api import PlacesAPI
            places_api = PlacesAPI(api_key=places_key)
            logger.info("Google Places API configured")
        except ImportError:
            logger.warning("receipt_label not available - Places API disabled")

    # Create the validation agent
    logger.info("Creating MetadataValidatorAgent...")
    agent = MetadataValidatorAgent(
        dynamo_client=dynamo,
        chroma_client=chroma,
        places_api=places_api,
        enable_tracing=True,
    )

    # Run validation
    logger.info(f"Validating receipt: {image_id}#{receipt_id}")
    print("-" * 60)

    result = await agent.validate(
        image_id=image_id,
        receipt_id=receipt_id,
    )

    # Display results
    print("\n" + "=" * 60)
    print("VALIDATION RESULT")
    print("=" * 60)
    print(f"Status: {result.status.value.upper()}")
    print(f"Confidence: {result.confidence:.2%}")
    print(f"Timestamp: {result.timestamp.isoformat()}")
    print("-" * 60)

    if result.validated_merchant:
        print("\nValidated Merchant:")
        print(f"  Name: {result.validated_merchant.merchant_name}")
        print(f"  Place ID: {result.validated_merchant.place_id}")
        print(f"  Address: {result.validated_merchant.address}")
        print(f"  Phone: {result.validated_merchant.phone_number}")
        print(f"  Source: {result.validated_merchant.source}")

    print("\nVerification Steps:")
    for step in result.verification_steps:
        status = "✅" if step.passed else ("❌" if step.passed is False else "⏸️")
        print(f"  {status} {step.step_name}: {step.answer or 'N/A'}")

    if result.evidence_summary:
        print("\nEvidence Summary:")
        for evidence in result.evidence_summary[:5]:  # Limit to top 5
            print(f"  - {evidence.evidence_type.value}: {evidence.description}")
            print(f"    Confidence: {evidence.confidence:.2%}")

    print("\nReasoning:")
    print(f"  {result.reasoning}")

    if result.recommendations:
        print("\nRecommendations:")
        for rec in result.recommendations:
            print(f"  • {rec}")

    print("=" * 60)

    # Clean up
    chroma.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Validate receipt metadata using agentic search"
    )
    parser.add_argument("image_id", help="UUID of the receipt image")
    parser.add_argument("receipt_id", type=int, help="Receipt ID within the image")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    asyncio.run(main(args.image_id, args.receipt_id, args.verbose))

