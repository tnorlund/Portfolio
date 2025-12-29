"""Evaluate labels with per-receipt tracing.

This handler creates a ROOT trace for each receipt, not a child of an
execution-level trace. Each receipt gets its own complete trace in LangSmith
with metadata including image_id, receipt_id, and merchant_name.

The trace includes reference spans for:
- DiscoverPatterns: Line item structure patterns discovered for this merchant
- ComputePatterns: Merchant-level spatial patterns computed from training data

The LLMReview Lambda will join this receipt's trace as a child.
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
        child_trace,
        create_receipt_trace,
        flush_langsmith_traces,
    )

    from utils.s3_helpers import load_json_from_s3, upload_json_to_s3

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
        child_trace,
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
    from utils.s3_helpers import load_json_from_s3, upload_json_to_s3

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
    patterns_s3_key = event.get("patterns_s3_key")
    line_item_patterns_s3_key = event.get("line_item_patterns_s3_key")
    execution_id = event.get("execution_id", "unknown")
    execution_arn = event.get("execution_arn", f"local:{execution_id}")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    merchant_name = event.get("merchant_name", "unknown")

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
                "data_s3_key": data_s3_key,
                "patterns_s3_key": patterns_s3_key,
                "word_count": len(words),
                "label_count": len(labels),
            },
            metadata={
                "execution_id": execution_id,
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
        from tracing import TraceContext

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

        # 3. Add reference span for DiscoverPatterns (line item structure)
        if line_item_patterns_s3_key and trace_ctx.run_tree:
            with child_trace(
                "DiscoverPatterns",
                trace_ctx,
                metadata={
                    "patterns_s3_key": line_item_patterns_s3_key,
                    "merchant_name": merchant_name,
                    "note": "Line item structure patterns discovered for merchant",
                },
            ):
                logger.info(
                    "Added DiscoverPatterns reference: %s",
                    line_item_patterns_s3_key,
                )

        # 4. Load pre-computed merchant patterns from S3
        merchant_patterns = None
        if patterns_s3_key:
            with child_trace(
                "ComputePatterns",
                trace_ctx,
                metadata={
                    "patterns_s3_key": patterns_s3_key,
                    "merchant_name": merchant_name,
                },
            ):
                logger.info(
                    "Loading patterns from s3://%s/%s",
                    batch_bucket,
                    patterns_s3_key,
                )
                patterns_data = load_json_from_s3(
                    s3, batch_bucket, patterns_s3_key, logger=logger
                )
                merchant_patterns = deserialize_patterns(patterns_data)

                if merchant_patterns:
                    logger.info(
                        "Loaded patterns for %s (%s receipts)",
                        merchant_patterns.merchant_name,
                        merchant_patterns.receipt_count,
                    )
                else:
                    logger.info("No patterns available for merchant")

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
        with child_trace(
            "EvaluateLabels",
            trace_ctx,
            metadata={
                "image_id": image_id,
                "receipt_id": receipt_id,
                "word_count": len(words),
                "label_count": len(labels),
            },
        ):
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
                "IssuesFound": result.get("issues_found", 0),
                "PatternReceiptCount": pattern_receipt_count,
                "ComputeTimeSeconds": round(compute_time, 3),
                "ProcessingTimeSeconds": round(processing_time, 2),
            },
            dimensions={},
            properties={
                "execution_id": execution_id,
                "image_id": image_id,
                "receipt_id": receipt_id,
            },
            units={
                "ComputeTimeSeconds": "Seconds",
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
                "EvaluationFailed": 1,
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
