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
from datetime import datetime
from typing import Any, Dict, List, Optional

import boto3

# LangSmith tracing - flush before Lambda exits
try:
    from langsmith.run_trees import get_cached_client as get_langsmith_client

    HAS_LANGSMITH = True
except ImportError:
    HAS_LANGSMITH = False
    get_langsmith_client = None


def flush_langsmith_traces():
    """Flush pending LangSmith traces before Lambda returns."""
    if HAS_LANGSMITH and get_langsmith_client:
        try:
            client = get_langsmith_client()
            client.flush()
            logger.info("LangSmith traces flushed")
        except Exception as e:
            logger.warning(f"Failed to flush LangSmith traces: {e}")


logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Suppress noisy HTTP logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

s3 = boto3.client("s3")


def load_json_from_s3(bucket: str, key: str) -> Dict[str, Any]:
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


def deserialize_word(data: Dict[str, Any]):
    """Deserialize a ReceiptWord from JSON."""
    from receipt_dynamo.entities import ReceiptWord

    return ReceiptWord(
        image_id=data["image_id"],
        receipt_id=data["receipt_id"],
        line_id=data["line_id"],
        word_id=data["word_id"],
        text=data["text"],
        bounding_box=data.get("bounding_box"),
        top_right=data.get("top_right"),
        top_left=data.get("top_left"),
        bottom_right=data.get("bottom_right"),
        bottom_left=data.get("bottom_left"),
        angle_degrees=data.get("angle_degrees"),
        angle_radians=data.get("angle_radians"),
        confidence=data.get("confidence"),
        extracted_data=data.get("extracted_data"),
        embedding_status=data.get("embedding_status", "NONE"),
        is_noise=data.get("is_noise", False),
    )


def deserialize_label(data: Dict[str, Any]):
    """Deserialize a ReceiptWordLabel from JSON."""
    from receipt_dynamo.entities import ReceiptWordLabel

    timestamp = data.get("timestamp_added")
    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp)
        except ValueError:
            timestamp = None

    return ReceiptWordLabel(
        image_id=data["image_id"],
        receipt_id=data["receipt_id"],
        line_id=data["line_id"],
        word_id=data["word_id"],
        label=data["label"],
        reasoning=data.get("reasoning"),
        timestamp_added=timestamp,
        validation_status=data.get("validation_status"),
        label_proposed_by=data.get("label_proposed_by"),
        label_consolidated_from=data.get("label_consolidated_from"),
    )


def deserialize_metadata(data: Optional[Dict[str, Any]]):
    """Deserialize a ReceiptMetadata from JSON."""
    if not data:
        return None

    from receipt_dynamo.entities import ReceiptMetadata

    date_val = data.get("date")
    if isinstance(date_val, str):
        try:
            date_val = datetime.fromisoformat(date_val).date()
        except ValueError:
            date_val = None

    return ReceiptMetadata(
        image_id=data["image_id"],
        receipt_id=data["receipt_id"],
        merchant_name=data.get("merchant_name"),
        canonical_merchant_name=data.get("canonical_merchant_name"),
        place_id=data.get("place_id"),
        address=data.get("address"),
        date=date_val,
        total=data.get("total"),
        currency=data.get("currency"),
    )


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Run compute-only label evaluator with pre-loaded state.

    Input:
    {
        "data_s3_key": "data/{exec}/{image_id}_{receipt_id}.json",
        "training_s3_key": "training/{exec}/{merchant_hash}.json",
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
    training_s3_key = event.get("training_s3_key")
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")

    if not data_s3_key:
        raise ValueError("data_s3_key is required")
    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    start_time = time.time()

    try:
        # 1. Load target receipt data from S3
        logger.info(f"Loading receipt data from s3://{batch_bucket}/{data_s3_key}")
        target_data = load_json_from_s3(batch_bucket, data_s3_key)

        image_id = target_data["image_id"]
        receipt_id = target_data["receipt_id"]

        # Deserialize entities
        words = [deserialize_word(w) for w in target_data.get("words", [])]
        labels = [deserialize_label(l) for l in target_data.get("labels", [])]
        metadata = deserialize_metadata(target_data.get("metadata"))

        logger.info(
            f"Loaded {len(words)} words, {len(labels)} labels "
            f"for {image_id}#{receipt_id}"
        )

        # 2. Load training data from S3
        other_receipt_data = []
        if training_s3_key:
            logger.info(
                f"Loading training data from s3://{batch_bucket}/{training_s3_key}"
            )
            training_data = load_json_from_s3(batch_bucket, training_s3_key)

            from receipt_agent.agents.label_evaluator.state import OtherReceiptData

            for r in training_data.get("receipts", []):
                try:
                    other_receipt_data.append(
                        OtherReceiptData(
                            metadata=deserialize_metadata(r.get("metadata")),
                            words=[deserialize_word(w) for w in r.get("words", [])],
                            labels=[deserialize_label(l) for l in r.get("labels", [])],
                        )
                    )
                except Exception as e:
                    logger.warning(f"Error deserializing training receipt: {e}")
                    continue

            logger.info(f"Loaded {len(other_receipt_data)} training receipts")

        # 3. Create pre-loaded EvaluatorState
        from receipt_agent.agents.label_evaluator.state import EvaluatorState

        state = EvaluatorState(
            image_id=image_id,
            receipt_id=receipt_id,
            words=words,
            labels=labels,
            metadata=metadata,
            other_receipt_data=other_receipt_data,
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
        emf_metrics.log_metrics(
            metrics={
                "IssuesFound": result.get("issues_found", 0),
                "TrainingReceipts": len(other_receipt_data),
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
            "image_id": event.get("image_id"),
            "receipt_id": event.get("receipt_id"),
            "issues_found": 0,
        }
