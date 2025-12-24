"""Evaluate labels with trace propagation.

This handler creates a child trace using deterministic UUIDs based on
execution ARN, batch_index, and receipt_index. The trace_id and root_run_id
are passed from the upstream DiscoverLineItemPatterns Lambda.
"""

# pylint: disable=import-outside-toplevel,wrong-import-position
# Lambda handlers delay imports until runtime for cold start optimization

import logging
import os
import time
from typing import TYPE_CHECKING, Any

import boto3

# Import tracing utilities - works in both container and local environments
import sys

try:
    # Container environment: tracing.py is in same directory
    from tracing import (
        TRACING_VERSION,
        child_trace,
        flush_langsmith_traces,
        state_trace,
    )
    from utils.s3_helpers import load_json_from_s3, upload_json_to_s3
    _tracing_import_source = "container"
except ImportError:
    # Local/development environment: use path relative to this file
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "lambdas", "utils"
    ))
    from tracing import (
        TRACING_VERSION,
        child_trace,
        flush_langsmith_traces,
        state_trace,
    )
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "lambdas"
    ))
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
    Run compute-only label evaluator with trace propagation.

    Input:
    {
        "data_s3_key": "data/{exec}/{image_id}_{receipt_id}.json",
        "patterns_s3_key": "patterns/{exec}/{merchant_hash}.json",
        "execution_id": "abc123",
        "execution_arn": "arn:aws:states:...",  # From $$.Execution.Id
        "batch_bucket": "bucket-name",
        "trace_id": "...",       # From upstream Lambda
        "root_run_id": "...",    # From upstream Lambda
        "batch_index": 0,        # From Map state
        "receipt_index": 0       # From Map state
    }

    Output:
    {
        "status": "completed",
        "results_s3_key": "results/{exec}/{image_id}_{receipt_id}.json",
        "image_id": "img1",
        "receipt_id": 1,
        "issues_found": 3
    }
    """
    # Log tracing module info to verify correct version is loaded
    logger.info(
        "[EvaluateLabels] Tracing module loaded: version=%s, source=%s",
        TRACING_VERSION,
        _tracing_import_source,
    )

    data_s3_key = event.get("data_s3_key")
    patterns_s3_key = event.get("patterns_s3_key")
    execution_id = event.get("execution_id", "unknown")
    execution_arn = event.get("execution_arn", f"local:{execution_id}")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")

    # Get trace IDs from upstream Lambda
    trace_id = event.get("trace_id", "")
    root_run_id = event.get("root_run_id", "")
    root_dotted_order = event.get("root_dotted_order")

    # Get Map state indices for unique trace IDs
    batch_index = event.get("batch_index", 0)
    receipt_index = event.get("receipt_index", 0)

    if not data_s3_key:
        raise ValueError("data_s3_key is required")
    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    start_time = time.time()

    # Initialize identifiers for error reporting
    image_id = None
    receipt_id = None

    # Create unique map_index from batch and receipt indices
    map_index = batch_index * 1000 + receipt_index  # Unique per receipt

    try:
        # Create a child trace using deterministic UUID
        with state_trace(
            execution_arn=execution_arn,
            state_name="EvaluateLabels",
            trace_id=trace_id,
            root_run_id=root_run_id,
            root_dotted_order=root_dotted_order,
            map_index=map_index,
            inputs={"data_s3_key": data_s3_key},
            metadata={
                "batch_index": batch_index,
                "receipt_index": receipt_index,
            },
            tags=["evaluate-labels"],
        ) as trace_ctx:

            # Import shared deserialization utilities
            from utils.serialization import (
                deserialize_label,
                deserialize_patterns,
                deserialize_place,
                deserialize_word,
            )

            # 1. Load target receipt data from S3
            with child_trace("load_receipt_data", trace_ctx):
                logger.info(
                    "Loading receipt data from s3://%s/%s",
                    batch_bucket,
                    data_s3_key,
                )
                target_data = load_json_from_s3(s3, batch_bucket, data_s3_key)

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

            # 2. Load pre-computed patterns from S3
            with child_trace("load_patterns", trace_ctx):
                merchant_patterns = None
                if patterns_s3_key:
                    logger.info(
                        "Loading patterns from s3://%s/%s",
                        batch_bucket,
                        patterns_s3_key,
                    )
                    patterns_data = load_json_from_s3(
                        s3, batch_bucket, patterns_s3_key
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

            # 3. Create pre-loaded EvaluatorState with patterns
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

            # 4. Run compute-only graph with child trace
            # child_trace sets up tracing_context which LangGraph auto-tracing uses
            with child_trace(
                f"run_evaluation:{image_id[:8]}#{receipt_id}",
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

            # 5. Upload results to S3
            results_s3_key = f"results/{execution_id}/{image_id}_{receipt_id}.json"
            upload_json_to_s3(s3, batch_bucket, results_s3_key, result)

            logger.info(
                "Uploaded results to s3://%s/%s",
                batch_bucket,
                results_s3_key,
            )

            # 6. Log metrics
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

            output = {
                "status": "completed",
                "results_s3_key": results_s3_key,
                "image_id": image_id,
                "receipt_id": receipt_id,
                "issues_found": result.get("issues_found", 0),
                "compute_time_seconds": round(compute_time, 3),
            }

            trace_ctx.set_outputs(output)

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
