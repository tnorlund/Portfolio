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
import re
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

    Note: Multipart upload errors ("multipart: NextPart: EOF") are non-fatal
    and can occur when:
    - Traces are very large (long-running Lambdas with many agent interactions)
    - Lambda shutdown interrupts the multipart upload
    - Network issues during upload

    These errors are logged at DEBUG level since they don't affect Lambda execution.
    """
    if HAS_LANGSMITH and get_langsmith_client:
        try:
            client = get_langsmith_client()
            client.flush()
            logger.info("LangSmith traces flushed successfully")
        except Exception as e:
            error_str = str(e)
            # Suppress multipart EOF errors - these are non-fatal and common
            # for long-running Lambdas with large traces. The error occurs when
            # Lambda shutdown interrupts the multipart upload, but this doesn't
            # affect the Lambda's execution or results.
            if "multipart" in error_str.lower() and (
                "eof" in error_str.lower() or "nextpart" in error_str.lower()
            ):
                logger.debug(
                    "LangSmith multipart upload error (non-fatal): %s. "
                    "Large traces may not upload completely before Lambda shutdown. "
                    "This is expected for long-running executions and does not affect results.",
                    error_str[:200],  # Truncate long error messages
                )
            else:
                logger.warning(
                    "Failed to flush LangSmith traces: %s", error_str
                )


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
    max_receipts_per_batch: int = 20,
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
    from receipt_agent.agents.harmonizer.tools import MerchantHarmonizerV3

    # Create harmonizer
    harmonizer = MerchantHarmonizerV3(
        dynamo_client=dynamo_client,
        places_client=places_client,
    )

    def sanitize_canonical_address(
        address: Optional[str], previous_value: Optional[str] = None
    ) -> Optional[str]:
        """
        Ensure canonical_address is a clean postal address (no reasoning/commentary).

        Returns a sanitized address or previous_value (to avoid overwriting with bad
        data). If nothing can be salvaged, returns None.
        """
        if not address:
            return previous_value

        cleaned = address.strip().strip('"')
        lower = cleaned.lower()

        # Reject addresses that clearly contain reasoning/commentary
        bad_markers = [
            "?",
            "actually need",
            "lind0",  # observed typo injected with reasoning
        ]
        if any(marker in lower for marker in bad_markers):
            return previous_value

        # Fix malformed ZIPs like 913001 → 91301 (truncate extra trailing digits)
        # Keep formatting (spaces/commas) intact; only trim a trailing 6+ digit run
        # Pattern: find 5 digits followed by 1-2 extra digits at the end (before non-digits)
        cleaned = re.sub(r"(\d{5})(\d{1,2})(?=\D*$)", r"\1", cleaned)

        return cleaned

    # Extract base place_ids (handle sub-batches like "place_id:sub_batch_idx")
    base_place_ids = set()
    for place_id_input in place_ids:
        if ":" in place_id_input:
            parts = place_id_input.split(":", 1)
            if len(parts) == 2:
                base_place_ids.add(parts[0])
            else:
                base_place_ids.add(place_id_input)
        else:
            base_place_ids.add(place_id_input)

    # Load only receipts for the place_ids in this batch (much more efficient)
    total_receipts = harmonizer.load_receipts_for_place_ids(
        list(base_place_ids)
    )
    logger.info(
        f"Loaded {total_receipts} receipts from DynamoDB for {len(base_place_ids)} place_id(s)"
    )

    # Filter to only process the place_ids in this batch
    # The harmonizer has already loaded all receipts, so we need to filter
    # the groups to only those in our batch. We no longer skip "consistent"
    # groups because the definition has changed to include receipt text checks.
    # Handle sub-batches for large groups (format: "place_id:sub_batch_idx")
    groups_to_process = []
    for place_id_input in place_ids:
        # Check if this is a sub-batch (format: "place_id:sub_batch_idx")
        if ":" in place_id_input:
            # Parse sub-batch: "place_id:sub_batch_idx"
            parts = place_id_input.split(":", 1)
            if len(parts) == 2:
                base_place_id = parts[0]
                try:
                    sub_batch_idx = int(parts[1])

                    # Get the full group for this place_id
                    if base_place_id in harmonizer._place_id_groups:
                        full_group = harmonizer._place_id_groups[base_place_id]
                        all_receipts = full_group.receipts

                        # Sort receipts deterministically by (image_id, receipt_id)
                        # This ensures sub-batches are consistent across Lambda invocations
                        # even if DynamoDB scan order varies. The same receipts will always
                        # be assigned to the same sub-batch index.
                        #
                        # IMPORTANT: This sorting must match the order used when calculating
                        # sub-batch counts in list_place_ids. Both use (image_id, receipt_id)
                        # as the sort key to ensure consistency.
                        sorted_receipts = sorted(
                            all_receipts,
                            key=lambda r: (r.image_id, r.receipt_id),
                        )

                        # Split receipts into sub-batches using deterministic order
                        # Sub-batch 0: receipts 0-19, Sub-batch 1: receipts 20-39, etc.
                        start_idx = sub_batch_idx * max_receipts_per_batch
                        end_idx = start_idx + max_receipts_per_batch
                        sub_batch_receipts = sorted_receipts[start_idx:end_idx]

                        # Validate sub-batch bounds
                        if start_idx >= len(sorted_receipts):
                            logger.warning(
                                f"Sub-batch {sub_batch_idx} start_idx ({start_idx}) >= "
                                f"total receipts ({len(sorted_receipts)}) for {base_place_id}. "
                                f"This may indicate receipt count changed between batching and processing."
                            )
                            continue

                        if not sub_batch_receipts:
                            logger.warning(
                                f"Sub-batch {sub_batch_idx} for {base_place_id} is empty "
                                f"(start_idx={start_idx}, total_receipts={len(sorted_receipts)})"
                            )
                            continue

                        # Log which receipts are in this sub-batch for debugging
                        receipt_ids = [
                            f"{r.image_id[:8]}...#{r.receipt_id}"
                            for r in sub_batch_receipts[:5]
                        ]
                        logger.debug(
                            f"Sub-batch {sub_batch_idx} receipts (first 5): {receipt_ids}"
                        )

                        # Create a temporary group with only the sub-batch receipts
                        from receipt_agent.agents.harmonizer.tools import (
                            PlaceIdGroup,
                        )

                        sub_group = PlaceIdGroup(
                            place_id=base_place_id,
                            receipts=sub_batch_receipts,
                        )
                        groups_to_process.append(sub_group)

                        logger.info(
                            f"Processing sub-batch {sub_batch_idx} of {base_place_id}: "
                            f"{len(sub_batch_receipts)}/{len(all_receipts)} receipts "
                            f"(indices {start_idx}-{min(end_idx, len(all_receipts))-1})"
                        )
                    else:
                        logger.warning(
                            f"Base place_id {base_place_id} not found for sub-batch {sub_batch_idx}"
                        )
                except ValueError:
                    logger.warning(
                        f"Invalid sub-batch format: {place_id_input} (expected 'place_id:idx')"
                    )
                    # Fall through to regular processing
                    if place_id_input in harmonizer._place_id_groups:
                        group = harmonizer._place_id_groups[place_id_input]
                        groups_to_process.append(group)
            else:
                # Invalid format, try as regular place_id
                if place_id_input in harmonizer._place_id_groups:
                    group = harmonizer._place_id_groups[place_id_input]
                    groups_to_process.append(group)
        else:
            # Regular place_id (not a sub-batch)
            if place_id_input in harmonizer._place_id_groups:
                group = harmonizer._place_id_groups[place_id_input]
                groups_to_process.append(group)

    if not groups_to_process:
        logger.info(
            f"No groups found to process for {len(place_ids)} place_ids"
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
    from receipt_agent.agents.harmonizer import (
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

    # API usage metrics (matching pattern from label-validation-agent)
    llm_calls_total = 0
    llm_calls_successful = 0
    llm_calls_failed = 0
    server_errors = 0  # 5xx errors
    retry_attempts = 0
    timeout_errors = 0
    rate_limit_errors = 0
    circuit_breaker_triggers = 0

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
            # Estimate LLM calls: 1 base call + tools used (rough estimate)
            # The agent may make multiple calls, but we'll track at the group level
            estimated_llm_calls = 1  # Base agent call
            llm_calls_total += estimated_llm_calls

            agent_result = await run_harmonizer_agent(
                graph=agent_graph,
                state_holder=agent_state_holder,
                place_id=group.place_id,
                receipts=receipts_data,
                places_api=places_client,
            )

            # Track successful call
            llm_calls_successful += estimated_llm_calls

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
            error_str = str(e)
            logger.error(f"Error processing place_id {group.place_id}: {e}")

            # Track failed LLM call
            llm_calls_total += 1
            llm_calls_failed += 1

            # Track error types (matching pattern from label-validation-agent)
            is_rate_limit = (
                "429" in error_str
                or "rate limit" in error_str.lower()
                or "rate_limit" in error_str.lower()
                or "too many concurrent requests" in error_str.lower()
                or "too many requests" in error_str.lower()
                or "OllamaRateLimitError" in error_str
            )

            if is_rate_limit:
                rate_limit_errors += 1

            # Track server errors (5xx)
            if any(code in error_str for code in ["500", "502", "503", "504"]):
                server_errors += 1

            # Track timeout errors
            if (
                "timeout" in error_str.lower()
                or "timed out" in error_str.lower()
            ):
                timeout_errors += 1

            results.append(
                {
                    "place_id": group.place_id,
                    "receipts_processed": len(group.receipts),
                    "receipts_needing_update": 0,
                    "error": error_str,
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
            canonical_address = sanitize_canonical_address(
                result.get("canonical_address")
            )
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

                        # TODO: DEPRECATION - Update base fields instead of canonical fields
                        # See docs/architecture/CANONICAL_FIELDS_DEPRECATION.md
                        # Currently updating canonical fields for backward compatibility,
                        # but the goal is to remove canonical fields entirely.
                        # The harmonizer determines canonical values for the place_id group
                        # These should be written to base fields (merchant_name, address, phone_number)
                        # instead of canonical fields.
                        updated_fields = []
                        if (
                            canonical_merchant_name
                            and metadata.canonical_merchant_name
                            != canonical_merchant_name
                        ):
                            metadata.canonical_merchant_name = (
                                canonical_merchant_name
                            )
                            updated_fields.append("canonical_merchant_name")

                        sanitized_address = sanitize_canonical_address(
                            canonical_address, metadata.canonical_address
                        )
                        if (
                            sanitized_address
                            and metadata.canonical_address != sanitized_address
                        ):
                            metadata.canonical_address = sanitized_address
                            updated_fields.append("canonical_address")

                        if (
                            canonical_phone
                            and metadata.canonical_phone_number
                            != canonical_phone
                        ):
                            metadata.canonical_phone_number = canonical_phone
                            updated_fields.append("canonical_phone_number")

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

                        sanitized_address = sanitize_canonical_address(
                            canonical_address, metadata.address
                        )
                        if (
                            sanitized_address
                            and metadata.address != sanitized_address
                        ):
                            metadata.address = sanitized_address
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
        # API usage metrics (matching label-validation-agent)
        "api_metrics": {
            "llm_calls_total": llm_calls_total,
            "llm_calls_successful": llm_calls_successful,
            "llm_calls_failed": llm_calls_failed,
            "rate_limit_errors": rate_limit_errors,
            "server_errors": server_errors,
            "retry_attempts": retry_attempts,  # Step Function handles retries
            "circuit_breaker_triggers": circuit_breaker_triggers,
            "timeout_errors": timeout_errors,
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

    # Allow Step Function input to override the LangSmith project at runtime
    langchain_project = event.get("langchain_project")
    if langchain_project:
        os.environ["LANGCHAIN_PROJECT"] = langchain_project
        logger.info("LANGCHAIN_PROJECT overridden to %s", langchain_project)

    place_ids = event.get("place_ids", [])
    dry_run = event.get("dry_run", True)
    batch_bucket = event.get("batch_bucket") or os.environ.get(
        "BATCH_BUCKET", ""
    )
    max_receipts_per_batch = event.get("max_receipts_per_batch", 20)

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
                max_receipts_per_batch=max_receipts_per_batch,
            )
        )

        processing_time = time.time() - start_time

        # Extract API metrics from result
        api_metrics = result.get("api_metrics", {})

        # Emit metrics (matching pattern from label-validation-agent)
        emf_metrics.log_metrics(
            metrics={
                "PlaceIdsProcessed": len(place_ids),
                "GroupsProcessed": result.get("groups_processed", 0),
                "ReceiptsProcessed": result.get("total_receipts", 0),
                "ReceiptsUpdated": result.get("receipts_updated", 0),
                "ReceiptsFailed": result.get("receipts_failed", 0),
                "ProcessingTimeSeconds": round(processing_time, 2),
                "BatchSucceeded": 1,
                "BatchFailed": 0,
                # API usage metrics (matching label-validation-agent)
                "LLMCallsTotal": api_metrics.get("llm_calls_total", 0),
                "LLMCallsSuccessful": api_metrics.get(
                    "llm_calls_successful", 0
                ),
                "LLMCallsFailed": api_metrics.get("llm_calls_failed", 0),
                "RateLimitErrors": api_metrics.get("rate_limit_errors", 0),
                "ServerErrors": api_metrics.get("server_errors", 0),
                "RetryAttempts": api_metrics.get("retry_attempts", 0),
                "CircuitBreakerTriggers": api_metrics.get(
                    "circuit_breaker_triggers", 0
                ),
                "TimeoutErrors": api_metrics.get("timeout_errors", 0),
            },
            dimensions={"Status": result.get("status", "unknown")},
            properties={
                "execution_id": execution_id,
                "dry_run": dry_run,
            },
            units={
                "ProcessingTimeSeconds": "Seconds",
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
        processing_time = time.time() - start_time
        logger.error(f"Error processing batch: {e}", exc_info=True)

        # Emit failure metrics (matching pattern from label-validation-agent)
        error_str = str(e)
        emf_metrics.log_metrics(
            metrics={
                "PlaceIdsProcessed": len(place_ids),
                "GroupsProcessed": 0,
                "ReceiptsProcessed": 0,
                "ReceiptsUpdated": 0,
                "ReceiptsFailed": 0,
                "ProcessingTimeSeconds": round(processing_time, 2),
                "BatchSucceeded": 0,
                "BatchFailed": 1,
                # API usage metrics (default to 0 on error)
                "LLMCallsTotal": 0,
                "LLMCallsSuccessful": 0,
                "LLMCallsFailed": 0,
                "RateLimitErrors": 0,
                "ServerErrors": 0,
                "RetryAttempts": 0,
                "CircuitBreakerTriggers": 0,
                "TimeoutErrors": 0,
            },
            dimensions={"Status": "error"},
            properties={
                "execution_id": execution_id,
                "dry_run": dry_run,
                "error": error_str,
            },
            units={
                "ProcessingTimeSeconds": "Seconds",
            },
        )

        flush_langsmith_traces()
        raise
