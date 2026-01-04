"""Evaluate labels with per-receipt tracing.

This handler creates a ROOT trace for each receipt. Each receipt gets its own
complete trace in LangSmith that shows the full pipeline:

ReceiptEvaluation
├── DiscoverPatterns (historical span from Phase 1 batch computation)
├── ComputePatterns (historical span from Phase 1 batch computation)
├── EvaluateLabels (actual geometric anomaly detection)
└── LLMReview (via separate Lambda, if issues found)

The pattern computation happens once per merchant in Phase 1 (batch optimization),
but appears in each receipt's trace with the actual timing from that computation.
This makes traces look like production (full pipeline per receipt) while still
benefiting from shared pattern computation in the Step Function.
"""

# pylint: disable=import-outside-toplevel,wrong-import-position
# Lambda handlers delay imports until runtime for cold start optimization

import logging
import os

# Import tracing utilities - works in both container and local environments
import sys
import time
from typing import TYPE_CHECKING, Any

import boto3

try:
    # Container environment: tracing.py is in same directory
    from tracing import (
        TRACING_VERSION,
        TraceContext,
        child_trace,
        create_historical_span,
        create_receipt_trace,
        flush_langsmith_traces,
    )

    from utils.s3_helpers import get_merchant_hash, load_json_from_s3, upload_json_to_s3

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
        flush_langsmith_traces,
    )

    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "lambdas",
        ),
    )
    from utils.s3_helpers import get_merchant_hash, load_json_from_s3, upload_json_to_s3

    _tracing_import_source = "local"

if TYPE_CHECKING:
    from handlers.evaluator_types import EvaluateLabelsOutput

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Suppress noisy HTTP logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], _context: Any) -> "EvaluateLabelsOutput":
    """
    Run compute-only label evaluator with per-receipt tracing.

    Creates a ROOT trace for this receipt (not a child of execution trace).
    Each receipt gets its own complete trace in LangSmith. The trace_id is
    deterministic (computed from execution_arn, image_id, receipt_id), so
    parallel evaluators (Currency, Metadata) can join this trace using the
    receipt_trace_id from FetchReceiptData.

    Input:
    {
        "data_s3_key": "data/{exec}/{image_id}_{receipt_id}.json",
        "patterns_s3_key": "patterns/{exec}/{merchant_hash}.json",
        "line_item_patterns_s3_key": "line_item_patterns/{merchant_hash}.json",
        "execution_id": "abc123",
        "execution_arn": "arn:aws:states:...",
        "batch_bucket": "bucket-name",
        "merchant_name": "Costco",
        "enable_tracing": true,
        "receipt_trace_id": "..."  # From FetchReceiptData (for verification)
    }

    Output:
    {
        "status": "completed",
        "results_s3_key": "results/{exec}/{image_id}_{receipt_id}.json",
        "image_id": "img1",
        "receipt_id": 1,
        "issues_found": 3,
        "trace_id": "...",           # For LLMReview to join
        "root_run_id": "...",        # For LLMReview to join
        "root_dotted_order": "..."   # For LLMReview to join
    }
    """
    # Log tracing module info to verify correct version is loaded
    logger.info(
        "[EvaluateLabels] Tracing module loaded: version=%s, source=%s",
        TRACING_VERSION,
        _tracing_import_source,
    )

    # Allow runtime override of LangSmith project via Step Function input
    langchain_project = event.get("langchain_project")
    if langchain_project:
        os.environ["LANGCHAIN_PROJECT"] = langchain_project
        logger.info("LangSmith project set to: %s", langchain_project)

    data_s3_key = event.get("data_s3_key")
    execution_id = event.get("execution_id", "unknown")
    execution_arn = event.get("execution_arn", f"local:{execution_id}")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    merchant_name = event.get("merchant_name", "unknown")

    # Compute S3 keys from merchant_name if not provided
    # This enables the two-phase architecture where receipts are processed
    # independently after all patterns have been computed
    patterns_s3_key = event.get("patterns_s3_key")
    line_item_patterns_s3_key = event.get("line_item_patterns_s3_key")

    if merchant_name and merchant_name != "unknown":
        merchant_hash = get_merchant_hash(merchant_name)
        if not patterns_s3_key:
            patterns_s3_key = f"patterns/{execution_id}/{merchant_hash}.json"
            logger.info("Computed patterns_s3_key from merchant_name: %s", patterns_s3_key)
        if not line_item_patterns_s3_key:
            line_item_patterns_s3_key = f"line_item_patterns/{merchant_hash}.json"
            logger.info(
                "Computed line_item_patterns_s3_key from merchant_name: %s",
                line_item_patterns_s3_key,
            )

    # Check if tracing is enabled
    enable_tracing = event.get("enable_tracing", True)

    if not data_s3_key:
        raise ValueError("data_s3_key is required")
    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    start_time = time.time()

    # Initialize identifiers for error reporting
    image_id = None
    receipt_id = None
    receipt_trace = None

    try:
        # Import shared deserialization utilities
        from utils.serialization import (
            deserialize_label,
            deserialize_patterns,
            deserialize_place,
            deserialize_word,
        )

        # 1. Load target receipt data from S3 FIRST (need image_id, receipt_id for trace)
        logger.info(
            "Loading receipt data from s3://%s/%s",
            batch_bucket,
            data_s3_key,
        )
        target_data = load_json_from_s3(
            s3, batch_bucket, data_s3_key, logger=logger
        )

        image_id = target_data.get("image_id")
        receipt_id = target_data.get("receipt_id")

        if not image_id or receipt_id is None:
            raise ValueError("image_id and receipt_id are required in data")

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

        # 2. Create ROOT trace for this receipt
        # The trace_id is deterministic, so parallel evaluators (Currency, Metadata)
        # can join this trace using the same receipt_trace_id from FetchReceiptData.
        receipt_trace = create_receipt_trace(
            execution_arn=execution_arn,
            image_id=image_id,
            receipt_id=receipt_id,
            merchant_name=merchant_name,
            name="ReceiptEvaluation",
            inputs={
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant_name": merchant_name,
                "word_count": len(words),
                "label_count": len(labels),
            },
            metadata={
                "execution_id": execution_id,
                "data_s3_key": data_s3_key,
                "patterns_s3_key": patterns_s3_key,
            },
            enable_tracing=enable_tracing,
        )

        # Verify trace_id matches FetchReceiptData (for debugging)
        expected_trace_id = event.get("receipt_trace_id")
        if expected_trace_id and expected_trace_id != receipt_trace.trace_id:
            logger.warning(
                "TRACE ID MISMATCH: FetchReceiptData=%s, EvaluateLabels=%s",
                expected_trace_id[:8],
                receipt_trace.trace_id[:8],
            )
        else:
            logger.info(
                "Receipt trace created: trace_id=%s (matches FetchReceiptData)",
                receipt_trace.trace_id[:8],
            )

        # Create a TraceContext for child_trace compatibility
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

        # 3. Load line item patterns (from Phase 1 DiscoverPatterns) and create historical span
        # The _trace_metadata contains timing from when patterns were discovered
        line_item_patterns_data = None
        if line_item_patterns_s3_key:
            logger.info(
                "Loading line item patterns from s3://%s/%s",
                batch_bucket,
                line_item_patterns_s3_key,
            )
            line_item_patterns_data = load_json_from_s3(
                s3, batch_bucket, line_item_patterns_s3_key, logger=logger, allow_missing=True
            )

            if line_item_patterns_data:
                # Create historical DiscoverPatterns span using stored timing
                discovery_metadata = line_item_patterns_data.get("_trace_metadata", {})
                if discovery_metadata.get("discovery_start_time"):
                    # Extract discovered patterns for rich output
                    discovered_patterns = line_item_patterns_data.get("patterns", {})
                    pattern_categories = list(discovered_patterns.keys()) if discovered_patterns else []
                    line_item_labels = line_item_patterns_data.get("line_item_labels", [])

                    create_historical_span(
                        parent_ctx=trace_ctx,
                        name="DiscoverPatterns",
                        start_time_iso=discovery_metadata["discovery_start_time"],
                        end_time_iso=discovery_metadata["discovery_end_time"],
                        duration_seconds=discovery_metadata.get("discovery_duration_seconds", 0),
                        run_type="llm",
                        inputs={
                            "merchant_name": merchant_name,
                            "sample_receipt_count": discovery_metadata.get("sample_receipt_count", 0),
                            "llm_model": discovery_metadata.get("llm_model", "unknown"),
                        },
                        outputs={
                            "status": discovery_metadata.get("discovery_status", "unknown"),
                            "pattern_categories": pattern_categories,
                            "line_item_labels": line_item_labels,
                            "patterns_discovered": len(pattern_categories),
                        },
                        metadata={
                            "llm_call_duration_seconds": discovery_metadata.get("llm_call_duration_seconds", 0),
                            "load_receipts_duration_seconds": discovery_metadata.get("load_receipts_duration_seconds", 0),
                            "build_prompt_duration_seconds": discovery_metadata.get("build_prompt_duration_seconds", 0),
                            "batch_computed": True,
                        },
                    )
                    logger.info(
                        "Added DiscoverPatterns historical span (%.2fs, %d categories)",
                        discovery_metadata.get("discovery_duration_seconds", 0),
                        len(pattern_categories),
                    )

        # 4. Load geometric patterns (from Phase 1 ComputePatterns) and create historical span
        # Patterns were computed in Phase 1 (ComputeAllPatterns) - we just load them here.
        merchant_patterns = None
        patterns_data = None
        if patterns_s3_key:
            logger.info(
                "Loading patterns from s3://%s/%s",
                batch_bucket,
                patterns_s3_key,
            )
            # Use allow_missing=True for graceful fallback when patterns don't exist
            patterns_data = load_json_from_s3(
                s3, batch_bucket, patterns_s3_key, logger=logger, allow_missing=True
            )

            if patterns_data:
                # Create historical ComputePatterns span using stored timing
                computation_metadata = patterns_data.get("_trace_metadata", {})
                if computation_metadata.get("computation_start_time"):
                    # Extract constellation/geometry pattern info for rich output
                    label_pair_geometry = patterns_data.get("label_pair_geometry", {})
                    label_geometry = patterns_data.get("label_geometry", {})
                    constellation_names = list(label_pair_geometry.keys()) if label_pair_geometry else []
                    individual_labels = list(label_geometry.keys()) if label_geometry else []

                    create_historical_span(
                        parent_ctx=trace_ctx,
                        name="ComputePatterns",
                        start_time_iso=computation_metadata["computation_start_time"],
                        end_time_iso=computation_metadata["computation_end_time"],
                        duration_seconds=computation_metadata.get("computation_duration_seconds", 0),
                        run_type="chain",
                        inputs={
                            "merchant_name": merchant_name,
                            "training_receipt_count": computation_metadata.get("training_receipt_count", 0),
                        },
                        outputs={
                            "status": computation_metadata.get("computation_status", "unknown"),
                            "constellation_patterns": constellation_names,
                            "constellation_count": len(constellation_names),
                            "individual_label_patterns": individual_labels,
                            "individual_label_count": len(individual_labels),
                        },
                        metadata={
                            "load_data_duration_seconds": computation_metadata.get("load_data_duration_seconds", 0),
                            "compute_patterns_duration_seconds": computation_metadata.get("compute_patterns_duration_seconds", 0),
                            "batch_computed": True,
                        },
                    )
                    logger.info(
                        "Added ComputePatterns historical span (%.2fs, %d constellations, %d labels)",
                        computation_metadata.get("computation_duration_seconds", 0),
                        len(constellation_names),
                        len(individual_labels),
                    )

                # Deserialize patterns for evaluation
                merchant_patterns = deserialize_patterns(patterns_data)
                if merchant_patterns:
                    logger.info(
                        "Loaded patterns for %s (%s receipts)",
                        merchant_patterns.merchant_name,
                        merchant_patterns.receipt_count,
                    )
                else:
                    logger.info("Patterns file exists but no patterns available")
            else:
                logger.warning(
                    "No patterns found at s3://%s/%s - proceeding without patterns",
                    batch_bucket,
                    patterns_s3_key,
                )

        # 5. Create pre-loaded EvaluatorState with patterns
        from receipt_agent.agents.label_evaluator.state import EvaluatorState

        state = EvaluatorState(
            image_id=image_id,
            receipt_id=receipt_id,
            words=words,
            labels=labels,
            place=place,
            other_receipt_data=[],  # Not needed - patterns already computed
            merchant_patterns=merchant_patterns,  # Pre-computed patterns
            skip_llm_review=True,  # Always skip LLM in compute-only
        )

        # 6. Run compute-only graph with child trace
        pattern_count = merchant_patterns.receipt_count if merchant_patterns else 0
        with child_trace(
            "EvaluateLabels",
            trace_ctx,
            inputs={
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant_name": merchant_name,
                "word_count": len(words),
                "label_count": len(labels),
                "pattern_receipt_count": pattern_count,
            },
            metadata={
                "data_s3_key": data_s3_key,
                "patterns_s3_key": patterns_s3_key,
            },
        ) as eval_ctx:
            logger.info("Running compute-only graph...")
            compute_start = time.time()

            from receipt_agent.agents.label_evaluator import (
                create_compute_only_graph,
                run_compute_only_sync,
            )

            graph = create_compute_only_graph()
            result = run_compute_only_sync(graph, state)

            compute_time = time.time() - compute_start
            logger.info(
                "Compute-only graph completed in %.3fs, found %s issues",
                compute_time,
                result.get("issues_found", 0),
            )

            # Set rich outputs
            eval_ctx.set_outputs({
                "issues_found": result.get("issues_found", 0),
                "compute_time_seconds": round(compute_time, 3),
                "flagged_words": result.get("flagged_words", []),
                "issues": result.get("issues", []),
            })

        # 7. Upload results to S3
        results_s3_key = f"results/{execution_id}/{image_id}_{receipt_id}.json"
        upload_json_to_s3(s3, batch_bucket, results_s3_key, result)

        logger.info(
            "Uploaded results to s3://%s/%s",
            batch_bucket,
            results_s3_key,
        )

        # 8. Log metrics
        from utils.emf_metrics import emf_metrics

        processing_time = time.time() - start_time
        pattern_receipt_count = (
            merchant_patterns.receipt_count if merchant_patterns else 0
        )
        emf_metrics.log_metrics(
            metrics={
                "WordsFlaggedForReview": result.get("issues_found", 0),
                "TrainingReceiptCount": pattern_receipt_count,
                "PatternAnalysisSeconds": round(compute_time, 3),
                "ProcessingTimeSeconds": round(processing_time, 2),
            },
            dimensions={},
            properties={
                "execution_id": execution_id,
                "image_id": image_id,
                "receipt_id": receipt_id,
            },
            units={
                "PatternAnalysisSeconds": "Seconds",
                "ProcessingTimeSeconds": "Seconds",
            },
        )

        # 9. Build output with trace info for LLMReview to join
        output = {
            "status": "completed",
            "results_s3_key": results_s3_key,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
            "issues_found": result.get("issues_found", 0),
            "compute_time_seconds": round(compute_time, 3),
            # Trace info for LLMReview to join this receipt's trace
            "trace_id": receipt_trace.trace_id,
            "root_run_id": receipt_trace.root_run_id,
            "root_dotted_order": receipt_trace.root_dotted_order,
        }

        # NOTE: Do NOT close the receipt trace here!
        # The trace is closed by either:
        # - LLMReviewReceipt (if issues > 0)
        # - CloseReceiptTrace (if issues == 0, after all parallel branches complete)
        # This ensures Currency and Metadata evaluators finish before trace closes.

        # Flush LangSmith traces
        flush_langsmith_traces()

        return output

    except Exception as e:
        logger.error("Error evaluating labels: %s", e, exc_info=True)

        # Log failure metrics
        from utils.emf_metrics import emf_metrics

        processing_time = time.time() - start_time
        emf_metrics.log_metrics(
            metrics={
                "PatternAnalysisFailed": 1,
                "ProcessingTimeSeconds": round(processing_time, 2),
            },
            dimensions={},
            properties={
                "execution_id": execution_id,
                "error": str(e),
            },
            units={
                "ProcessingTimeSeconds": "Seconds",
            },
        )

        # NOTE: Do NOT close the receipt trace on error here.
        # The trace will be closed by CloseReceiptTrace or left open.
        # Parallel branches (Currency/Metadata) may still be running.

        # Flush LangSmith traces even on error
        flush_langsmith_traces()

        safe_image_id = image_id or ""
        safe_receipt_id = receipt_id if receipt_id is not None else -1
        return {
            "status": "error",
            "error": str(e),
            "image_id": safe_image_id,
            "receipt_id": safe_receipt_id,
            "issues_found": 0,
        }
