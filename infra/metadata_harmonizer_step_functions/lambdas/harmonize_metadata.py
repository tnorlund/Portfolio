"""
Harmonize Metadata Handler (Container Lambda)

Processes a batch of place_ids using MerchantHarmonizerV3 to ensure
metadata consistency across receipts sharing the same Google Place ID.

Uses MerchantHarmonizerV3 which uses an LLM agent for intelligent reasoning.

Dependencies (via pyproject.toml):
- receipt_agent: MerchantHarmonizerV3
- receipt_dynamo: DynamoClient
- receipt_places: PlacesClient (optional, for Google Places validation)

Environment Variables (set by infrastructure.py):
- Lambda-specific:
  - BATCH_BUCKET: S3 bucket for batch files (not used, but kept for consistency)
- receipt_agent Settings (RECEIPT_AGENT_* prefix):
  - RECEIPT_AGENT_DYNAMO_TABLE_NAME: DynamoDB table name
  - RECEIPT_AGENT_OPENAI_API_KEY: OpenAI API key (for agent LLM)
  - RECEIPT_AGENT_OLLAMA_API_KEY: Ollama API key (for agent LLM)
  - RECEIPT_AGENT_OLLAMA_BASE_URL: Ollama API base URL
  - RECEIPT_AGENT_OLLAMA_MODEL: Ollama model name
- Google Places (optional):
  - GOOGLE_PLACES_API_KEY: Google Places API key for validation
- LangSmith tracing:
  - LANGCHAIN_API_KEY: LangSmith API key
  - LANGCHAIN_TRACING_V2: Enable tracing
  - LANGCHAIN_ENDPOINT: LangSmith endpoint
  - LANGCHAIN_PROJECT: LangSmith project name
"""

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import boto3
from utils.emf_metrics import emf_metrics

# LangSmith tracing - ensure traces are flushed before Lambda exits
try:
    from langsmith.run_trees import get_cached_client as get_langsmith_client

    HAS_LANGSMITH = True
except ImportError:
    HAS_LANGSMITH = False
    get_langsmith_client = None  # type: ignore


def flush_langsmith_traces():
    """
    Flush all pending LangSmith traces to the API.

    Must be called before Lambda returns to prevent traces from getting
    stuck as "pending" in LangSmith. Lambda freezes/terminates background
    threads after returning, so we must explicitly flush.
    """
    if HAS_LANGSMITH and get_langsmith_client:
        try:
            client = get_langsmith_client()
            client.flush()
            logger.info("LangSmith traces flushed successfully")
        except Exception as e:
            logger.warning(f"Failed to flush LangSmith traces: {e}")


logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Suppress noisy HTTP request logs from httpx/httpcore (used by langchain-ollama)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

s3 = boto3.client("s3")


async def process_place_id_batch(
    place_ids: List[str],
    dynamo_client: Any,
    places_client: Optional[Any],
    dry_run: bool = True,
    execution_id: str = "unknown",
    batch_bucket: str = "",
) -> Dict[str, Any]:
    """
    Process a batch of place_ids using MerchantHarmonizerV3.

    Args:
        place_ids: List of place_ids to process
        dynamo_client: DynamoDB client
        places_client: Optional Google Places client
        dry_run: If True, only report what would be updated

    Returns:
        Dictionary with processing results
    """
    from receipt_agent.tools.harmonizer_v3 import MerchantHarmonizerV3

    # Create harmonizer
    harmonizer = MerchantHarmonizerV3(
        dynamo_client=dynamo_client,
        places_client=places_client,
    )

    # Load all receipts (this groups them by place_id)
    total_receipts = harmonizer.load_all_receipts()
    logger.info(f"Loaded {total_receipts} receipts from DynamoDB")

    # Filter to only process the place_ids in this batch
    # The harmonizer has already loaded all receipts, so we need to filter
    # the groups to only those in our batch
    groups_to_process = []
    for place_id in place_ids:
        if place_id in harmonizer._place_id_groups:
            group = harmonizer._place_id_groups[place_id]
            # Only process inconsistent groups (skip already consistent ones)
            if not group.is_consistent:
                groups_to_process.append(group)
            else:
                logger.info(
                    f"Skipping {place_id}: already consistent ({len(group.receipts)} receipts)"
                )

    if not groups_to_process:
        logger.info(
            f"No inconsistent groups found for {len(place_ids)} place_ids"
        )
        return {
            "status": "skipped",
            "place_ids": place_ids,
            "groups_processed": 0,
            "total_receipts": 0,
            "receipts_updated": 0,
            "receipts_failed": 0,
            "results_path": None,
            "message": "All groups already consistent",
        }

    logger.info(
        f"Processing {len(groups_to_process)} inconsistent groups "
        f"from {len(place_ids)} place_ids"
    )

    # Run harmonization for the filtered groups
    # We'll need to manually process each group since harmonize_all() processes all groups
    # Let's use the agent directly for each group
    from receipt_agent.graph.harmonizer_workflow import (
        create_harmonizer_graph,
        run_harmonizer_agent,
    )

    # Get ChromaDB bucket name for lazy loading (only if sub-agent is needed)
    chromadb_bucket = os.environ.get("CHROMADB_BUCKET", "")

    # Initialize agent graph (ChromaDB will be lazy-loaded if find_correct_metadata is called)
    agent_graph, agent_state_holder = create_harmonizer_graph(
        dynamo_client=dynamo_client,
        places_api=places_client,
        settings=None,
        chromadb_bucket=chromadb_bucket,  # Pass bucket name for lazy loading
    )

    results = []
    receipts_updated = 0
    total_receipts_in_batch = 0

    for group in groups_to_process:
        total_receipts_in_batch += len(group.receipts)

        # Convert receipts to dict format for agent
        receipts_data = [
            {
                "image_id": r.image_id,
                "receipt_id": r.receipt_id,
                "merchant_name": r.merchant_name,
                "address": r.address,
                "phone": r.phone,
            }
            for r in group.receipts
        ]

        logger.info(
            f"Processing place_id {group.place_id} with {len(group.receipts)} receipts"
        )

        try:
            agent_result = await run_harmonizer_agent(
                graph=agent_graph,
                state_holder=agent_state_holder,
                place_id=group.place_id,
                receipts=receipts_data,
                places_api=places_client,
            )

            receipts_needing_update = agent_result.get(
                "receipts_needing_update", 0
            )
            receipts_updated += receipts_needing_update

            # Store full agent result (including updates) for later use
            results.append(
                {
                    "place_id": group.place_id,
                    "receipts_processed": len(group.receipts),
                    "receipts_needing_update": receipts_needing_update,
                    "confidence": agent_result.get("confidence", 0),
                    "canonical_merchant_name": agent_result.get(
                        "canonical_merchant_name"
                    ),
                    "canonical_address": agent_result.get("canonical_address"),
                    "canonical_phone": agent_result.get("canonical_phone"),
                    "updates": agent_result.get(
                        "updates", []
                    ),  # Store updates for applying
                    "error": agent_result.get("error"),
                    "_agent_result": agent_result,  # Store full result for reference
                }
            )

        except Exception as e:
            logger.error(f"Error processing place_id {group.place_id}: {e}")
            results.append(
                {
                    "place_id": group.place_id,
                    "receipts_processed": len(group.receipts),
                    "receipts_needing_update": 0,
                    "error": str(e),
                }
            )

    # Apply fixes if not dry run
    updates_applied = 0
    updates_failed = 0
    if not dry_run:
        logger.info(f"Applying fixes for {receipts_updated} receipts...")

        # Apply updates directly from agent results
        for result in results:
            if result.get("error") or not result.get(
                "receipts_needing_update"
            ):
                continue

            canonical_merchant_name = result.get("canonical_merchant_name")
            canonical_address = result.get("canonical_address")
            canonical_phone = result.get("canonical_phone")
            updates = result.get("updates", [])

            if not canonical_merchant_name:
                continue

            # Apply updates from agent result (preferred) or reconstruct from canonical values
            if updates:
                # Use updates from agent (more precise - only updates receipts that need it)
                for update in updates:
                    image_id = update.get("image_id")
                    receipt_id = update.get("receipt_id")

                    try:
                        metadata = dynamo_client.get_receipt_metadata(
                            image_id, receipt_id
                        )

                        if not metadata:
                            logger.warning(
                                f"Metadata not found for {image_id}#{receipt_id}"
                            )
                            updates_failed += 1
                            continue

                        # Update fields based on canonical values
                        updated_fields = []
                        if (
                            canonical_merchant_name
                            and metadata.merchant_name
                            != canonical_merchant_name
                        ):
                            metadata.merchant_name = canonical_merchant_name
                            updated_fields.append("merchant_name")

                        if (
                            canonical_address
                            and metadata.address != canonical_address
                        ):
                            metadata.address = canonical_address
                            updated_fields.append("address")

                        if (
                            canonical_phone
                            and metadata.phone_number != canonical_phone
                        ):
                            metadata.phone_number = canonical_phone
                            updated_fields.append("phone_number")

                        if updated_fields:
                            dynamo_client.update_receipt_metadata(metadata)
                            updates_applied += 1
                            logger.debug(
                                f"Updated {image_id[:8]}...#{receipt_id}: "
                                f"{', '.join(updated_fields)}"
                            )

                    except Exception as e:
                        logger.error(
                            f"Failed to update {image_id}#{receipt_id}: {e}"
                        )
                        updates_failed += 1
            else:
                # Fallback: apply to all receipts in the group
                # Find the group from groups_to_process (we already have it)
                place_id = result.get("place_id")
                group = None
                for g in groups_to_process:
                    if g.place_id == place_id:
                        group = g
                        break

                if not group:
                    logger.warning(f"Group not found for place_id {place_id}")
                    continue

                for receipt in group.receipts:
                    try:
                        metadata = dynamo_client.get_receipt_metadata(
                            receipt.image_id, receipt.receipt_id
                        )

                        if not metadata:
                            logger.warning(
                                f"Metadata not found for {receipt.image_id}#{receipt.receipt_id}"
                            )
                            updates_failed += 1
                            continue

                        # Update fields if they differ
                        updated_fields = []
                        if (
                            canonical_merchant_name
                            and metadata.merchant_name
                            != canonical_merchant_name
                        ):
                            metadata.merchant_name = canonical_merchant_name
                            updated_fields.append("merchant_name")

                        if (
                            canonical_address
                            and metadata.address != canonical_address
                        ):
                            metadata.address = canonical_address
                            updated_fields.append("address")

                        if (
                            canonical_phone
                            and metadata.phone_number != canonical_phone
                        ):
                            metadata.phone_number = canonical_phone
                            updated_fields.append("phone_number")

                        if updated_fields:
                            dynamo_client.update_receipt_metadata(metadata)
                            updates_applied += 1
                            logger.debug(
                                f"Updated {receipt.image_id[:8]}...#{receipt.receipt_id}: "
                                f"{', '.join(updated_fields)}"
                            )

                    except Exception as e:
                        logger.error(
                            f"Failed to update {receipt.image_id}#{receipt.receipt_id}: {e}"
                        )
                        updates_failed += 1

        logger.info(
            f"Applied {updates_applied} updates, {updates_failed} failed"
        )
    else:
        logger.info(f"[DRY RUN] Would apply {receipts_updated} updates")

    # Remove internal _agent_result from results before returning
    cleaned_results = []
    for r in results:
        cleaned_r = {k: v for k, v in r.items() if k != "_agent_result"}
        cleaned_results.append(cleaned_r)

    # Save results to S3 to avoid Step Functions payload size limits
    results_path = None
    if batch_bucket and execution_id:
        try:
            # Create results key: results/{execution_id}/batch-{place_ids_hash}.json
            # Use first and last place_id for readability
            place_ids_str = (
                f"{place_ids[0][:8]}...{place_ids[-1][:8]}"
                if len(place_ids) > 1
                else place_ids[0][:16]
            )
            safe_name = place_ids_str.replace("/", "-").replace(" ", "-")
            results_key = f"results/{execution_id}/batch-{safe_name}.json"

            results_data = {
                "execution_id": execution_id,
                "place_ids": place_ids,
                "groups_processed": len(groups_to_process),
                "total_receipts": total_receipts_in_batch,
                "receipts_updated": (
                    receipts_updated if dry_run else updates_applied
                ),
                "receipts_failed": updates_failed if not dry_run else 0,
                "dry_run": dry_run,
                "results": cleaned_results,
            }

            s3.put_object(
                Bucket=batch_bucket,
                Key=results_key,
                Body=json.dumps(results_data, indent=2, default=str).encode(
                    "utf-8"
                ),
                ContentType="application/json",
            )

            results_path = f"s3://{batch_bucket}/{results_key}"
            logger.info(f"Results uploaded to {results_path}")
        except Exception as e:
            logger.warning(f"Failed to upload results to S3: {e}")

    return {
        "status": "success",
        "place_ids": place_ids,
        "groups_processed": len(groups_to_process),
        "total_receipts": total_receipts_in_batch,
        "receipts_updated": receipts_updated if dry_run else updates_applied,
        "receipts_failed": updates_failed if not dry_run else 0,
        "results_path": results_path,  # S3 path to full results
        "dry_run": dry_run,
        # Include summary only (not full results) to avoid payload size limits
        "results_summary": {
            "total_groups": len(cleaned_results),
            "groups_with_updates": sum(
                1
                for r in cleaned_results
                if r.get("receipts_needing_update", 0) > 0
            ),
        },
    }


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    Lambda handler for harmonizing metadata for a batch of place_ids.

    Input:
    {
        "place_ids": ["place_id_1", "place_id_2", ...],
        "dry_run": true,
        "execution_id": "abc123"
    }

    Output:
    {
        "status": "success",
        "place_ids": [...],
        "groups_processed": 5,
        "receipts_updated": 12,
        "results": [...]
    }
    """
    start_time = time.time()
    execution_id = event.get("execution_id", "unknown")
    place_ids = event.get("place_ids", [])
    dry_run = event.get("dry_run", True)
    batch_bucket = event.get("batch_bucket") or os.environ.get(
        "BATCH_BUCKET", ""
    )

    logger.info(
        f"Processing batch of {len(place_ids)} place_ids "
        f"(execution_id={execution_id}, dry_run={dry_run})"
    )

    if not place_ids:
        logger.warning("No place_ids provided")
        return {
            "status": "skipped",
            "place_ids": [],
            "groups_processed": 0,
            "receipts_updated": 0,
            "message": "No place_ids provided",
        }

    # Create clients
    table_name = os.environ.get(
        "RECEIPT_AGENT_DYNAMO_TABLE_NAME"
    ) or os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        raise ValueError(
            "DYNAMODB_TABLE_NAME or RECEIPT_AGENT_DYNAMO_TABLE_NAME not set"
        )

    from receipt_dynamo import DynamoClient

    dynamo_client = DynamoClient(table_name)

    # Optional Google Places client
    places_client = None
    google_places_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if google_places_key:
        try:
            from receipt_places import PlacesClient

            places_client = PlacesClient(api_key=google_places_key)
            logger.info("Google Places API client initialized")
        except ImportError:
            logger.warning(
                "receipt_places not available, skipping Google Places validation"
            )
    else:
        logger.info(
            "GOOGLE_PLACES_API_KEY not set, skipping Google Places validation"
        )

    # Process batch
    try:
        result = asyncio.run(
            process_place_id_batch(
                place_ids=place_ids,
                dynamo_client=dynamo_client,
                places_client=places_client,
                dry_run=dry_run,
                execution_id=execution_id,
                batch_bucket=batch_bucket,
            )
        )

        processing_time = time.time() - start_time

        # Emit metrics
        emf_metrics.log_metrics(
            metrics={
                "PlaceIdsProcessed": len(place_ids),
                "GroupsProcessed": result.get("groups_processed", 0),
                "ReceiptsUpdated": result.get("receipts_updated", 0),
                "ProcessingTimeSeconds": processing_time,
            },
            dimensions={"Status": result.get("status", "unknown")},
            properties={
                "execution_id": execution_id,
                "dry_run": dry_run,
            },
        )

        # Flush LangSmith traces before returning
        flush_langsmith_traces()

        logger.info(
            f"Batch complete: {result.get('groups_processed', 0)} groups, "
            f"{result.get('receipts_updated', 0)} receipts updated "
            f"({processing_time:.2f}s)"
        )

        return result

    except Exception as e:
        logger.error(f"Error processing batch: {e}", exc_info=True)
        flush_langsmith_traces()
        raise

