"""
Lambda handler for job training metrics.

Returns F1 scores, confusion matrices, and per-label metrics for a training job.
"""

import json
import logging
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]

# Featured job ID - hardcoded for the portfolio demo
# This can be updated to point to the best trained model
FEATURED_JOB_ID = os.environ.get(
    "FEATURED_JOB_ID",
    "b8af06b6-27eb-41bc-846a-8b0ff93b8845"  # confusion-matrix-test-2
)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Get training metrics for a specific job.

    Path parameters:
        job_id: UUID of the training job

    Query parameters:
        epoch: (optional) Filter to specific epoch
        include_confusion_matrix: (optional) Include confusion matrix (default: true)
        collapse_bio: (optional) Collapse B-/I- tags to entity level (default: false)
    """
    try:
        logger.info("Received event: %s", json.dumps(event))

        # Extract job_id from path parameters
        path_params = event.get("pathParameters") or {}
        job_id = path_params.get("job_id")

        if not job_id:
            return _error_response(400, "Missing required path parameter: job_id")

        # Support "featured" as a special job_id alias
        if job_id == "featured":
            job_id = FEATURED_JOB_ID

        # Parse query parameters
        query_params = event.get("queryStringParameters") or {}
        epoch_filter = query_params.get("epoch")
        if epoch_filter is not None:
            try:
                epoch_filter = int(epoch_filter)
            except ValueError:
                return _error_response(400, "epoch must be an integer")

        include_cm = query_params.get("include_confusion_matrix", "true").lower() != "false"
        collapse_bio = query_params.get("collapse_bio", "false").lower() == "true"

        # Initialize DynamoDB client
        client = DynamoClient(table_name=DYNAMODB_TABLE_NAME, region="us-east-1")

        # Get job metadata
        try:
            job = client.get_job(job_id)
        except EntityNotFoundError:
            return _error_response(404, f"Job not found: {job_id}")

        # Get all metrics for the job
        metrics_data = _fetch_all_metrics(client, job_id)

        # Aggregate metrics by epoch
        epochs = _aggregate_by_epoch(
            metrics_data,
            include_confusion_matrix=include_cm,
            collapse_bio=collapse_bio,
            epoch_filter=epoch_filter,
        )

        # Find best epoch
        best_epoch = None
        best_f1 = 0.0
        for epoch_data in epochs:
            f1 = epoch_data.get("metrics", {}).get("val_f1", 0)
            if f1 > best_f1:
                best_f1 = f1
                best_epoch = epoch_data.get("epoch")

        # Mark best epoch with is_best flag
        for epoch_data in epochs:
            epoch_data["is_best"] = epoch_data.get("epoch") == best_epoch

        response_body = {
            "job_id": job_id,
            "job_name": job.name,
            "status": job.status,
            "created_at": job.created_at,
            "epochs": epochs,
            "best_epoch": best_epoch,
            "best_f1": best_f1,
            "total_epochs": len(epochs),
        }

        return _success_response(response_body)

    except Exception as e:
        logger.exception("Error processing request")
        return _error_response(500, str(e))


def _fetch_all_metrics(client: DynamoClient, job_id: str) -> Dict[str, List]:
    """Fetch all relevant metrics for a job."""
    metric_names = [
        "val_f1",
        "val_precision",
        "val_recall",
        "eval_loss",
        "train_loss",
        "learning_rate",
        "confusion_matrix",
    ]

    result = {}
    for name in metric_names:
        metrics, _ = client.list_job_metrics(job_id, metric_name=name)
        result[name] = metrics

    # Fetch per-label metrics (they have dynamic names like label_ADDRESS_f1)
    all_metrics, _ = client.list_job_metrics(job_id)
    label_metrics = [m for m in all_metrics if m.metric_name.startswith("label_")]
    result["label_metrics"] = label_metrics

    return result


def _aggregate_by_epoch(
    metrics_data: Dict[str, List],
    include_confusion_matrix: bool = True,
    collapse_bio: bool = False,
    epoch_filter: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Aggregate all metrics by epoch."""
    epochs_dict: Dict[int, Dict[str, Any]] = defaultdict(lambda: {
        "epoch": None,
        "metrics": {},
        "per_label": {},
    })

    # Process scalar metrics
    scalar_metrics = ["val_f1", "val_precision", "val_recall", "eval_loss", "train_loss", "learning_rate"]
    for metric_name in scalar_metrics:
        for m in metrics_data.get(metric_name, []):
            if m.epoch is None:
                continue
            if epoch_filter is not None and m.epoch != epoch_filter:
                continue
            epochs_dict[m.epoch]["epoch"] = m.epoch
            epochs_dict[m.epoch]["metrics"][metric_name] = m.value

    # Process confusion matrices
    if include_confusion_matrix:
        for m in metrics_data.get("confusion_matrix", []):
            if m.epoch is None:
                continue
            if epoch_filter is not None and m.epoch != epoch_filter:
                continue
            epochs_dict[m.epoch]["epoch"] = m.epoch

            cm_data = m.value
            if collapse_bio and isinstance(cm_data, dict):
                cm_data = _collapse_bio_tags(cm_data)

            epochs_dict[m.epoch]["confusion_matrix"] = cm_data

    # Process per-label metrics
    for m in metrics_data.get("label_metrics", []):
        if m.epoch is None:
            continue
        if epoch_filter is not None and m.epoch != epoch_filter:
            continue

        # Parse label name and metric type from metric_name
        # Format: label_{LABEL_NAME}_{metric_type}
        parts = m.metric_name.split("_")
        if len(parts) >= 3:
            metric_type = parts[-1]  # f1, precision, recall, support
            label_name = "_".join(parts[1:-1])  # Handle labels with underscores

            epochs_dict[m.epoch]["epoch"] = m.epoch
            if label_name not in epochs_dict[m.epoch]["per_label"]:
                epochs_dict[m.epoch]["per_label"][label_name] = {}
            epochs_dict[m.epoch]["per_label"][label_name][metric_type] = m.value

    # Convert to sorted list
    epochs_list = [v for v in epochs_dict.values() if v["epoch"] is not None]
    epochs_list.sort(key=lambda x: x["epoch"])

    return epochs_list


def _collapse_bio_tags(cm_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Collapse B- and I- prefixed labels into entity-level labels.

    Input: {"labels": ["B-ADDRESS", "I-ADDRESS", "O"], "matrix": [[...]]}
    Output: {"labels": ["ADDRESS", "O"], "matrix": [[...]]}
    """
    labels = cm_data.get("labels", [])
    matrix = cm_data.get("matrix", [])

    if not labels or not matrix:
        return cm_data

    # Map old indices to new entity labels
    entity_map = {}  # old_index -> entity_name
    entity_indices = {}  # entity_name -> new_index
    new_labels = []

    for i, label in enumerate(labels):
        # Strip B- or I- prefix
        if label.startswith("B-") or label.startswith("I-"):
            entity = label[2:]
        else:
            entity = label

        entity_map[i] = entity

        if entity not in entity_indices:
            entity_indices[entity] = len(new_labels)
            new_labels.append(entity)

    # Build collapsed matrix
    n_new = len(new_labels)
    new_matrix = [[0] * n_new for _ in range(n_new)]

    for i, row in enumerate(matrix):
        for j, value in enumerate(row):
            new_i = entity_indices[entity_map[i]]
            new_j = entity_indices[entity_map[j]]
            new_matrix[new_i][new_j] += value

    return {
        "labels": new_labels,
        "matrix": new_matrix,
    }


def _success_response(body: Dict[str, Any]) -> Dict[str, Any]:
    """Create a successful API response."""
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }


def _error_response(status_code: int, message: str) -> Dict[str, Any]:
    """Create an error API response."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({"error": message}),
    }
