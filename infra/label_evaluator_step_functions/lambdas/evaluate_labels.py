"""
Evaluate Labels Lambda Handler

Runs the compute-only label evaluator graph with pre-loaded state from S3.
This is the core evaluation Lambda that detects labeling issues using
spatial pattern analysis.

Environment Variables:
- BATCH_BUCKET: S3 bucket for data and results
- LANGCHAIN_API_KEY: LangSmith API key for tracing
- LANGCHAIN_TRACING_V2: Enable tracing ("true")
- LANGCHAIN_PROJECT: LangSmith project name
"""

import json
import logging
import os
import time
from typing import Any

import boto3

# Import shared tracing utility
from utils.tracing import flush_langsmith_traces


logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Suppress noisy HTTP logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

s3 = boto3.client("s3")


def load_json_from_s3(bucket: str, key: str) -> dict[str, Any]:
    """Load JSON data from S3."""
    response = s3.get_object(Bucket=bucket, Key=key)
    return json.loads(response["Body"].read().decode("utf-8"))


def upload_json_to_s3(bucket: str, key: str, data: Any) -> None:
    """Upload JSON data to S3."""
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(data, indent=2, default=str).encode("utf-8"),
        ContentType="application/json",
    )


# Import shared deserialization utilities
from utils.serialization import (
    deserialize_label,
    deserialize_patterns,
    deserialize_place,
    deserialize_word,
)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Run compute-only label evaluator with pre-loaded state and patterns.

    Input:
    {
        "data_s3_key": "data/{exec}/{image_id}_{receipt_id}.json",
        "patterns_s3_key": "patterns/{exec}/{merchant_hash}.json",
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "skip_llm_review": true,
        "dry_run": true
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
    data_s3_key = event.get("data_s3_key")
    patterns_s3_key = event.get("patterns_s3_key")
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")

    if not data_s3_key:
        raise ValueError("data_s3_key is required")
    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    start_time = time.time()

    # Initialize identifiers for error reporting
    image_id = None
    receipt_id = None

    try:
        # 1. Load target receipt data from S3
        logger.info(f"Loading receipt data from s3://{batch_bucket}/{data_s3_key}")
        target_data = load_json_from_s3(batch_bucket, data_s3_key)

        image_id = target_data.get("image_id")
        receipt_id = target_data.get("receipt_id")

        # Deserialize entities
        words = [deserialize_word(w) for w in target_data.get("words", [])]
        labels = [
            deserialize_label(label_data)
            for label_data in target_data.get("labels", [])
        ]
        place = deserialize_place(target_data.get("place"))

        logger.info(
            f"Loaded {len(words)} words, {len(labels)} labels "
            f"for {image_id}#{receipt_id}"
        )

        # 2. Load pre-computed patterns from S3
        merchant_patterns = None
        if patterns_s3_key:
            logger.info(
                f"Loading patterns from s3://{batch_bucket}/{patterns_s3_key}"
            )
            patterns_data = load_json_from_s3(batch_bucket, patterns_s3_key)
            merchant_patterns = deserialize_patterns(patterns_data)

            if merchant_patterns:
                logger.info(
                    f"Loaded patterns for {merchant_patterns.merchant_name} "
                    f"({merchant_patterns.receipt_count} receipts)"
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

        # 4. Run compute-only graph
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
            f"Compute-only graph completed in {compute_time:.3f}s, "
            f"found {result.get('issues_found', 0)} issues"
        )

        # 5. Upload results to S3
        results_s3_key = f"results/{execution_id}/{image_id}_{receipt_id}.json"
        upload_json_to_s3(batch_bucket, results_s3_key, result)

        logger.info(f"Uploaded results to s3://{batch_bucket}/{results_s3_key}")

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

        # Flush LangSmith traces
        flush_langsmith_traces()

        return {
            "status": "completed",
            "results_s3_key": results_s3_key,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "issues_found": result.get("issues_found", 0),
            "compute_time_seconds": round(compute_time, 3),
        }

    except Exception as e:
        logger.error(f"Error evaluating labels: {e}", exc_info=True)

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

        return {
            "status": "error",
            "error": str(e),
            "image_id": image_id,
            "receipt_id": receipt_id,
            "issues_found": 0,
        }

