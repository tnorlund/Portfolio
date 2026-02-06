"""Unified receipt evaluator with concurrent LLM calls.

This handler consolidates all receipt evaluation steps into a single Lambda:
- Step 0: Fetch receipt data from DynamoDB and write to S3 (for EMR job)
- Phase 1 (concurrent): Currency, Metadata, and Geometric evaluations
- Phase 2 (sequential): Financial validation (needs corrected labels)
- Phase 3 (conditional): LLM review of flagged issues

Uses asyncio.gather() for true concurrent LLM calls, eliminating Step Function
orchestration overhead and reducing cold starts from 5 lambdas to 1.

This handler replaces the previous two-step flow:
- LoadReceiptData (fetch from DynamoDB, write to S3)
- UnifiedReceiptEvaluator (read from S3, evaluate)

Now combined into a single Lambda that:
1. Fetches directly from DynamoDB
2. Writes to S3 for the EMR job (data/{execution_id}/*.json)
3. Runs all evaluations
4. Writes results to S3 (unified/{execution_id}/*.json)
"""

# pylint: disable=import-outside-toplevel,wrong-import-position
# Lambda handlers delay imports until runtime for cold start optimization

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any

import boto3

# Import tracing utilities - works in both container and local environments
try:
    # Container environment: tracing.py is in same directory
    from tracing import (
        TRACING_VERSION,
        TraceContext,
        child_trace,
        create_historical_span,
        create_receipt_trace,
        end_child_trace,
        end_receipt_trace,
        flush_langsmith_traces,
        start_child_trace,
    )

    from utils.s3_helpers import (
        download_chromadb_snapshot,
        get_merchant_hash,
        load_json_from_s3,
        upload_json_to_s3,
    )

    _tracing_import_source = "container"
except ImportError:
    # Local/development environment: use path relative to this file
    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "lambdas",
            "utils",
        ),
    )
    from tracing import (
        TRACING_VERSION,
        TraceContext,
        child_trace,
        create_historical_span,
        create_receipt_trace,
        end_child_trace,
        end_receipt_trace,
        flush_langsmith_traces,
        start_child_trace,
    )

    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "lambdas",
        ),
    )
    from utils.s3_helpers import (
        download_chromadb_snapshot,
        get_merchant_hash,
        load_json_from_s3,
        upload_json_to_s3,
    )

    _tracing_import_source = "local"


logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Suppress noisy HTTP logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

s3 = boto3.client("s3")


async def unified_receipt_evaluator(
    event: dict[str, Any], _context: Any
) -> dict[str, Any]:
    """
    Unified receipt evaluation with concurrent LLM calls.

    Input (new format - direct receipt info):
    {
        "receipt": {"image_id": "img1", "receipt_id": 1, "merchant_name": "Wild Fork"},
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "execution_arn": "arn:aws:states:...",
        "langchain_project": "label-eval-{timestamp}"
    }

    Input (legacy format - S3 key):
    {
        "data_s3_key": "data/{exec}/{image_id}_{receipt_id}.json",
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "merchant_name": "Wild Fork",
        "receipt_trace_id": "...",
        "execution_arn": "arn:aws:states:...",
        "langchain_project": "label-eval-{timestamp}"
    }

    Output:
    {
        "status": "completed",
        "image_id": "img1",
        "receipt_id": 1,
        "issues_found": 3,
        "currency_words_evaluated": 12,
        "metadata_words_evaluated": 15,
        "financial_values_evaluated": 8,
        "decisions": {...}
    }
    """
    logger.info(
        "[UnifiedReceiptEvaluator] Tracing module loaded: version=%s, source=%s",
        TRACING_VERSION,
        _tracing_import_source,
    )

    # Allow runtime override of LangSmith project via Step Function input
    langchain_project = event.get("langchain_project")
    if langchain_project:
        os.environ["LANGCHAIN_PROJECT"] = langchain_project
        logger.info("LangSmith project set to: %s", langchain_project)

    execution_id = event.get("execution_id", "unknown")
    execution_arn = event.get("execution_arn", f"local:{execution_id}")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")

    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    # Determine input mode: new format (receipt object) or legacy format (data_s3_key)
    receipt_obj = event.get("receipt")
    data_s3_key = event.get("data_s3_key")
    use_direct_fetch = receipt_obj is not None

    if use_direct_fetch:
        # New format: fetch from DynamoDB directly
        image_id = receipt_obj.get("image_id")
        receipt_id = receipt_obj.get("receipt_id")
        merchant_name = receipt_obj.get("merchant_name", "unknown")
        receipt_trace_id = (
            ""  # Not passed in new format, trace ID generated internally
        )
        if not image_id or receipt_id is None:
            raise ValueError(
                "receipt.image_id and receipt.receipt_id are required"
            )
    elif data_s3_key:
        # Legacy format: will load from S3
        merchant_name = event.get("merchant_name", "unknown")
        receipt_trace_id = event.get("receipt_trace_id", "")
        image_id = None
        receipt_id = None
    else:
        raise ValueError(
            "Either 'receipt' object or 'data_s3_key' is required"
        )

    start_time = time.time()

    # Initialize for error handling (image_id/receipt_id already set above for direct fetch)
    result = None
    receipt_trace = None  # Will hold the ReceiptTraceInfo

    try:
        # Import utilities
        from utils.serialization import (
            deserialize_label,
            deserialize_patterns,
            deserialize_place,
            deserialize_word,
            serialize_label,
            serialize_place,
            serialize_word,
        )

        # Initialize dynamo_table early for use across all phases
        dynamo_table = os.environ.get("DYNAMODB_TABLE_NAME") or os.environ.get(
            "RECEIPT_AGENT_DYNAMO_TABLE_NAME"
        )

        # 1. Load receipt data - either from DynamoDB (new) or S3 (legacy)
        receipt_info = None
        if use_direct_fetch:
            # NEW: Fetch directly from DynamoDB
            logger.info(
                "Fetching receipt data from DynamoDB for %s#%s",
                image_id,
                receipt_id,
            )

            from receipt_dynamo import DynamoClient

            if not dynamo_table:
                raise ValueError(
                    "DYNAMODB_TABLE_NAME environment variable not set"
                )

            dynamo = DynamoClient(table_name=dynamo_table)

            # Fetch receipt data (place, words, labels, receipt metadata)
            try:
                place = dynamo.get_receipt_place(image_id, receipt_id)
            except Exception:
                logger.warning(
                    "No ReceiptPlace found for %s#%s", image_id, receipt_id
                )
                place = None

            try:
                words = dynamo.list_receipt_words_from_receipt(
                    image_id, receipt_id
                )
            except Exception:
                logger.warning(
                    "Failed to fetch words for %s#%s", image_id, receipt_id
                )
                words = []

            try:
                labels, _ = dynamo.list_receipt_word_labels_for_receipt(
                    image_id, receipt_id
                )
            except Exception:
                logger.warning(
                    "Failed to fetch labels for %s#%s", image_id, receipt_id
                )
                labels = []

            try:
                receipt_info = dynamo.get_receipt(image_id, receipt_id)
            except Exception:
                logger.warning(
                    "Failed to fetch receipt metadata for %s#%s",
                    image_id,
                    receipt_id,
                )
                receipt_info = None

            logger.info(
                "Fetched %s words, %s labels for %s#%s",
                len(words),
                len(labels),
                image_id,
                receipt_id,
            )

            # Write receipt data to S3 for EMR job (same format as old LoadReceiptData)
            data_s3_key = f"data/{execution_id}/{image_id}_{receipt_id}.json"
            receipt_data_for_s3 = {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant_name": merchant_name,
                "place": serialize_place(place) if place else None,
                "words": [serialize_word(w) for w in words],
                "labels": [serialize_label(label) for label in labels],
            }

            try:
                s3.put_object(
                    Bucket=batch_bucket,
                    Key=data_s3_key,
                    Body=json.dumps(receipt_data_for_s3).encode("utf-8"),
                    ContentType="application/json",
                )
                logger.info(
                    "Saved receipt data to s3://%s/%s (for EMR job)",
                    batch_bucket,
                    data_s3_key,
                )
            except Exception:
                logger.exception(
                    "Failed to upload data to s3://%s/%s",
                    batch_bucket,
                    data_s3_key,
                )
                raise

        else:
            # LEGACY: Load from S3 (for backwards compatibility)
            logger.info(
                "Loading receipt data from s3://%s/%s",
                batch_bucket,
                data_s3_key,
            )
            target_data = load_json_from_s3(
                s3, batch_bucket, data_s3_key, logger=logger
            )

            if target_data is None:
                raise ValueError(f"Receipt data not found at {data_s3_key}")

            image_id = target_data.get("image_id")
            receipt_id = target_data.get("receipt_id")

            if not image_id or receipt_id is None:
                raise ValueError(
                    "image_id and receipt_id are required in data"
                )

            # Deserialize entities
            words = [deserialize_word(w) for w in target_data.get("words", [])]
            labels = [
                deserialize_label(label_data)
                for label_data in target_data.get("labels", [])
            ]
            place = deserialize_place(target_data.get("place"))

            logger.info(
                "Loaded %s words, %s labels for %s#%s",
                len(words),
                len(labels),
                image_id,
                receipt_id,
            )

            # Attempt to fetch receipt metadata for CDN keys (if DynamoDB is available)
            receipt_info = None
            if dynamo_table:
                try:
                    from receipt_dynamo import DynamoClient

                    dynamo = DynamoClient(table_name=dynamo_table)
                    receipt_info = dynamo.get_receipt(image_id, receipt_id)
                except Exception:
                    logger.warning(
                        "Failed to fetch receipt metadata for %s#%s",
                        image_id,
                        receipt_id,
                    )

        # Write receipt lookup record (JSONL-style) for EMR viz-cache
        # Generate CDN keys - use DynamoDB values if available, otherwise construct from pattern
        receipt_key = f"{image_id}_RECEIPT_{receipt_id:05d}"
        if receipt_info is not None and receipt_info.cdn_s3_key:
            # Use CDN keys from DynamoDB
            cdn_s3_key = receipt_info.cdn_s3_key
            cdn_webp_s3_key = receipt_info.cdn_webp_s3_key
            cdn_avif_s3_key = receipt_info.cdn_avif_s3_key
            cdn_medium_s3_key = receipt_info.cdn_medium_s3_key
            cdn_medium_webp_s3_key = receipt_info.cdn_medium_webp_s3_key
            cdn_medium_avif_s3_key = receipt_info.cdn_medium_avif_s3_key
            width = receipt_info.width
            height = receipt_info.height
        else:
            # Generate CDN keys from predictable pattern
            cdn_s3_key = f"assets/{receipt_key}.jpg"
            cdn_webp_s3_key = f"assets/{receipt_key}.webp"
            cdn_avif_s3_key = f"assets/{receipt_key}.avif"
            cdn_medium_s3_key = f"assets/{receipt_key}_medium.jpg"
            cdn_medium_webp_s3_key = f"assets/{receipt_key}_medium.webp"
            cdn_medium_avif_s3_key = f"assets/{receipt_key}_medium.avif"
            # Default dimensions - will be overridden if receipt_info exists
            width = receipt_info.width if receipt_info else 800
            height = receipt_info.height if receipt_info else 2400
            logger.info(
                "Generated CDN keys for %s#%s (no DynamoDB cdn_s3_key)",
                image_id,
                receipt_id,
            )

        lookup_key = (
            f"receipts_lookup/{execution_id}/{image_id}_{receipt_id}.json"
        )
        lookup_payload = {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "cdn_s3_key": cdn_s3_key,
            "cdn_webp_s3_key": cdn_webp_s3_key,
            "cdn_avif_s3_key": cdn_avif_s3_key,
            "cdn_medium_s3_key": cdn_medium_s3_key,
            "cdn_medium_webp_s3_key": cdn_medium_webp_s3_key,
            "cdn_medium_avif_s3_key": cdn_medium_avif_s3_key,
            "width": width,
            "height": height,
        }
        if lookup_payload["cdn_s3_key"]:
            try:
                s3.put_object(
                    Bucket=batch_bucket,
                    Key=lookup_key,
                    Body=json.dumps(lookup_payload).encode("utf-8"),
                    ContentType="application/json",
                )
                logger.info(
                    "Saved receipt lookup to s3://%s/%s",
                    batch_bucket,
                    lookup_key,
                )
            except Exception:
                logger.exception(
                    "Failed to upload receipt lookup to s3://%s/%s",
                    batch_bucket,
                    lookup_key,
                )

        # 2. Create the ROOT receipt trace (one per receipt)
        # This is the parent trace that all child operations will link to
        # NOTE: Must use "ReceiptEvaluation" name for Spark analytics compatibility
        receipt_trace = create_receipt_trace(
            execution_arn=execution_arn,
            image_id=image_id,
            receipt_id=receipt_id,
            merchant_name=merchant_name,
            name="ReceiptEvaluation",
            inputs={
                "data_s3_key": data_s3_key,
                "merchant_name": merchant_name,
                "word_count": len(words),
                "label_count": len(labels),
            },
            metadata={
                "merchant_name": merchant_name,
                "execution_id": execution_id,
            },
            tags=["unified-evaluation", "llm", "per-receipt"],
            enable_tracing=True,
        )

        # Verify trace ID matches what FetchReceiptData generated
        if receipt_trace_id and receipt_trace_id != receipt_trace.trace_id:
            logger.warning(
                "TRACE ID MISMATCH: FetchReceiptData=%s, UnifiedEvaluator=%s",
                receipt_trace_id[:8],
                receipt_trace.trace_id[:8],
            )

        # Create TraceContext for child_trace() calls
        trace_ctx = TraceContext(
            run_tree=receipt_trace.run_tree,
            headers=(
                receipt_trace.run_tree.to_headers()
                if receipt_trace.run_tree
                else None
            ),
            trace_id=receipt_trace.trace_id,
            root_run_id=receipt_trace.root_run_id,
        )

        # 3. Setup phase: Load patterns, build visual lines, and setup LLM concurrently
        # These three operations have no dependencies on each other
        from receipt_agent.agents.label_evaluator.word_context import (
            assemble_visual_lines,
            build_word_contexts,
        )
        from receipt_agent.utils import create_production_invoker

        merchant_hash = get_merchant_hash(merchant_name)
        patterns_s3_key = event.get("patterns_s3_key") or (
            f"patterns/{execution_id}/{merchant_hash}.json"
        )
        line_item_patterns_s3_key = event.get("line_item_patterns_s3_key") or (
            f"line_item_patterns/{merchant_hash}.json"
        )

        # Create child traces for parallel operations
        load_patterns_trace_ctx = start_child_trace(
            "load_patterns",
            trace_ctx,
            metadata={"patterns_s3_key": patterns_s3_key},
        )
        build_visual_lines_trace_ctx = start_child_trace(
            "build_visual_lines",
            trace_ctx,
            metadata={"word_count": len(words), "label_count": len(labels)},
        )
        setup_llm_trace_ctx = start_child_trace(
            "setup_llm",
            trace_ctx,
            metadata={"temperature": 0.0, "timeout": 120},
        )

        # Define async functions for parallel execution
        async def load_patterns_async() -> tuple[dict | None, dict | None]:
            """Load geometric and line item patterns from S3."""
            patterns_data = await asyncio.to_thread(
                load_json_from_s3,
                s3,
                batch_bucket,
                patterns_s3_key,
                logger,
                True,  # allow_missing
            )
            line_item_data = await asyncio.to_thread(
                load_json_from_s3,
                s3,
                batch_bucket,
                line_item_patterns_s3_key,
                logger,
                True,  # allow_missing
            )
            return patterns_data, line_item_data

        async def build_visual_lines_async() -> list:
            """Build visual lines from words and labels."""
            word_contexts = await asyncio.to_thread(
                build_word_contexts, words, labels
            )
            return await asyncio.to_thread(
                assemble_visual_lines, word_contexts
            )

        async def setup_llm_async():
            """Create the production LLM invoker."""
            return await asyncio.to_thread(
                create_production_invoker,
                temperature=0.0,
                timeout=120,
                circuit_breaker_threshold=5,
                max_jitter_seconds=0.25,
            )

        # Initialize results
        patterns = None
        line_item_patterns = None
        patterns_data = None
        line_item_patterns_data = None
        visual_lines: list = []
        llm_invoker = None

        try:
            # Run all three setup operations concurrently
            (
                (patterns_data, line_item_patterns_data),
                visual_lines,
                llm_invoker,
            ) = await asyncio.gather(
                load_patterns_async(),
                build_visual_lines_async(),
                setup_llm_async(),
            )

            # Deserialize patterns after loading
            if patterns_data:
                patterns = deserialize_patterns(patterns_data)
            if line_item_patterns_data:
                if "patterns" in line_item_patterns_data:
                    line_item_patterns = line_item_patterns_data["patterns"]
                else:
                    line_item_patterns = line_item_patterns_data

            logger.info(
                "Setup complete: %d visual lines, patterns=%s, line_item_patterns=%s",
                len(visual_lines),
                patterns is not None,
                line_item_patterns is not None,
            )
        finally:
            # End child traces
            end_child_trace(
                load_patterns_trace_ctx,
                outputs={
                    "has_patterns": patterns_data is not None,
                    "has_line_item_patterns": line_item_patterns_data
                    is not None,
                },
            )
            end_child_trace(
                build_visual_lines_trace_ctx,
                outputs={
                    "visual_line_count": len(visual_lines),
                },
            )
            end_child_trace(
                setup_llm_trace_ctx,
                outputs={
                    "invoker_ready": llm_invoker is not None,
                },
            )

        # Create historical spans for pattern computation (from batch Phase 1)
        # These show up in the trace as if they happened during this receipt's evaluation
        if line_item_patterns_data:
            try:
                discovery_metadata = line_item_patterns_data.get(
                    "_trace_metadata", {}
                )
                discovery_start = discovery_metadata.get(
                    "discovery_start_time"
                )
                discovery_end = discovery_metadata.get("discovery_end_time")

                if discovery_start and discovery_end:
                    create_historical_span(
                        parent_ctx=trace_ctx,
                        name="DiscoverPatterns",
                        start_time_iso=discovery_start,
                        end_time_iso=discovery_end,
                        duration_seconds=discovery_metadata.get(
                            "discovery_duration_seconds", 0
                        ),
                        run_type="chain",
                        inputs={
                            "merchant_name": merchant_name,
                            "llm_model": discovery_metadata.get(
                                "discovery_llm_model", "unknown"
                            ),
                        },
                        outputs={
                            "line_item_labels": discovery_metadata.get(
                                "line_item_labels", []
                            ),
                        },
                        metadata={
                            "batch_computed": True,
                        },
                    )
                    logger.info(
                        "Added DiscoverPatterns historical span (%.2fs)",
                        discovery_metadata.get(
                            "discovery_duration_seconds", 0
                        ),
                    )
            except Exception as span_err:
                logger.warning(
                    "Failed to create DiscoverPatterns historical span: %s",
                    span_err,
                )

        if patterns_data:
            try:
                computation_metadata = patterns_data.get("_trace_metadata", {})
                computation_start = computation_metadata.get(
                    "computation_start_time"
                )
                computation_end = computation_metadata.get(
                    "computation_end_time"
                )

                if computation_start and computation_end:
                    # Extract pattern names for outputs
                    constellation_geometry = patterns_data.get(
                        "constellation_geometry", {}
                    )
                    label_pair_geometry = patterns_data.get(
                        "label_pair_geometry", {}
                    )
                    constellation_names = (
                        list(constellation_geometry.keys())
                        if constellation_geometry
                        else []
                    )
                    label_pair_names = (
                        list(label_pair_geometry.keys())
                        if label_pair_geometry
                        else []
                    )

                    create_historical_span(
                        parent_ctx=trace_ctx,
                        name="ComputePatterns",
                        start_time_iso=computation_start,
                        end_time_iso=computation_end,
                        duration_seconds=computation_metadata.get(
                            "computation_duration_seconds", 0
                        ),
                        run_type="chain",
                        inputs={
                            "merchant_name": merchant_name,
                            "training_receipt_count": computation_metadata.get(
                                "training_receipt_count", 0
                            ),
                        },
                        outputs={
                            "status": computation_metadata.get(
                                "computation_status", "unknown"
                            ),
                            "constellation_patterns": constellation_names,
                            "constellation_count": len(constellation_names),
                            "label_pair_patterns": label_pair_names,
                            "label_pair_count": len(label_pair_names),
                        },
                        metadata={
                            "load_data_duration_seconds": computation_metadata.get(
                                "load_data_duration_seconds", 0
                            ),
                            "compute_patterns_duration_seconds": computation_metadata.get(
                                "compute_patterns_duration_seconds", 0
                            ),
                            "batch_computed": True,
                        },
                    )
                    logger.info(
                        "Added ComputePatterns historical span "
                        "(%.2fs, %d constellations, %d label pairs)",
                        computation_metadata.get(
                            "computation_duration_seconds", 0
                        ),
                        len(constellation_names),
                        len(label_pair_names),
                    )
            except Exception as span_err:
                logger.warning(
                    "Failed to create ComputePatterns historical span: %s",
                    span_err,
                )

        # 4. Phase 1: Run currency, metadata, and geometric evaluations concurrently
        # Each evaluation gets its own child trace for visibility in LangSmith
        from receipt_agent.agents.label_evaluator import (
            create_compute_only_graph,
            run_compute_only_sync,
        )
        from receipt_agent.agents.label_evaluator.currency_subagent import (
            evaluate_currency_labels_async,
        )
        from receipt_agent.agents.label_evaluator.metadata_subagent import (
            evaluate_metadata_labels_async,
        )
        from receipt_agent.agents.label_evaluator.state import (
            EvaluatorState,
        )

        # Create child traces for parallel evaluations (visible as siblings in LangSmith)
        currency_trace_ctx = start_child_trace(
            "currency_evaluation",
            trace_ctx,
            metadata={"image_id": image_id, "receipt_id": receipt_id},
            inputs={
                "merchant_name": merchant_name,
                "num_visual_lines": len(visual_lines),
            },
        )
        metadata_trace_ctx = start_child_trace(
            "metadata_evaluation",
            trace_ctx,
            metadata={"image_id": image_id, "receipt_id": receipt_id},
            inputs={
                "merchant_name": merchant_name,
                "has_place": place is not None,
            },
        )
        geometric_trace_ctx = start_child_trace(
            "geometric_evaluation",
            trace_ctx,
            metadata={"image_id": image_id, "receipt_id": receipt_id},
            inputs={"num_words": len(words), "num_labels": len(labels)},
        )

        # Initialize results before try block for clean finally handling
        currency_result: list[dict] = []
        metadata_result: list[dict] = []
        geometric_result: dict = {"issues_found": 0}
        currency_duration = 0.0
        metadata_duration = 0.0
        geometric_duration = 0.0

        try:

            async def timed_eval(coro):
                start = time.time()
                result = await coro
                return result, time.time() - start

            # Run currency and metadata concurrently (both use LLM)
            currency_task = timed_eval(
                evaluate_currency_labels_async(
                    visual_lines=visual_lines,
                    patterns=line_item_patterns,
                    llm=llm_invoker,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    merchant_name=merchant_name,
                    trace_ctx=currency_trace_ctx,
                )
            )

            metadata_task = timed_eval(
                evaluate_metadata_labels_async(
                    visual_lines=visual_lines,
                    place=place,
                    llm=llm_invoker,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    merchant_name=merchant_name,
                    trace_ctx=metadata_trace_ctx,
                )
            )

            # Geometric evaluation is sync (no LLM) - run in parallel with LLM calls
            # Create EvaluatorState for geometric evaluation
            geometric_state = EvaluatorState(
                image_id=image_id,
                receipt_id=receipt_id,
                words=words,
                labels=labels,
                place=place,
                other_receipt_data=[],
                merchant_patterns=patterns,
                skip_llm_review=True,
            )

            # Run geometric evaluation concurrently with LLM calls using to_thread
            geometric_graph = create_compute_only_graph()
            geometric_config = (
                geometric_trace_ctx.get_langchain_config()
                if geometric_trace_ctx
                else None
            )

            async def run_geometric() -> tuple[dict, float]:
                start = time.time()
                result = await asyncio.to_thread(
                    run_compute_only_sync,
                    geometric_graph,
                    geometric_state,
                    geometric_config,
                )
                return result, time.time() - start

            # Wait for all evaluations concurrently
            (
                (currency_result, currency_duration),
                (
                    metadata_result,
                    metadata_duration,
                ),
                (
                    geometric_result,
                    geometric_duration,
                ),
            ) = await asyncio.gather(
                currency_task, metadata_task, run_geometric()
            )

            logger.info(
                "Phase 1 complete: currency=%d, metadata=%d, geometric issues=%d",
                len(currency_result),
                len(metadata_result),
                geometric_result.get("issues_found", 0),
            )
        finally:
            # End child traces with outputs
            end_child_trace(
                currency_trace_ctx,
                outputs={
                    "decisions_count": len(currency_result),
                },
            )
            end_child_trace(
                metadata_trace_ctx,
                outputs={
                    "decisions_count": len(metadata_result),
                },
            )
            end_child_trace(
                geometric_trace_ctx,
                outputs={
                    "issues_found": geometric_result.get("issues_found", 0),
                },
            )

        # 7. Apply Phase 1 corrections to DynamoDB
        applied_stats_currency = None
        applied_stats_metadata = None

        if dynamo_table:
            with child_trace("apply_phase1_corrections", trace_ctx):
                from receipt_agent.agents.label_evaluator.llm_review import (
                    apply_llm_decisions,
                )
                from receipt_dynamo import DynamoClient

                if dynamo_table:
                    dynamo_client = DynamoClient(table_name=dynamo_table)

                    # Apply currency corrections
                    invalid_currency = [
                        d
                        for d in currency_result
                        if d.get("llm_review", {}).get("decision") == "INVALID"
                    ]
                    if invalid_currency:
                        applied_stats_currency = apply_llm_decisions(
                            reviewed_issues=invalid_currency,
                            dynamo_client=dynamo_client,
                            execution_id=f"currency-{execution_id}",
                        )

                    # Apply metadata corrections
                    invalid_metadata = [
                        d
                        for d in metadata_result
                        if d.get("llm_review", {}).get("decision") == "INVALID"
                    ]
                    if invalid_metadata:
                        applied_stats_metadata = apply_llm_decisions(
                            reviewed_issues=invalid_metadata,
                            dynamo_client=dynamo_client,
                            execution_id=f"metadata-{execution_id}",
                        )

        # 8. Phase 2: Financial validation (needs corrected labels from Phase 1)
        financial_result = None
        financial_duration = 0.0
        if dynamo_table:
            with child_trace(
                "phase2_financial_validation", trace_ctx
            ) as financial_ctx:
                # Re-fetch labels from DynamoDB to get corrections
                from receipt_dynamo import DynamoClient

                dynamo_client = DynamoClient(table_name=dynamo_table)

                # Fetch fresh labels
                fresh_labels = []
                page, lek = dynamo_client.list_receipt_word_labels_for_receipt(
                    image_id=image_id, receipt_id=receipt_id
                )
                fresh_labels.extend(page or [])
                while lek:
                    page, lek = (
                        dynamo_client.list_receipt_word_labels_for_receipt(
                            image_id=image_id,
                            receipt_id=receipt_id,
                            last_evaluated_key=lek,
                        )
                    )
                    fresh_labels.extend(page or [])

                logger.info(
                    "Fetched %s fresh labels from DynamoDB",
                    len(fresh_labels),
                )

                # Rebuild visual lines with fresh labels
                fresh_word_contexts = build_word_contexts(words, fresh_labels)
                fresh_visual_lines = assemble_visual_lines(fresh_word_contexts)

                # Run financial validation
                from receipt_agent.agents.label_evaluator.financial_subagent import (
                    evaluate_financial_math_async,
                )

                financial_start = time.time()
                financial_result = await evaluate_financial_math_async(
                    visual_lines=fresh_visual_lines,
                    llm=llm_invoker,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    merchant_name=merchant_name,
                    trace_ctx=financial_ctx,
                )
                financial_duration = time.time() - financial_start

                # Apply financial corrections
                if financial_result:
                    from receipt_agent.agents.label_evaluator.llm_review import (
                        apply_llm_decisions,
                    )

                    invalid_financial = [
                        d
                        for d in financial_result
                        if d.get("llm_review", {}).get("decision") == "INVALID"
                    ]
                    if invalid_financial:
                        apply_llm_decisions(
                            reviewed_issues=invalid_financial,
                            dynamo_client=dynamo_client,
                            execution_id=f"financial-{execution_id}",
                        )

        # 9. Phase 3: LLM review of flagged geometric issues (if any)
        llm_review_result = None
        review_duration = 0.0
        geometric_issues_found = geometric_result.get("issues_found", 0)

        if geometric_issues_found > 0:
            with child_trace("phase3_llm_review", trace_ctx) as review_ctx:
                review_start = time.time()
                # Setup ChromaDB client (Cloud preferred, S3 snapshot fallback)
                chromadb_bucket = os.environ.get("CHROMADB_BUCKET")
                chroma_client = None
                use_chroma_cloud = (
                    os.environ.get("CHROMA_CLOUD_ENABLED", "false").lower()
                    == "true"
                )
                cloud_api_key = os.environ.get("CHROMA_CLOUD_API_KEY", "").strip()
                cloud_tenant = os.environ.get("CHROMA_CLOUD_TENANT") or None
                cloud_database = (
                    os.environ.get("CHROMA_CLOUD_DATABASE") or None
                )

                from receipt_chroma import ChromaClient

                if use_chroma_cloud and cloud_api_key:
                    try:
                        chroma_client = ChromaClient(
                            cloud_api_key=cloud_api_key,
                            cloud_tenant=cloud_tenant,
                            cloud_database=cloud_database,
                            mode="read",
                            metadata_only=True,
                        )
                        chroma_client.get_collection("words")
                        chroma_client.get_collection("lines")
                        logger.info(
                            "Using Chroma Cloud for evidence lookup "
                            "(tenant=%s, database=%s)",
                            cloud_tenant or "default",
                            cloud_database or "default",
                        )
                    except Exception as e:
                        logger.warning(
                            "Could not initialize Chroma Cloud client: %s",
                            e,
                        )
                        chroma_client = None
                elif use_chroma_cloud:
                    logger.warning(
                        "CHROMA_CLOUD_ENABLED=true but "
                        "CHROMA_CLOUD_API_KEY is empty; "
                        "falling back to S3 snapshot"
                    )

                if chroma_client is None and chromadb_bucket:
                    try:
                        chroma_root = os.environ.get(
                            "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY",
                            "/tmp/chromadb",
                        )
                        lines_path = os.path.join(chroma_root, "lines")
                        words_path = os.path.join(chroma_root, "words")
                        download_chromadb_snapshot(
                            s3, chromadb_bucket, "lines", lines_path
                        )
                        download_chromadb_snapshot(
                            s3, chromadb_bucket, "words", words_path
                        )
                        os.environ[
                            "RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"
                        ] = lines_path
                        os.environ[
                            "RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"
                        ] = words_path

                        from receipt_agent.clients.factory import (
                            create_chroma_client,
                        )

                        chroma_client = create_chroma_client(mode="read")
                        logger.info(
                            "Using S3 snapshot evidence lookup "
                            "(lines=%s, words=%s)",
                            lines_path,
                            words_path,
                        )
                    except Exception as e:
                        logger.warning(
                            "Could not initialize local ChromaDB snapshots: %s",
                            e,
                        )

                # Get issues from geometric result
                geometric_issues = geometric_result.get("issues", [])

                if geometric_issues and chroma_client:
                    from langchain_core.messages import HumanMessage
                    from receipt_agent.agents.label_evaluator.llm_review import (
                        assemble_receipt_text,
                    )
                    from receipt_agent.prompts.label_evaluator import (
                        build_receipt_context_prompt,
                        parse_batched_llm_response,
                    )
                    from receipt_agent.utils.chroma_helpers import (
                        compute_label_consensus,
                        format_label_evidence_for_prompt,
                        query_label_evidence,
                    )

                    # Gather context for issues using targeted boolean queries
                    issues_with_context = []
                    for issue in geometric_issues[:15]:  # Limit to 15
                        line_id = issue.get("line_id", 0)
                        word_id = issue.get("word_id", 0)
                        current_label = issue.get("current_label", "")

                        try:
                            # Use targeted query for the specific label being reviewed
                            if current_label and current_label != "O":
                                label_evidence = query_label_evidence(
                                    chroma_client=chroma_client,
                                    image_id=image_id,
                                    receipt_id=receipt_id,
                                    line_id=line_id,
                                    word_id=word_id,
                                    target_label=current_label,
                                    target_merchant=merchant_name,
                                    n_results_per_query=15,
                                    min_similarity=0.70,
                                    include_collections=("words", "lines"),
                                )

                                # Format evidence for prompt
                                evidence_text = (
                                    format_label_evidence_for_prompt(
                                        label_evidence,
                                        target_label=current_label,
                                        max_positive=5,
                                        max_negative=3,
                                    )
                                )

                                # Compute consensus for decision support
                                consensus, pos_count, neg_count = (
                                    compute_label_consensus(label_evidence)
                                )
                            else:
                                label_evidence = []
                                evidence_text = (
                                    "No evidence needed for O labels."
                                )
                                consensus, pos_count, neg_count = 0.0, 0, 0

                            issues_with_context.append(
                                {
                                    "issue": issue,
                                    "label_evidence": label_evidence,
                                    "evidence_text": evidence_text,
                                    "consensus": consensus,
                                    "positive_count": pos_count,
                                    "negative_count": neg_count,
                                }
                            )
                        except Exception as e:
                            logger.warning(
                                "Error gathering context for %s: %s",
                                current_label,
                                e,
                            )
                            issues_with_context.append(
                                {
                                    "issue": issue,
                                    "label_evidence": [],
                                    "evidence_text": f"Error: {e}",
                                    "consensus": 0.0,
                                    "positive_count": 0,
                                    "negative_count": 0,
                                }
                            )

                    if issues_with_context:
                        # Build prompt and call LLM
                        highlight_words = [
                            (
                                item["issue"].get("line_id"),
                                item["issue"].get("word_id"),
                            )
                            for item in issues_with_context
                        ]

                        # Convert objects to dicts for assemble_receipt_text
                        words_as_dicts = [serialize_word(w) for w in words]
                        labels_as_dicts = [
                            serialize_label(lbl) for lbl in labels
                        ]
                        receipt_text = assemble_receipt_text(
                            words=words_as_dicts,
                            labels=labels_as_dicts,
                            highlight_words=highlight_words,
                            max_lines=60,
                        )

                        prompt = build_receipt_context_prompt(
                            receipt_text=receipt_text,
                            issues_with_context=issues_with_context,
                            merchant_name=merchant_name,
                            merchant_receipt_count=0,  # Not available here
                            line_item_patterns=line_item_patterns,
                        )

                        llm_config = (
                            review_ctx.get_langchain_config()
                            if review_ctx
                            else None
                        )

                        # Make async LLM call
                        response = await llm_invoker.ainvoke(
                            [HumanMessage(content=prompt)],
                            config=llm_config,
                        )

                        # Parse response
                        response_text = (
                            response.content
                            if hasattr(response, "content")
                            else str(response)
                        )
                        chunk_reviews = parse_batched_llm_response(
                            response_text.strip(),
                            expected_count=len(issues_with_context),
                            raise_on_parse_error=False,
                        )

                        # Format results
                        llm_review_result = []
                        for i, review_result in enumerate(chunk_reviews):
                            meta = issues_with_context[i]
                            llm_review_result.append(
                                {
                                    "image_id": image_id,
                                    "receipt_id": receipt_id,
                                    "issue": meta["issue"],
                                    "llm_review": review_result,
                                }
                            )

                        # Apply LLM review decisions
                        if dynamo_table:
                            invalid_reviewed = [
                                d
                                for d in llm_review_result
                                if d.get("llm_review", {}).get("decision")
                                == "INVALID"
                            ]
                            if invalid_reviewed:
                                dynamo_client = DynamoClient(
                                    table_name=dynamo_table
                                )
                                apply_llm_decisions(
                                    reviewed_issues=invalid_reviewed,
                                    dynamo_client=dynamo_client,
                                    execution_id=execution_id,
                                )
                if chroma_client and hasattr(chroma_client, "close"):
                    try:
                        chroma_client.close()
                    except Exception as e:
                        logger.warning(
                            "Failed to close Chroma client cleanly: %s",
                            e,
                        )
                review_duration = time.time() - review_start

        # 10. Aggregate results
        decision_counts = {
            "currency": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0},
            "metadata": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0},
            "financial": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0},
        }

        for d in currency_result:
            decision = d.get("llm_review", {}).get("decision", "NEEDS_REVIEW")
            if decision in decision_counts["currency"]:
                decision_counts["currency"][decision] += 1

        for d in metadata_result:
            decision = d.get("llm_review", {}).get("decision", "NEEDS_REVIEW")
            if decision in decision_counts["metadata"]:
                decision_counts["metadata"][decision] += 1

        if financial_result:
            for d in financial_result:
                decision = d.get("llm_review", {}).get(
                    "decision", "NEEDS_REVIEW"
                )
                if decision in decision_counts["financial"]:
                    decision_counts["financial"][decision] += 1

        review_counts = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}
        if llm_review_result:
            for d in llm_review_result:
                decision = d.get("llm_review", {}).get(
                    "decision", "NEEDS_REVIEW"
                )
                if decision in review_counts:
                    review_counts[decision] += 1

        total_issues = geometric_issues_found
        for key in ("currency", "metadata", "financial"):
            total_issues += decision_counts[key].get("INVALID", 0)
            total_issues += decision_counts[key].get("NEEDS_REVIEW", 0)

        # 11. Upload results to S3
        with child_trace("upload_results", trace_ctx):
            results_s3_key = (
                f"unified/{execution_id}/{image_id}_{receipt_id}.json"
            )
            results_data = {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant_name": merchant_name,
                "issues_found": total_issues,
                "currency_words_evaluated": len(currency_result),
                "metadata_words_evaluated": len(metadata_result),
                "financial_values_evaluated": (
                    len(financial_result) if financial_result else 0
                ),
                "decisions": decision_counts,
                "currency_decisions": decision_counts["currency"],
                "metadata_decisions": decision_counts["metadata"],
                "financial_decisions": decision_counts["financial"],
                "currency_all_decisions": currency_result,
                "metadata_all_decisions": metadata_result,
                "financial_all_decisions": financial_result or [],
                "currency_duration_seconds": currency_duration,
                "metadata_duration_seconds": metadata_duration,
                "financial_duration_seconds": financial_duration,
                "geometric_issues_found": geometric_issues_found,
                "geometric_issues": geometric_result.get("issues", []),
                "geometric_duration_seconds": geometric_duration,
                "review_decisions": review_counts,
                "review_all_decisions": llm_review_result or [],
                "review_duration_seconds": review_duration,
                "applied_stats": {
                    "currency": applied_stats_currency,
                    "metadata": applied_stats_metadata,
                },
                "duration_seconds": time.time() - start_time,
            }

            upload_json_to_s3(s3, batch_bucket, results_s3_key, results_data)
            logger.info(
                "Uploaded results to s3://%s/%s",
                batch_bucket,
                results_s3_key,
            )

        result = {
            "status": "completed",
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
            "issues_found": total_issues,
            "currency_words_evaluated": len(currency_result),
            "metadata_words_evaluated": len(metadata_result),
            "financial_values_evaluated": (
                len(financial_result) if financial_result else 0
            ),
            "decisions": decision_counts,
            "currency_decisions": decision_counts["currency"],
            "metadata_decisions": decision_counts["metadata"],
            "financial_decisions": decision_counts["financial"],
            "geometric_issues_found": geometric_issues_found,
            "review_decisions": review_counts,
            "results_s3_key": results_s3_key,
        }

    except Exception as e:
        from receipt_agent.utils.llm_factory import LLMRateLimitError

        if isinstance(e, LLMRateLimitError):
            logger.error(
                "Rate limit error, propagating for Step Function retry: %s",
                e,
            )
            # Close receipt trace before re-raising so trace is properly finalized
            if receipt_trace is not None:
                end_receipt_trace(
                    receipt_trace,
                    outputs={
                        "status": "rate_limited",
                        "error": str(e),
                    },
                )
            flush_langsmith_traces()
            raise

        logger.exception("Unified evaluation failed")
        result = {
            "status": "failed",
            "error": str(e),
            "image_id": image_id or event.get("image_id"),
            "receipt_id": receipt_id or event.get("receipt_id"),
            "merchant_name": merchant_name,
            "issues_found": 0,  # Default to 0 on error
            "currency_words_evaluated": 0,
            "metadata_words_evaluated": 0,
            "financial_values_evaluated": 0,
            "decisions": {
                "currency": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0},
                "metadata": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0},
                "financial": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0},
            },
        }

    # Close the receipt trace (created with create_receipt_trace)
    if receipt_trace is not None:
        end_receipt_trace(
            receipt_trace,
            outputs={
                "status": (
                    result.get("status", "unknown") if result else "unknown"
                ),
                "issues_found": result.get("issues_found", 0) if result else 0,
            },
        )

    # Flush LangSmith traces
    flush_langsmith_traces()

    return result


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point - runs the async handler."""
    return asyncio.run(unified_receipt_evaluator(event, context))
