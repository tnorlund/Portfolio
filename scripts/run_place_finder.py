#!/usr/bin/env python3
"""
Run the place finder agent on receipts missing places.

This script runs the place finder agent locally to debug why places
aren't being created for certain receipts.

Usage:
    python scripts/run_place_finder.py --env prod --image-id <uuid> --receipt-id <int>
    python scripts/run_place_finder.py --env prod --all-missing
    python scripts/run_place_finder.py --env prod --all-missing --dry-run
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))
sys.path.insert(0, os.path.join(parent_dir, "receipt_agent"))

from receipt_dynamo.data._pulumi import load_env, load_secrets
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.receipt_place import ReceiptPlace


def setup_environment(env: str) -> dict:
    """Load Pulumi config/secrets and set environment variables for receipt_agent.

    Returns the loaded config dict.
    """
    # Load infrastructure outputs
    config = load_env(env=env)

    # Load secrets from Pulumi
    secrets = load_secrets(env=env)

    # Map Pulumi secrets to receipt_agent environment variables
    secret_mappings = {
        "portfolio:OPENAI_API_KEY": "RECEIPT_AGENT_OPENAI_API_KEY",
        "portfolio:OPENROUTER_API_KEY": "OPENROUTER_API_KEY",
        "portfolio:GOOGLE_PLACES_API_KEY": "RECEIPT_AGENT_GOOGLE_PLACES_API_KEY",
        "portfolio:LANGCHAIN_API_KEY": "RECEIPT_AGENT_LANGCHAIN_API_KEY",
    }

    for pulumi_key, env_var in secret_mappings.items():
        if pulumi_key in secrets and secrets[pulumi_key]:
            os.environ[env_var] = secrets[pulumi_key]

    # Set infrastructure config
    if "dynamodb_table_name" in config:
        os.environ["RECEIPT_AGENT_DYNAMO_TABLE_NAME"] = config["dynamodb_table_name"]
        os.environ["RECEIPT_PLACES_TABLE_NAME"] = config["dynamodb_table_name"]

    # Set AWS region
    os.environ["RECEIPT_AGENT_AWS_REGION"] = "us-east-1"
    os.environ["RECEIPT_PLACES_AWS_REGION"] = "us-east-1"

    # Use OpenRouter instead of Ollama Cloud (no local Ollama server needed)
    os.environ["RECEIPT_AGENT_LLM_PROVIDER"] = "openrouter"

    return config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Known missing places in prod (as of 2025-12-29)
MISSING_PLACES = [
    ('13da1048-3888-429f-b2aa-b3e15341da5e', 1),
    ('1905dba2-c408-48e9-aa4d-f47fec0a3835', 1),
    ('23e1b623-e3fa-4102-84ca-4b5b3862e6ed', 2),
    ('53e716ed-49bf-4562-90e7-fc8c860cb985', 1),
    ('53e716ed-49bf-4562-90e7-fc8c860cb985', 2),
    ('6bfbbd7b-40af-4cb7-89c5-095796f9d1a5', 2),
    ('783f5ca8-dec8-4d72-bfc2-5196573942bb', 1),
    ('783f5ca8-dec8-4d72-bfc2-5196573942bb', 2),
    ('866ae94f-6274-4f02-a4ab-42eec72381fb', 1),
    ('866ae94f-6274-4f02-a4ab-42eec72381fb', 2),
    ('910efdda-b8f9-42e4-9666-ce76cf8371bf', 1),
    ('95ae3b59-238c-4725-a2b0-d428d09bb82a', 1),
    ('9efaf9b1-b39d-49c6-93f2-bc58e52387b0', 1),
    ('a1dacbc8-ab6a-40af-8146-b15d44a7aa81', 1),
    ('a63a6c84-d399-452f-9e56-08ff664c8f35', 1),
    ('abaec508-6730-4d75-9d48-76492a26a168', 1),
    ('b4791435-f9a7-4fb3-ac5f-35f8c933e3fe', 2),
    ('b4791435-f9a7-4fb3-ac5f-35f8c933e3fe', 3),
    ('baf2e4e5-fd03-4301-8f99-4a92b03ba40e', 1),
    ('baf2e4e5-fd03-4301-8f99-4a92b03ba40e', 2),
    ('c0900230-2907-4093-9bf7-29ca3cdf397a', 1),
    ('cb22100f-44c2-4b7d-b29f-46627a64355a', 2),
    ('cb22100f-44c2-4b7d-b29f-46627a64355a', 3),
    ('dea7bb6f-ea6d-4ae8-a365-02c79b00970f', 1),
    ('dea7bb6f-ea6d-4ae8-a365-02c79b00970f', 3),
    ('e26d6ebe-ace2-45e2-9a3c-830fb947f4c7', 1),
    ('e26d6ebe-ace2-45e2-9a3c-830fb947f4c7', 2),
]


async def run_place_finder_for_receipt(
    dynamo_client: DynamoClient,
    image_id: str,
    receipt_id: int,
    dry_run: bool = True,
    skip_existing: bool = True,
) -> dict:
    """Run the place finder agent for a single receipt."""
    # Check if place already exists
    if skip_existing:
        try:
            existing_place = dynamo_client.get_receipt_place(image_id, receipt_id)
            if existing_place and existing_place.merchant_name:
                logger.info(f"Skipping {image_id}#{receipt_id} - place already exists: {existing_place.merchant_name}")
                return {
                    "found": True,
                    "skipped": True,
                    "merchant_name": existing_place.merchant_name,
                    "place_id": existing_place.place_id,
                }
        except Exception:
            pass  # No existing place, continue processing

    from receipt_agent.config.settings import get_settings
    from receipt_agent.clients.factory import create_places_client, create_embed_fn
    from receipt_agent.subagents.place_finder.graph import (
        create_receipt_place_finder_graph,
        run_receipt_place_finder,
    )

    settings = get_settings()

    # Get receipt details
    logger.info(f"Getting receipt details for {image_id}#{receipt_id}")
    receipt_details = dynamo_client.get_receipt_details(
        image_id=image_id,
        receipt_id=receipt_id,
    )

    # Show receipt info
    logger.info(f"  Lines: {len(receipt_details.lines)}")
    logger.info(f"  Words: {len(receipt_details.words)}")

    # Skip empty receipts
    if len(receipt_details.lines) == 0 and len(receipt_details.words) == 0:
        logger.warning(f"Skipping {image_id}#{receipt_id} - empty receipt (0 lines, 0 words)")
        return {
            "found": False,
            "empty": True,
            "reasoning": "Empty receipt - no lines or words to process",
        }

    # Show first few lines for context
    logger.info("  First 5 lines:")
    for line in receipt_details.lines[:5]:
        logger.info(f"    {line.line_id}: {line.text[:60]}..." if len(line.text) > 60 else f"    {line.line_id}: {line.text}")

    # Create clients
    logger.info("Creating Places API client...")
    places_client = create_places_client(settings=settings)

    logger.info("Creating embedding function...")
    embed_fn = create_embed_fn(settings=settings)

    # Create the place finder graph (without ChromaDB for local testing)
    logger.info("Creating place finder graph...")
    graph, state_holder = create_receipt_place_finder_graph(
        dynamo_client=dynamo_client,
        chroma_client=None,  # Will use chromadb_bucket for lazy loading
        embed_fn=embed_fn,
        places_api=places_client,
        settings=settings,
        chromadb_bucket=None,  # No ChromaDB for local testing
    )

    # Run the place finder
    logger.info(f"Running place finder for {image_id}#{receipt_id}...")
    result = await run_receipt_place_finder(
        graph=graph,
        state_holder=state_holder,
        image_id=image_id,
        receipt_id=receipt_id,
        line_embeddings=None,
        word_embeddings=None,
        receipt_lines=receipt_details.lines,
        receipt_words=receipt_details.words,
    )

    # Display result
    logger.info("=" * 60)
    logger.info("PLACE FINDER RESULT")
    logger.info("=" * 60)
    logger.info(f"Found: {result.get('found', False)}")
    logger.info(f"Place ID: {result.get('place_id')}")
    logger.info(f"Merchant Name: {result.get('merchant_name')}")
    logger.info(f"Address: {result.get('address')}")
    logger.info(f"Phone: {result.get('phone_number')}")
    logger.info(f"Confidence: {result.get('confidence', 0):.2%}")
    logger.info(f"Reasoning: {result.get('reasoning', 'N/A')}")
    logger.info("=" * 60)

    # Save to DynamoDB if not dry run
    if not dry_run and result.get('found') and result.get('merchant_name'):
        merchant_name = result.get('merchant_name', '').strip()
        if merchant_name:
            matched_fields = []
            if result.get('merchant_name'):
                matched_fields.append('name')
            if result.get('address'):
                matched_fields.append('address')
            if result.get('phone_number'):
                matched_fields.append('phone')
            if result.get('place_id'):
                matched_fields.append('place_id')

            place_entity = ReceiptPlace(
                image_id=image_id,
                receipt_id=receipt_id,
                place_id=result.get('place_id') or '',
                merchant_name=merchant_name,
                matched_fields=matched_fields,
                timestamp=datetime.now(timezone.utc),
                merchant_category='',
                formatted_address=result.get('address') or '',
                phone_number=result.get('phone_number') or '',
                validated_by='INFERENCE',
                reasoning=result.get('reasoning') or '',
            )

            logger.info(f"Saving place to DynamoDB...")
            dynamo_client.add_receipt_places([place_entity])
            logger.info(f"Saved place for {image_id}#{receipt_id}")
        else:
            logger.warning("Merchant name is empty, not saving")
    elif dry_run:
        logger.info("[DRY RUN] Would save place to DynamoDB")
    else:
        logger.warning("Place not found or merchant_name missing, not saving")

    return result


async def run_all_missing(
    dynamo_client: DynamoClient,
    dry_run: bool = True,
    limit: int = None,
) -> dict:
    """Run place finder for all missing places."""
    results = {
        'skipped': [],
        'empty': [],
        'success': [],
        'failed': [],
        'errors': [],
    }

    receipts_to_process = MISSING_PLACES[:limit] if limit else MISSING_PLACES

    for i, (image_id, receipt_id) in enumerate(receipts_to_process):
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing {i+1}/{len(receipts_to_process)}: {image_id}#{receipt_id}")
        logger.info(f"{'='*60}")

        try:
            result = await run_place_finder_for_receipt(
                dynamo_client=dynamo_client,
                image_id=image_id,
                receipt_id=receipt_id,
                dry_run=dry_run,
            )

            if result.get('skipped'):
                results['skipped'].append((image_id, receipt_id, result))
            elif result.get('empty'):
                results['empty'].append((image_id, receipt_id, result))
            elif result.get('found') and result.get('merchant_name'):
                results['success'].append((image_id, receipt_id, result))
            else:
                results['failed'].append((image_id, receipt_id, result))

        except Exception as e:
            logger.exception(f"Error processing {image_id}#{receipt_id}")
            results['errors'].append((image_id, receipt_id, str(e)))

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Skipped (already exist): {len(results['skipped'])}")
    logger.info(f"Empty (no lines/words): {len(results['empty'])}")
    logger.info(f"Success (newly found): {len(results['success'])}")
    logger.info(f"Failed: {len(results['failed'])}")
    logger.info(f"Errors: {len(results['errors'])}")

    if results['skipped']:
        logger.info("\nSkipped receipts (already have places):")
        for image_id, receipt_id, result in results['skipped']:
            logger.info(f"  {image_id}#{receipt_id}: {result.get('merchant_name')}")

    if results['empty']:
        logger.info("\nEmpty receipts (no lines/words):")
        for image_id, receipt_id, result in results['empty']:
            logger.info(f"  {image_id}#{receipt_id}")

    if results['failed']:
        logger.info("\nFailed receipts:")
        for image_id, receipt_id, result in results['failed']:
            logger.info(f"  {image_id}#{receipt_id}: {result.get('reasoning', 'Unknown')[:50]}")

    if results['errors']:
        logger.info("\nError receipts:")
        for image_id, receipt_id, error in results['errors']:
            logger.info(f"  {image_id}#{receipt_id}: {error[:50]}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Run place finder agent on receipts missing places"
    )
    parser.add_argument(
        "--env",
        type=str,
        required=True,
        choices=["dev", "prod"],
        help="Environment (dev or prod)",
    )
    parser.add_argument(
        "--image-id",
        type=str,
        help="Image ID to process (use with --receipt-id)",
    )
    parser.add_argument(
        "--receipt-id",
        type=int,
        help="Receipt ID to process (use with --image-id)",
    )
    parser.add_argument(
        "--all-missing",
        action="store_true",
        help="Process all known missing places",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of receipts to process (use with --all-missing)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry run mode - don't save to DynamoDB (default: True)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_false",
        dest="dry_run",
        help="Actually save places to DynamoDB",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Validate args
    if args.image_id and not args.receipt_id:
        parser.error("--image-id requires --receipt-id")
    if args.receipt_id and not args.image_id:
        parser.error("--receipt-id requires --image-id")
    if not args.image_id and not args.all_missing:
        parser.error("Must specify either --image-id/--receipt-id or --all-missing")

    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info(f"Mode: {mode}")
    logger.info(f"Environment: {args.env.upper()}")

    # Load config/secrets and set up environment variables
    config = setup_environment(env=args.env)
    logger.info(f"Loaded {len(config)} config values from Pulumi")

    # Create DynamoDB client
    dynamo = DynamoClient(config["dynamodb_table_name"])

    if args.all_missing:
        asyncio.run(run_all_missing(
            dynamo_client=dynamo,
            dry_run=args.dry_run,
            limit=args.limit,
        ))
    else:
        asyncio.run(run_place_finder_for_receipt(
            dynamo_client=dynamo,
            image_id=args.image_id,
            receipt_id=args.receipt_id,
            dry_run=args.dry_run,
        ))


if __name__ == "__main__":
    main()
