"""
Lambda handler for job training metrics.

Returns F1 scores, confusion matrices, and per-label metrics for a training job.
"""

import json
import logging
import os
import re
from collections import Counter, defaultdict
from datetime import date
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import boto3
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import CORE_LABELS
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
SYNTHESIS_OPERATION_FAMILIES = (
    "hard_negative",
    "add_line_item",
    "remove_line_item",
    "replace_field",
)
SYNTHESIS_OPERATION_LABEL_NAMES = set(CORE_LABELS.keys()) | {"O"}
LLM_MODEL_FRESHNESS_MAX_AGE_DAYS = 30
LLM_MODEL_FRESHNESS_CHECK_DATE_ENV = "RECEIPT_AGENT_MODEL_FRESHNESS_CHECK_DATE"

# Featured job ID for the portfolio demo.
#
# `FEATURED_JOB_ID` is read from the Lambda's environment if present, but
# Pulumi does NOT currently set it for any stack (only `DYNAMODB_TABLE_NAME`
# is wired in `infra/routes/job_training_metrics/infra.py`). The os.environ
# check is kept as a forward-compatible escape hatch — wire the env var in
# Pulumi config if you want to swap the featured run without a code deploy.
# Until then, this hardcoded default is what actually serves prod requests.
#
# Current featured run: layoutlm-v14-loose-clip-may2026
#   per-receipt-window training, class-weighted CE clipped [0.5, 3.0],
#   best_f1 = 0.785 at epoch 48 of 100 (early-stopped at 63).
# To swap: copy the new run's Job + JobMetric rows from dev DDB to prod
# DDB (otherwise the handler returns 404), then update this constant.
FEATURED_JOB_ID = os.environ.get(
    "FEATURED_JOB_ID",
    "9775a6f5-80f5-435b-b711-47b69ce174bc",
)


def _resolve_newest_layoutlm_job(client: "DynamoClient") -> Optional[str]:
    """Return the job_id of the newest ``layoutlm-vNN`` run (highest version),
    so the confusion matrix tracks the SAME model the inference viz auto-picks.
    Falls back to None on any error (caller then uses FEATURED_JOB_ID)."""
    try:
        jobs, _ = client.list_jobs(limit=500)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("list_jobs failed; falling back to FEATURED_JOB_ID")
        return None

    def vnum(name: Optional[str]) -> int:
        m = re.search(r"-v(\d+)", name or "")
        return int(m.group(1)) if m else -1

    cands = [j for j in jobs if vnum(getattr(j, "name", "")) >= 0]
    if not cands:
        return None
    best = max(cands, key=lambda j: (vnum(j.name), j.created_at or ""))
    logger.info("Newest layoutlm job: %s (%s)", best.name, best.job_id)
    return best.job_id


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

        # "featured" is resolved after the client is created (below) so it can
        # track the newest run dynamically.

        # Parse query parameters
        query_params = event.get("queryStringParameters") or {}
        epoch_filter = query_params.get("epoch")
        if epoch_filter is not None:
            try:
                epoch_filter = int(epoch_filter)
            except ValueError:
                return _error_response(400, "epoch must be an integer")

        include_cm = (
            query_params.get("include_confusion_matrix", "true").lower() != "false"
        )
        collapse_bio = query_params.get("collapse_bio", "false").lower() == "true"

        # Initialize DynamoDB client
        client = DynamoClient(table_name=DYNAMODB_TABLE_NAME, region="us-east-1")

        # Resolve "featured" to the newest layoutlm run — the same model the
        # inference viz auto-selects — falling back to the configured default.
        if job_id == "featured":
            job_id = _resolve_newest_layoutlm_job(client) or FEATURED_JOB_ID
            logger.info("Resolved featured -> %s", job_id)

        # Get job metadata
        try:
            job = client.get_job(job_id)
        except ValueError:
            return _error_response(400, f"Invalid job_id format: {job_id}")
        except EntityNotFoundError:
            return _error_response(404, f"Job not found: {job_id}")

        # Get all metrics for the job (includes dataset_metrics)
        metrics_data = _fetch_all_metrics(client, job_id)

        # Get dataset-level metrics (already extracted in _fetch_all_metrics)
        dataset_metrics = metrics_data.get("dataset_metrics", {})
        synthesis_summary = _build_synthesis_summary(job, dataset_metrics)
        visual_review_summary = _build_synthetic_visual_review_summary(
            client, job_id
        )
        if visual_review_summary:
            if synthesis_summary is None:
                synthesis_summary = {
                    "status": "metrics_only",
                    "validation_policy": "real_receipts_only",
                }
            synthesis_summary["visual_review_summary"] = visual_review_summary

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
            "dataset_metrics": dataset_metrics if dataset_metrics else None,
            "synthesis": synthesis_summary,
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
    """Fetch all relevant metrics for a job, handling pagination."""
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
        all_items = []
        last_key = None
        while True:
            metrics, last_key = client.list_job_metrics(
                job_id, metric_name=name, last_evaluated_key=last_key
            )
            all_items.extend(metrics)
            if not last_key:
                break
        result[name] = all_items

    # Fetch per-label metrics (they have dynamic names like label_ADDRESS_f1)
    # Need to paginate through all metrics to find label_* entries
    all_metrics = []
    last_key = None
    while True:
        metrics, last_key = client.list_job_metrics(job_id, last_evaluated_key=last_key)
        all_metrics.extend(metrics)
        if not last_key:
            break
    label_metrics = [m for m in all_metrics if m.metric_name.startswith("label_")]
    result["label_metrics"] = label_metrics

    # Extract dataset-level metrics (epoch=None) from the same fetch
    result["dataset_metrics"] = {
        m.metric_name: m.value for m in all_metrics if m.epoch is None
    }

    return result


def _aggregate_by_epoch(
    metrics_data: Dict[str, List],
    include_confusion_matrix: bool = True,
    collapse_bio: bool = False,
    epoch_filter: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Aggregate all metrics by epoch."""
    epochs_dict: Dict[int, Dict[str, Any]] = defaultdict(
        lambda: {
            "epoch": None,
            "metrics": {},
            "per_label": {},
        }
    )

    # Process scalar metrics
    scalar_metrics = [
        "val_f1",
        "val_precision",
        "val_recall",
        "eval_loss",
        "train_loss",
        "learning_rate",
    ]
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


def _build_synthesis_summary(
    job: Any,
    dataset_metrics: Dict[str, Any] | None,
) -> Dict[str, Any] | None:
    """Build a compact synthetic-receipt evidence summary for the UI.

    Full synthetic candidates can include token arrays and boxes. This API only
    returns bounded proof that generated examples are train-only, merchant-local,
    and grounded in observed item/category/layout evidence.
    """
    synthetic_examples = _safe_int(
        (dataset_metrics or {}).get("synthetic_train_examples")
    )
    loader_summary = _synthetic_loader_summary(dataset_metrics)
    artifact_ref = _pattern_artifact_ref(job)

    if artifact_ref:
        try:
            artifacts = _load_pattern_artifacts(artifact_ref)
            if artifacts:
                summary = _summarize_synthesis_artifacts(
                    artifacts,
                    artifact_ref=artifact_ref,
                    synthetic_train_examples=synthetic_examples,
                )
                summary.update(loader_summary)
                return summary
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Could not load synthesis artifact for job %s",
                getattr(job, "job_id", None),
                exc_info=True,
            )
            return {
                "status": "artifact_unavailable",
                "synthetic_train_examples": synthetic_examples,
                "artifact_s3_uri": artifact_ref.get("s3_uri"),
                "validation_policy": "real_receipts_only",
                **loader_summary,
            }

    if synthetic_examples or loader_summary:
        return {
            "status": "metrics_only",
            "synthetic_train_examples": synthetic_examples,
            "validation_policy": "real_receipts_only",
            **loader_summary,
        }
    return None


def _visual_review_to_dict(review: Any) -> Dict[str, Any]:
    return {
        "review_id": getattr(review, "review_id", None),
        "candidate_id": getattr(review, "candidate_id", None),
        "synthetic_image_id": getattr(review, "synthetic_image_id", None),
        "status": getattr(review, "status", None),
        "reviewer": getattr(review, "reviewer", None),
        "reviewer_model": getattr(review, "reviewer_model", None),
        "created_at": getattr(review, "created_at", None),
        "merchant_name": getattr(review, "merchant_name", None),
        "operation": getattr(review, "operation", None),
        "realism_score": _safe_float(getattr(review, "realism_score", None)),
        "fidelity_score": _safe_float(getattr(review, "fidelity_score", None)),
        "alignment_score": _safe_float(getattr(review, "alignment_score", None)),
        "issue_count": _safe_int(getattr(review, "issue_count", None)),
        "findings": list(getattr(review, "findings", None) or [])[:8],
        "recommendations": [
            str(item)
            for item in (getattr(review, "recommendations", None) or [])[:8]
            if item
        ],
    }


def _compact_synthetic_visual_reviews(reviews: List[Any]) -> Dict[str, Any]:
    status_counts: Counter[str] = Counter()
    latest_by_candidate: dict[str, Any] = {}
    score_totals: Counter[str] = Counter()
    score_counts: Counter[str] = Counter()
    recommendations: list[str] = []

    for review in sorted(
        reviews,
        key=lambda item: str(getattr(item, "created_at", "") or ""),
    ):
        status = str(getattr(review, "status", "") or "unknown")
        status_counts[status] += 1
        candidate_id = str(getattr(review, "candidate_id", "") or "")
        if candidate_id:
            latest_by_candidate[candidate_id] = review
        for field in ("realism_score", "fidelity_score", "alignment_score"):
            score = _safe_float(getattr(review, field, None))
            if score is None:
                continue
            score_totals[field] += score
            score_counts[field] += 1
        for recommendation in getattr(review, "recommendations", None) or []:
            if recommendation and recommendation not in recommendations:
                recommendations.append(str(recommendation))

    latest_reviews = sorted(
        latest_by_candidate.values(),
        key=lambda item: str(getattr(item, "created_at", "") or ""),
        reverse=True,
    )
    avg_scores = {
        field: round(score_totals[field] / score_counts[field], 3)
        for field in sorted(score_totals)
        if score_counts[field]
    }
    return {
        "review_count": len(reviews),
        "reviewed_candidate_count": len(latest_by_candidate),
        "status_counts": dict(status_counts),
        "avg_scores": avg_scores,
        "latest_reviews": [
            _visual_review_to_dict(review) for review in latest_reviews[:8]
        ],
        "open_recommendations": recommendations[:8],
    }


def _build_synthetic_visual_review_summary(
    client: DynamoClient,
    job_id: str,
) -> Dict[str, Any]:
    try:
        reviews, _ = client.list_synthetic_receipt_visual_reviews_for_job(
            job_id,
            limit=100,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning(
            "Could not load synthetic visual reviews for job %s",
            job_id,
            exc_info=True,
        )
        return {}

    if not reviews:
        return {}
    return _compact_synthetic_visual_reviews(reviews)


def _synthetic_loader_summary(
    dataset_metrics: Dict[str, Any] | None,
) -> Dict[str, Any]:
    metrics = dataset_metrics or {}
    result: Dict[str, Any] = {}
    for key in (
        "synthetic_candidates_seen",
        "synthetic_candidates_accepted",
        "synthetic_candidates_rejected",
    ):
        value = _safe_int(metrics.get(key))
        if value is not None:
            result[key] = value

    reasons = metrics.get("synthetic_rejection_reasons")
    if isinstance(reasons, dict):
        compact_reasons = {
            str(key): value
            for key, raw_value in reasons.items()
            if (value := _safe_int(raw_value)) is not None and value > 0
        }
        if compact_reasons:
            result["synthetic_rejection_reasons"] = compact_reasons

    accepted_operation_counts = _compact_count_map(
        metrics.get("synthetic_accepted_operation_counts")
    )
    if accepted_operation_counts:
        result["synthetic_accepted_operation_counts"] = accepted_operation_counts
        result["accepted_operation_counts"] = accepted_operation_counts

    accepted_operation_coverage = _compact_accepted_operation_coverage(
        metrics.get("synthetic_accepted_operation_coverage")
    )
    if accepted_operation_coverage:
        result["synthetic_accepted_operation_coverage"] = accepted_operation_coverage
        result["accepted_operation_coverage"] = accepted_operation_coverage

    accepted_category_counts = _compact_count_map(
        metrics.get("synthetic_accepted_category_counts")
    )
    if accepted_category_counts:
        result["synthetic_accepted_category_counts"] = accepted_category_counts
        result["accepted_category_counts"] = accepted_category_counts

    accepted_field_replacement_counts = _compact_count_map(
        metrics.get("synthetic_accepted_field_replacement_counts")
    )
    if accepted_field_replacement_counts:
        result["synthetic_accepted_field_replacement_counts"] = (
            accepted_field_replacement_counts
        )
        result["accepted_field_replacement_counts"] = accepted_field_replacement_counts

    accepted_structure_similarity = _compact_score_summary(
        metrics.get("synthetic_accepted_structure_similarity")
    )
    if accepted_structure_similarity:
        result["synthetic_accepted_structure_similarity"] = (
            accepted_structure_similarity
        )
        result["accepted_structure_similarity"] = accepted_structure_similarity

    accepted_structure_components = _compact_component_score_summary(
        metrics.get("synthetic_accepted_structure_components")
    )
    if accepted_structure_components:
        result["synthetic_accepted_structure_components"] = (
            accepted_structure_components
        )
        result["accepted_structure_components"] = accepted_structure_components

    accepted_candidate_quality = _compact_score_summary(
        metrics.get("synthetic_accepted_candidate_quality")
    )
    if accepted_candidate_quality:
        result["synthetic_accepted_candidate_quality"] = accepted_candidate_quality
        result["accepted_candidate_quality"] = accepted_candidate_quality

    accepted_candidate_quality_components = _compact_component_score_summary(
        metrics.get("synthetic_accepted_candidate_quality_components")
    )
    if accepted_candidate_quality_components:
        result["synthetic_accepted_candidate_quality_components"] = (
            accepted_candidate_quality_components
        )
        result["accepted_candidate_quality_components"] = (
            accepted_candidate_quality_components
        )

    accepted_real_baseline = _compact_real_baseline_comparison_summary(
        metrics.get("synthetic_accepted_real_baseline_comparison")
    )
    if accepted_real_baseline:
        result["synthetic_accepted_real_baseline_comparison"] = accepted_real_baseline
        result["accepted_real_baseline_comparison"] = accepted_real_baseline

    accepted_mix_balance = _compact_mix_balance(
        metrics.get("synthetic_accepted_mix_balance")
    )
    if accepted_mix_balance:
        result["synthetic_accepted_mix_balance"] = accepted_mix_balance
        result["accepted_mix_balance"] = accepted_mix_balance

    accepted_grounded = _safe_int(metrics.get("synthetic_accepted_grounded_count"))
    if accepted_grounded is not None:
        result["synthetic_accepted_grounded_count"] = accepted_grounded
        result["accepted_grounded_candidate_count"] = accepted_grounded
        result["grounded_candidate_count"] = accepted_grounded

    accepted_arithmetic = _safe_int(metrics.get("synthetic_accepted_arithmetic_count"))
    if accepted_arithmetic is not None:
        result["synthetic_accepted_arithmetic_count"] = accepted_arithmetic
        result["accepted_arithmetic_candidate_count"] = accepted_arithmetic
        result["arithmetic_candidate_count"] = accepted_arithmetic
    return result


def _pattern_artifact_ref(job: Any) -> Dict[str, str] | None:
    tags = getattr(job, "tags", None) or {}
    config = getattr(job, "job_config", None) or {}

    s3_uri = (
        tags.get("pattern-artifact-s3-uri")
        or tags.get("line-item-patterns-s3-uri")
        or config.get("synthetic_training_examples")
        or config.get("line_item_patterns_s3_uri")
    )
    bucket = tags.get("batch-bucket") or config.get("batch_bucket")
    key = tags.get("line-item-patterns-s3-key") or config.get(
        "line_item_patterns_s3_key"
    )
    prefix = tags.get("line-item-patterns-s3-prefix") or config.get(
        "line_item_patterns_s3_prefix"
    )

    if s3_uri:
        parsed = _parse_s3_uri(str(s3_uri))
        if parsed:
            return parsed
    if bucket and key:
        return {
            "bucket": str(bucket),
            "key": str(key),
            "s3_uri": f"s3://{bucket}/{key}",
        }
    if bucket and prefix:
        prefix_value = str(prefix)
        return {
            "bucket": str(bucket),
            "prefix": prefix_value,
            "s3_uri": f"s3://{bucket}/{prefix_value}",
        }
    return None


def _parse_s3_uri(uri: str) -> Dict[str, str] | None:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        return None
    key = parsed.path.lstrip("/")
    ref = {
        "bucket": parsed.netloc,
        "s3_uri": uri,
    }
    if key.endswith("/"):
        ref["prefix"] = key
    elif key:
        ref["key"] = key
    return ref


def _load_pattern_artifacts(ref: Dict[str, str]) -> List[Dict[str, Any]]:
    bucket = ref.get("bucket")
    if not bucket:
        return []
    s3_client = boto3.client("s3")
    if ref.get("key"):
        return [_read_pattern_artifact(s3_client, bucket, ref["key"])]

    prefix = ref.get("prefix")
    if not prefix:
        return []
    artifacts: list[dict[str, Any]] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item.get("Key")
            if not key or not str(key).endswith(".json"):
                continue
            artifacts.append(_read_pattern_artifact(s3_client, bucket, key))
            if len(artifacts) >= 8:
                return artifacts
    return artifacts


def _read_pattern_artifact(
    s3_client: Any,
    bucket: str,
    key: str,
) -> Dict[str, Any]:
    response = s3_client.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read()
    return json.loads(body.decode("utf-8"))


def _summarize_synthesis_artifacts(
    artifacts: List[Dict[str, Any]],
    *,
    artifact_ref: Dict[str, str],
    synthetic_train_examples: int | None,
) -> Dict[str, Any]:
    bundle = next(
        (
            artifact
            for artifact in artifacts
            if artifact.get("schema_version") == "layoutlm-synthetic-training-bundle-v1"
        ),
        None,
    )
    if bundle:
        return _summarize_synthesis_bundle(
            bundle,
            artifact_ref=artifact_ref,
            synthetic_train_examples=synthetic_train_examples,
        )

    source_counts: Counter[str] = Counter()
    operation_counts: Counter[str] = Counter()
    field_replacement_counts: Counter[str] = Counter()
    merchants: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    profile_receipt_count = 0
    catalog_item_count = 0
    category_count = 0
    top_catalog_items: list[dict[str, Any]] = []
    similarities: list[float] = []
    structure_component_totals: Counter[str] = Counter()
    structure_component_counts: Counter[str] = Counter()
    arithmetic_update_counts: Counter[str] = Counter()
    grounded_candidate_count = 0
    arithmetic_candidate_count = 0
    non_taxable_arithmetic_candidate_count = 0
    recipe_count = 0
    candidate_count = 0
    readiness_status_counts: Counter[str] = Counter()
    readiness_scores: list[float] = []
    merchant_readiness: list[dict[str, Any]] = []

    for artifact in artifacts:
        profile = artifact.get("merchant_receipt_parameterization") or {}
        merchant = str(
            artifact.get("merchant_name")
            or profile.get("merchant_name")
            or "Unknown merchant"
        )
        merchants[merchant] += 1
        plan = artifact.get("synthetic_receipt_plan") or {}
        recipe_count += len(plan.get("recipes") or [])

        profile_receipt_count += int(profile.get("receipt_count") or 0)
        catalog = profile.get("observed_item_catalog") or []
        catalog_item_count += len(catalog)
        category_patterns = profile.get("category_patterns") or {}
        heading_counts = category_patterns.get("heading_counts") or {}
        top_by_category = category_patterns.get("top_items_by_category") or {}
        category_count += max(len(heading_counts), len(top_by_category))
        for item in catalog[:4]:
            if len(top_catalog_items) >= 6:
                break
            top_catalog_items.append(_compact_catalog_item(item))
        readiness = profile.get("synthesis_readiness") or {}
        if isinstance(readiness, dict) and readiness:
            readiness_status = str(readiness.get("status") or "unknown")
            readiness_status_counts[readiness_status] += 1
            readiness_score = _safe_float(readiness.get("score"))
            if readiness_score is not None:
                readiness_scores.append(readiness_score)
            if len(merchant_readiness) < 6:
                merchant_readiness.append(
                    _compact_readiness(readiness, merchant=merchant)
                )

        for candidate in artifact.get("synthetic_receipt_candidates") or []:
            candidate_count += 1
            metadata = candidate.get("metadata") or {}
            source = str(metadata.get("source") or "unknown")
            operation = str(metadata.get("operation") or "unknown")
            source_counts[source] += 1
            operation_counts[operation] += 1
            field_label = _field_replacement_label(candidate)
            if field_label:
                field_replacement_counts[field_label] += 1

            structure = metadata.get("structure_similarity") or {}
            score = _safe_float(structure.get("score"))
            if score is not None:
                similarities.append(score)
            for key, value in (structure.get("components") or {}).items():
                component_score = _safe_float(value)
                if component_score is None:
                    continue
                structure_component_totals[str(key)] += component_score
                structure_component_counts[str(key)] += 1

            added_item = metadata.get("added_item") or {}
            observed = metadata.get("observed_item_evidence") or {}
            if added_item.get("seen_in_other_receipt") or observed.get(
                "product_seen_outside_base"
            ):
                grounded_candidate_count += 1

            arithmetic = metadata.get("arithmetic_reconciliation") or {}
            if arithmetic:
                arithmetic_candidate_count += 1
                if arithmetic.get("tax_delta") == "0.00":
                    non_taxable_arithmetic_candidate_count += 1
                for key, value in (
                    arithmetic.get("updated_summary_labels") or {}
                ).items():
                    arithmetic_update_counts[str(key)] += _safe_int(value) or 0

            if len(examples) < 4:
                examples.append(
                    _compact_candidate_example(candidate, merchant=merchant)
                )

    summary = {
        "status": "available",
        "artifact_s3_uri": artifact_ref.get("s3_uri"),
        "synthetic_train_examples": synthetic_train_examples,
        "candidate_count": candidate_count,
        "recipe_count": recipe_count,
        "merchant_count": len(merchants),
        "merchants": list(merchants.keys())[:6],
        "source_counts": dict(source_counts),
        "operation_counts": dict(operation_counts),
        "field_replacement_counts": dict(field_replacement_counts),
        "grounded_candidate_count": grounded_candidate_count,
        "grounded_candidate_share": (
            round(grounded_candidate_count / candidate_count, 3)
            if candidate_count
            else None
        ),
        "best_structure_similarity": (
            round(max(similarities), 3) if similarities else None
        ),
        "avg_structure_similarity": (
            round(sum(similarities) / len(similarities), 3) if similarities else None
        ),
        "avg_structure_components": {
            key: round(
                structure_component_totals[key] / structure_component_counts[key],
                3,
            )
            for key in sorted(structure_component_totals)
            if structure_component_counts[key]
        },
        "arithmetic_candidate_count": arithmetic_candidate_count,
        "non_taxable_arithmetic_candidate_count": (
            non_taxable_arithmetic_candidate_count
        ),
        "arithmetic_update_counts": dict(arithmetic_update_counts),
        "profile_receipt_count": profile_receipt_count,
        "catalog_item_count": catalog_item_count,
        "category_count": category_count,
        "readiness_status_counts": dict(readiness_status_counts),
        "ready_merchant_count": readiness_status_counts.get("ready", 0),
        "avg_readiness_score": (
            round(sum(readiness_scores) / len(readiness_scores), 3)
            if readiness_scores
            else None
        ),
        "merchant_readiness": merchant_readiness,
        "top_catalog_items": top_catalog_items,
        "candidate_examples": examples,
        "validation_policy": "real_receipts_only",
    }
    source_receipt_quality = _summarize_source_receipt_quality_from_artifacts(artifacts)
    if source_receipt_quality:
        summary["source_receipt_quality"] = source_receipt_quality
    return summary


def _summarize_synthesis_bundle(
    bundle: Dict[str, Any],
    *,
    artifact_ref: Dict[str, str],
    synthetic_train_examples: int | None,
) -> Dict[str, Any]:
    candidate_mix = bundle.get("candidate_mix") or {}
    selection = bundle.get("selection") or {}
    examples = bundle.get("synthetic_training_examples") or []
    candidate_count = _safe_int(
        candidate_mix.get("candidate_count")
        or selection.get("candidates_seen")
        or len(examples)
    )
    accepted_count = _safe_int(
        candidate_mix.get("accepted_count")
        or selection.get("candidates_accepted")
        or len(examples)
    )
    synthetic_examples = synthetic_train_examples
    if synthetic_examples is None:
        synthetic_examples = accepted_count

    merchants = [
        str(row.get("merchant_name"))
        for row in candidate_mix.get("merchants") or []
        if row.get("merchant_name")
    ]
    accepted_grounded = _safe_int(
        candidate_mix.get("accepted_grounded_candidate_count")
    )
    accepted_arithmetic = _safe_int(
        candidate_mix.get("accepted_arithmetic_candidate_count")
    )
    contracts = [
        contract
        for contract in bundle.get("merchant_synthesis_contracts") or []
        if isinstance(contract, dict)
    ]
    contract_merchant_count = len(contracts) if contracts else None
    contract_ready_merchant_count = (
        sum(1 for contract in contracts if contract.get("status") == "ready")
        if contracts
        else None
    )
    candidate_examples = [
        _compact_candidate_example(
            candidate,
            merchant=str(candidate.get("merchant_name") or "Unknown merchant"),
        )
        for candidate in examples[:4]
        if isinstance(candidate, dict)
    ]
    accepted_source_lineage = _compact_source_lineage_summary(
        candidate_mix.get("accepted_source_lineage")
    )
    if not accepted_source_lineage:
        accepted_source_lineage = _accepted_source_lineage_from_candidates(
            examples,
            expected_candidate_count=accepted_count,
        )

    summary = {
        "status": "available",
        "artifact_s3_uri": artifact_ref.get("s3_uri"),
        "artifact_schema_version": bundle.get("schema_version"),
        "synthetic_train_examples": synthetic_examples,
        "candidate_count": candidate_count,
        "rejected_count": _safe_int(candidate_mix.get("rejected_count")),
        "rejection_reasons": _compact_count_map(candidate_mix.get("rejection_reasons")),
        "merchant_count": _safe_int(candidate_mix.get("merchant_count")),
        "accepted_merchant_count": _safe_int(
            candidate_mix.get("accepted_merchant_count")
        ),
        "merchants": merchants[:6],
        "operation_counts": _compact_count_map(candidate_mix.get("operation_counts")),
        "accepted_operation_counts": _compact_count_map(
            candidate_mix.get("accepted_operation_counts")
        ),
        "accepted_field_replacement_counts": _compact_count_map(
            candidate_mix.get("accepted_field_replacement_counts")
        )
        or _summarize_field_replacements(examples),
        "category_counts": _compact_count_map(candidate_mix.get("category_counts")),
        "accepted_category_counts": _compact_count_map(
            candidate_mix.get("accepted_category_counts")
        ),
        "accepted_structure_similarity": _compact_score_summary(
            candidate_mix.get("accepted_structure_similarity")
        ),
        "accepted_grounded_candidate_count": accepted_grounded,
        "accepted_arithmetic_candidate_count": accepted_arithmetic,
        "grounded_candidate_count": accepted_grounded,
        "arithmetic_candidate_count": accepted_arithmetic,
        "contract_merchant_count": contract_merchant_count,
        "contract_ready_merchant_count": contract_ready_merchant_count,
        "contract_operation_counts": _summarize_contract_operations(contracts),
        "contract_field_replacement_counts": _summarize_contract_fields(contracts),
        "merchant_synthesis_contracts": _compact_merchant_synthesis_contracts(
            contracts
        ),
        "candidate_mix_merchants": _compact_candidate_mix_merchants(
            candidate_mix.get("merchants") or []
        ),
        "candidate_examples": candidate_examples,
        "validation_policy": bundle.get("validation_policy") or "real_receipts_only",
    }
    source_receipt_quality = _compact_source_receipt_quality(
        bundle.get("source_receipt_quality")
    )
    if source_receipt_quality:
        summary["source_receipt_quality"] = source_receipt_quality
    accepted_components = _compact_component_score_summary(
        candidate_mix.get("accepted_structure_components")
    )
    if accepted_components:
        summary["accepted_structure_components"] = accepted_components
    accepted_mix_balance = _compact_mix_balance(
        candidate_mix.get("accepted_mix_balance")
    )
    if accepted_mix_balance:
        summary["accepted_mix_balance"] = accepted_mix_balance
    if accepted_source_lineage:
        summary["accepted_source_lineage"] = accepted_source_lineage
    accepted_candidate_quality = _compact_score_summary(
        candidate_mix.get("accepted_candidate_quality")
    )
    if accepted_candidate_quality:
        summary["accepted_candidate_quality"] = accepted_candidate_quality
    accepted_quality_components = _compact_component_score_summary(
        candidate_mix.get("accepted_candidate_quality_components")
    )
    if accepted_quality_components:
        summary["accepted_candidate_quality_components"] = accepted_quality_components
    accepted_real_baseline = _compact_real_baseline_comparison_summary(
        candidate_mix.get("accepted_real_baseline_comparison")
    )
    if accepted_real_baseline:
        summary["accepted_real_baseline_comparison"] = accepted_real_baseline
    preflight = (
        bundle.get("preflight") if isinstance(bundle.get("preflight"), dict) else {}
    )
    llm_execution = _compact_llm_execution_summary(
        preflight.get("llm_execution") if isinstance(preflight, dict) else None
    )
    if llm_execution:
        summary["llm_execution"] = llm_execution
    quality_report = _compact_synthesis_quality_report(
        bundle.get("synthesis_quality_report")
        or bundle.get("quality_report")
        or bundle.get("report")
    )
    if not quality_report:
        quality_report = _derive_synthesis_quality_report(
            bundle=bundle,
            candidate_mix=candidate_mix,
            selection=selection,
            contracts=contracts,
            candidate_examples=summary["candidate_examples"],
        )
    if quality_report:
        summary["quality_report"] = quality_report
    if candidate_count:
        summary["grounded_candidate_share"] = (
            round(accepted_grounded / candidate_count, 3)
            if accepted_grounded is not None
            else None
        )
    if isinstance(selection, dict):
        for key in (
            "candidates_seen",
            "candidates_accepted",
            "candidates_rejected",
            "rejection_reasons",
            "max_per_merchant",
            "max_per_merchant_operation",
            "min_structure_similarity",
        ):
            if key in selection:
                summary[f"bundle_{key}"] = selection[key]
    return summary


def _compact_count_map(value: Any) -> Dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): count
        for key, raw_value in value.items()
        if (count := _safe_int(raw_value)) is not None and count > 0
    }


def _count_values(values: Any) -> Dict[str, int]:
    counts: Counter[str] = Counter()
    for value in values:
        if value:
            counts[str(value)] += 1
    return dict(counts)


def _next_synthesis_action_counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    return _count_values(
        action
        for row in rows
        for action in row.get("next_synthesis_actions") or []
        if action
    )


def _source_key_values(value: Any) -> List[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        values: list[str] = []
        for item in value:
            values.extend(_source_key_values(item))
        return values
    return []


def _unique_source_keys(*values: Any) -> List[str]:
    keys: set[str] = set()
    for value in values:
        keys.update(_source_key_values(value))
    return sorted(keys)


def _compact_source_lineage(
    value: Any,
    *,
    redact_source_keys: bool = True,
) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    flags = value.get("evidence_flags")
    flags = flags if isinstance(flags, dict) else {}
    source_keys = _unique_source_keys(value.get("source_receipt_keys"))
    product_keys = _unique_source_keys(value.get("product_source_receipt_keys"))
    category_keys = _unique_source_keys(value.get("category_source_receipt_keys"))
    compact_flags = {
        str(key): flag for key, flag in flags.items() if isinstance(flag, bool)
    }
    if not source_keys and not any(compact_flags.values()):
        return {}
    result = {
        "schema_version": value.get("schema_version"),
        "source_receipt_key_count": _safe_int(value.get("source_receipt_key_count"))
        or len(source_keys),
        "product_source_receipt_key_count": _safe_int(
            value.get("product_source_receipt_key_count")
        )
        or len(product_keys),
        "category_source_receipt_key_count": _safe_int(
            value.get("category_source_receipt_key_count")
        )
        or len(category_keys),
        "evidence_flags": compact_flags,
    }
    if redact_source_keys:
        if (
            source_keys
            or product_keys
            or category_keys
            or value.get("source_receipt_keys_redacted") is True
            or value.get("base_receipt_key")
            or value.get("nearest_real_receipt_key")
        ):
            result["source_receipt_keys_redacted"] = True
    else:
        result.update(
            {
                "base_receipt_key": value.get("base_receipt_key"),
                "nearest_real_receipt_key": value.get("nearest_real_receipt_key"),
                "source_receipt_keys": source_keys[:12],
                "product_source_receipt_keys": product_keys[:8],
                "category_source_receipt_keys": category_keys[:8],
            }
        )
    return {key: item for key, item in result.items() if item not in (None, "", [], {})}


def _compact_source_lineage_summary(
    value: Any,
    *,
    redact_source_keys: bool = True,
) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if not value:
        return {}
    source_keys = _unique_source_keys(value.get("source_receipt_keys"))
    result = {
        "schema_version": value.get("schema_version"),
        "coverage_status": value.get("coverage_status"),
        "authoritative": (
            value.get("authoritative")
            if isinstance(value.get("authoritative"), bool)
            else None
        ),
        "coverage_warning": value.get("coverage_warning"),
        "candidate_count": _safe_int(value.get("candidate_count")),
        "observed_candidate_count": _safe_int(value.get("observed_candidate_count")),
        "expected_candidate_count": _safe_int(value.get("expected_candidate_count")),
        "with_base_receipt_count": _safe_int(value.get("with_base_receipt_count")),
        "with_cross_receipt_item_count": _safe_int(
            value.get("with_cross_receipt_item_count")
        ),
        "with_category_evidence_count": _safe_int(
            value.get("with_category_evidence_count")
        ),
        "with_nearest_real_structure_count": _safe_int(
            value.get("with_nearest_real_structure_count")
        ),
        "with_layout_integrity_count": _safe_int(
            value.get("with_layout_integrity_count")
        ),
        "with_arithmetic_reconciliation_count": _safe_int(
            value.get("with_arithmetic_reconciliation_count")
        ),
        "with_selection_evidence_count": _safe_int(
            value.get("with_selection_evidence_count")
        ),
        "source_receipt_key_count": _safe_int(value.get("source_receipt_key_count"))
        or len(source_keys),
        "source_receipt_keys_truncated": (
            value.get("source_receipt_keys_truncated")
            if isinstance(value.get("source_receipt_keys_truncated"), bool)
            else len(source_keys) > 20
        ),
    }
    if redact_source_keys:
        if source_keys or result.get("source_receipt_key_count"):
            result["source_receipt_keys_redacted"] = True
    else:
        result["source_receipt_keys"] = source_keys[:20]
    return {key: item for key, item in result.items() if item not in (None, "", [], {})}


def _candidate_source_lineage(
    candidate: Dict[str, Any],
    *,
    redact_source_keys: bool = True,
) -> Dict[str, Any]:
    metadata = candidate.get("metadata") if isinstance(candidate, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    accuracy = metadata.get("synthesis_accuracy_evidence")
    accuracy = accuracy if isinstance(accuracy, dict) else {}
    structure = metadata.get("structure_similarity")
    structure = structure if isinstance(structure, dict) else {}
    accuracy_structure = accuracy.get("structure_similarity")
    if isinstance(accuracy_structure, dict):
        if not structure:
            structure = accuracy_structure
        elif not structure.get("nearest_real_receipt_key"):
            structure = {**accuracy_structure, **structure}
    catalog = accuracy.get("catalog_grounding")
    catalog = catalog if isinstance(catalog, dict) else {}
    observed = metadata.get("observed_item_evidence")
    observed = observed if isinstance(observed, dict) else {}
    added = metadata.get("added_item")
    added = added if isinstance(added, dict) else {}
    layout = metadata.get("layout_integrity") or accuracy.get("layout_integrity")
    layout = layout if isinstance(layout, dict) else {}
    arithmetic = metadata.get("arithmetic_reconciliation")
    arithmetic = arithmetic if isinstance(arithmetic, dict) else {}
    selection = metadata.get("selection_evidence")

    base_key = str(metadata.get("base_receipt_key") or "").strip()
    nearest_key = str(structure.get("nearest_real_receipt_key") or "").strip()
    product_keys = _unique_source_keys(
        added.get("source_receipt_keys"),
        observed.get("product_seen_outside_base"),
        catalog.get("product_seen_outside_base"),
    )
    category_keys = _unique_source_keys(
        observed.get("category_seen_in_receipts"),
        catalog.get("category_seen_in_receipts"),
    )
    source_keys = _unique_source_keys(
        base_key, nearest_key, product_keys, category_keys
    )
    flags = {
        "has_base_receipt": bool(base_key),
        "has_cross_receipt_item": (
            any(key != base_key for key in product_keys)
            if base_key
            else bool(product_keys)
        ),
        "has_category_evidence": bool(category_keys),
        "has_nearest_real_structure": bool(nearest_key),
        "has_layout_integrity": bool(layout),
        "has_arithmetic_reconciliation": bool(arithmetic),
        "has_selection_evidence": isinstance(selection, dict),
    }
    if not source_keys and not any(flags.values()):
        return {}
    return _compact_source_lineage(
        {
            "schema_version": "synthetic-candidate-lineage-v1",
            "base_receipt_key": base_key or None,
            "nearest_real_receipt_key": nearest_key or None,
            "source_receipt_key_count": len(source_keys),
            "source_receipt_keys": source_keys,
            "product_source_receipt_keys": product_keys,
            "category_source_receipt_keys": category_keys,
            "evidence_flags": flags,
        },
        redact_source_keys=redact_source_keys,
    )


def _accepted_source_lineage_from_candidates(
    candidates: Any,
    *,
    expected_candidate_count: int | None = None,
) -> Dict[str, Any]:
    if not isinstance(candidates, list) or not candidates:
        return {}
    candidate_rows = [
        candidate for candidate in candidates if isinstance(candidate, dict)
    ]
    if not candidate_rows:
        return {}
    lineages: list[Dict[str, Any]] = []
    for candidate in candidate_rows:
        lineage = _compact_source_lineage(
            candidate.get("source_lineage"),
            redact_source_keys=False,
        )
        if not lineage:
            lineage = _candidate_source_lineage(
                candidate,
                redact_source_keys=False,
            )
        lineages.append(lineage)
    meaningful_lineages = [lineage for lineage in lineages if lineage]
    if not meaningful_lineages:
        return {}
    flags = [
        lineage.get("evidence_flags") or {}
        for lineage in meaningful_lineages
        if isinstance(lineage, dict)
    ]
    source_keys = _unique_source_keys(
        *[lineage.get("source_receipt_keys") or [] for lineage in meaningful_lineages]
    )
    lineage_source_counts = [
        _safe_int(lineage.get("source_receipt_key_count")) or 0
        for lineage in meaningful_lineages
    ]
    source_key_count = (
        len(source_keys) if source_keys else max(lineage_source_counts, default=0)
    )

    def count_flag(name: str) -> int:
        return sum(1 for row in flags if row.get(name) is True)

    expected_count = expected_candidate_count or len(candidate_rows)
    observed_count = len(candidate_rows)
    authoritative = observed_count == expected_count
    return _compact_source_lineage_summary(
        {
            "schema_version": "accepted-source-lineage-v1",
            "coverage_status": ("complete" if authoritative else "sampled"),
            "authoritative": authoritative,
            "coverage_warning": (
                None if authoritative else "sampled_source_lineage_not_authoritative"
            ),
            "candidate_count": observed_count,
            "observed_candidate_count": observed_count,
            "expected_candidate_count": expected_count,
            "with_base_receipt_count": count_flag("has_base_receipt"),
            "with_cross_receipt_item_count": count_flag("has_cross_receipt_item"),
            "with_category_evidence_count": count_flag("has_category_evidence"),
            "with_nearest_real_structure_count": count_flag(
                "has_nearest_real_structure"
            ),
            "with_layout_integrity_count": count_flag("has_layout_integrity"),
            "with_arithmetic_reconciliation_count": count_flag(
                "has_arithmetic_reconciliation"
            ),
            "with_selection_evidence_count": count_flag("has_selection_evidence"),
            "source_receipt_key_count": source_key_count,
            "source_receipt_keys": source_keys[:20],
            "source_receipt_keys_truncated": len(source_keys) > 20,
        }
    )


def _accepted_source_lineage_incomplete(
    accepted_source_lineage: Dict[str, Any],
    *,
    accepted_count: int | None,
) -> bool:
    """Fail closed when accepted rows cannot all prove base receipt lineage."""
    accepted_count = accepted_count or 0
    if accepted_count <= 0:
        return False
    if not isinstance(accepted_source_lineage, dict) or not accepted_source_lineage:
        return True
    if accepted_source_lineage.get("authoritative") is False:
        return True
    # Candidate/observed counts can be bounded diagnostics; base coverage is
    # the full-population training invariant enforced here.
    with_base_count = _safe_int(accepted_source_lineage.get("with_base_receipt_count"))
    if with_base_count is None or with_base_count < accepted_count:
        return True
    return False


def _compact_score_summary(value: Any) -> Dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: Dict[str, Any] = {}
    count = _safe_int(value.get("count"))
    if count is not None:
        result["count"] = count
    for key in ("avg", "min", "max"):
        score = _safe_float(value.get(key))
        if score is not None:
            result[key] = score
    return result or None


def _compact_mix_balance(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = {
        "accepted_count": _safe_int(value.get("accepted_count")),
        "merchant_count": _safe_int(value.get("merchant_count")),
        "operation_count": _safe_int(value.get("operation_count")),
        "top_merchant": value.get("top_merchant"),
        "top_merchant_count": _safe_int(value.get("top_merchant_count")),
        "top_merchant_share": _safe_float(value.get("top_merchant_share")),
        "top_operation": value.get("top_operation"),
        "top_operation_count": _safe_int(value.get("top_operation_count")),
        "top_operation_share": _safe_float(value.get("top_operation_share")),
        "merchant_entropy": _safe_float(value.get("merchant_entropy")),
        "operation_entropy": _safe_float(value.get("operation_entropy")),
        "risk_level": value.get("risk_level"),
        "risk_reasons": [
            str(item) for item in (value.get("risk_reasons") or [])[:8] if item
        ],
    }
    return {key: item for key, item in result.items() if item not in (None, "", [], {})}


def _compact_real_baseline_comparison_summary(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: Dict[str, Any] = {
        "count": _safe_int(value.get("count")),
        "within_real_score_range_count": _safe_int(
            value.get("within_real_score_range_count")
        ),
        "below_real_score_range_count": _safe_int(
            value.get("below_real_score_range_count")
        ),
        "within_real_score_range_share": _safe_float(
            value.get("within_real_score_range_share")
        ),
    }
    for key in (
        "candidate_score",
        "baseline_avg",
        "baseline_min",
        "baseline_pair_count",
        "delta_from_avg",
        "delta_from_min",
    ):
        compact = _compact_score_summary(value.get(key))
        if compact:
            result[key] = compact
    return {key: item for key, item in result.items() if item not in (None, "", [], {})}


def _compact_component_score_summary(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: Dict[str, Any] = {}
    for name, summary in value.items():
        compact = _compact_score_summary(summary)
        if compact:
            result[str(name)] = compact
    return result


def _compact_llm_execution_summary(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: Dict[str, Any] = {
        "mode_counts": _compact_count_map(value.get("mode_counts")),
        "paid_llm_disabled_count": _safe_int(value.get("paid_llm_disabled_count")),
        "api_call_allowed_count": _safe_int(value.get("api_call_allowed_count")),
        "configured_models": [
            str(item) for item in (value.get("configured_models") or [])[:5] if item
        ],
        "latest_openai_models": [
            str(item) for item in (value.get("latest_openai_models") or [])[:3] if item
        ],
        "latest_model_sources": [
            str(item) for item in (value.get("latest_model_sources") or [])[:3] if item
        ],
        "latest_model_verified_at": value.get("latest_model_verified_at"),
    }
    return {key: item for key, item in result.items() if item not in (None, "", [], {})}


def _model_freshness_check_date() -> date:
    raw_value = os.environ.get(LLM_MODEL_FRESHNESS_CHECK_DATE_ENV)
    if raw_value:
        try:
            return date.fromisoformat(raw_value)
        except ValueError:
            pass
    return date.today()


def _llm_model_freshness_gate(llm_execution: Any) -> Dict[str, Any]:
    if not isinstance(llm_execution, dict):
        llm_execution = {}
    verified_at = str(llm_execution.get("latest_model_verified_at") or "")
    latest_sources = [
        str(item) for item in (llm_execution.get("latest_model_sources") or []) if item
    ][:3]
    latest_openai_models = [
        str(item) for item in (llm_execution.get("latest_openai_models") or []) if item
    ][:3]
    api_call_allowed_count = _safe_int(llm_execution.get("api_call_allowed_count")) or 0
    mode_counts = llm_execution.get("mode_counts")
    llm_assisted_mode_count = (
        sum(
            count
            for mode, raw_count in mode_counts.items()
            if str(mode) not in {"deterministic_fallback", "unknown"}
            and (count := (_safe_int(raw_count) or 0)) > 0
        )
        if isinstance(mode_counts, dict)
        else 0
    )
    requires_current_guidance = (
        api_call_allowed_count > 0 or llm_assisted_mode_count > 0
    )
    parsed_date: date | None = None
    if verified_at:
        try:
            parsed_date = date.fromisoformat(verified_at)
        except ValueError:
            parsed_date = None
    raw_age_days = (
        max(0, (_model_freshness_check_date() - parsed_date).days)
        if parsed_date
        else None
    )
    fresh = (
        raw_age_days is not None and raw_age_days <= LLM_MODEL_FRESHNESS_MAX_AGE_DAYS
    )
    passed = (fresh and bool(latest_sources)) or not requires_current_guidance
    result = {
        "enabled": True,
        "passed": passed,
        "requires_current_model_guidance": requires_current_guidance,
        "api_call_allowed_count": api_call_allowed_count,
        "llm_assisted_mode_count": (
            llm_assisted_mode_count if llm_assisted_mode_count else None
        ),
        "latest_model_verified_at": verified_at or None,
        "latest_model_age_days": (raw_age_days if requires_current_guidance else None),
        "max_age_days": LLM_MODEL_FRESHNESS_MAX_AGE_DAYS,
        "latest_openai_models": latest_openai_models,
        "latest_model_sources": latest_sources,
        "reason": None if passed else "latest_model_guidance_stale_or_missing",
    }
    return {key: item for key, item in result.items() if item not in (None, "", [])}


def _compact_source_receipt_quality_merchant(row: Any) -> Dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    result = {
        "merchant_name": row.get("merchant_name"),
        "status": row.get("status"),
        "receipt_count": _safe_int(row.get("receipt_count")),
        "receipts_with_lines": _safe_int(row.get("receipts_with_lines")),
        "receipts_with_words": _safe_int(row.get("receipts_with_words")),
        "receipts_with_labels": _safe_int(row.get("receipts_with_labels")),
        "receipts_with_merchant_name_label": _safe_int(
            row.get("receipts_with_merchant_name_label")
        ),
        "receipts_with_line_item_labels": _safe_int(
            row.get("receipts_with_line_item_labels")
        ),
        "receipts_with_grand_total_label": _safe_int(
            row.get("receipts_with_grand_total_label")
        ),
        "receipts_with_date_or_time_label": _safe_int(
            row.get("receipts_with_date_or_time_label")
        ),
        "receipts_with_line_item_like_text": _safe_int(
            row.get("receipts_with_line_item_like_text")
        ),
        "receipts_with_total_like_text": _safe_int(
            row.get("receipts_with_total_like_text")
        ),
        "receipts_with_date_time_like_text": _safe_int(
            row.get("receipts_with_date_time_like_text")
        ),
        "receipts_with_merchant_header_like_text": _safe_int(
            row.get("receipts_with_merchant_header_like_text")
        ),
        "line_count": _safe_int(row.get("line_count")),
        "word_count": _safe_int(row.get("word_count")),
        "labeled_word_count": _safe_int(row.get("labeled_word_count")),
        "text_structure_status": row.get("text_structure_status"),
        "merchant_header_like_line_count": _safe_int(
            row.get("merchant_header_like_line_count")
        ),
        "price_like_line_count": _safe_int(row.get("price_like_line_count")),
        "line_item_like_text_line_count": _safe_int(
            row.get("line_item_like_text_line_count")
        ),
        "total_like_text_line_count": _safe_int(row.get("total_like_text_line_count")),
        "date_time_like_text_line_count": _safe_int(
            row.get("date_time_like_text_line_count")
        ),
        "top_labels": _compact_count_map(row.get("top_labels")),
        "blockers": list(row.get("blockers") or [])[:5],
        "limitations": _source_quality_limitations(row, limit=5),
    }
    return {
        key: value for key, value in result.items() if value not in (None, "", [], {})
    }


def _summarize_source_receipt_quality_rows(rows: List[Any]) -> Dict[str, Any]:
    merchants = [
        row
        for row in (_compact_source_receipt_quality_merchant(item) for item in rows)
        if row
    ]
    if not merchants:
        return {}
    status_counts = Counter(str(row.get("status") or "missing") for row in merchants)
    result = {
        "merchant_count": len(merchants),
        "usable_merchant_count": status_counts.get("usable", 0),
        "limited_merchant_count": status_counts.get("limited", 0),
        "blocked_merchant_count": status_counts.get("blocked", 0),
        "status_counts": dict(status_counts),
        "receipt_count": sum(
            _safe_int(row.get("receipt_count")) or 0 for row in merchants
        ),
        "labeled_word_count": sum(
            _safe_int(row.get("labeled_word_count")) or 0 for row in merchants
        ),
        "merchants": merchants[:8],
    }
    if any(row.get("text_structure_status") for row in merchants):
        text_structure_status_counts = Counter(
            str(row.get("text_structure_status") or "missing") for row in merchants
        )
        result["text_structure_status_counts"] = dict(text_structure_status_counts)
        result["line_item_like_text_line_count"] = sum(
            _safe_int(row.get("line_item_like_text_line_count")) or 0
            for row in merchants
        )
        result["total_like_text_line_count"] = sum(
            _safe_int(row.get("total_like_text_line_count")) or 0 for row in merchants
        )
    return result


def _compact_source_receipt_quality(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    raw_merchants = value.get("merchants")
    if isinstance(raw_merchants, list):
        summary = _summarize_source_receipt_quality_rows(raw_merchants)
        if not summary:
            return {}
        status_counts = _compact_count_map(value.get("status_counts"))
        if status_counts:
            summary["status_counts"] = status_counts
        for key in (
            "merchant_count",
            "usable_merchant_count",
            "limited_merchant_count",
            "blocked_merchant_count",
            "receipt_count",
            "labeled_word_count",
            "line_item_like_text_line_count",
            "total_like_text_line_count",
        ):
            compact_value = _safe_int(value.get(key))
            if compact_value is not None:
                summary[key] = compact_value
        text_structure_status_counts = _compact_count_map(
            value.get("text_structure_status_counts")
        )
        if text_structure_status_counts:
            summary["text_structure_status_counts"] = text_structure_status_counts
        return summary

    if value.get("merchant_name"):
        return _summarize_source_receipt_quality_rows([value])
    return {}


def _source_quality_operation_blocker(
    source_quality: Dict[str, Any],
    operation: str,
) -> Optional[str]:
    if not isinstance(source_quality, dict) or not source_quality:
        return None
    status = str(source_quality.get("status") or "").strip().lower()
    if status == "blocked":
        return "source_receipt_quality_blocked"

    limitations = {
        str(item)
        for item in source_quality.get("limitations") or []
        if str(item).strip()
    }
    line_item_receipts = _safe_int(source_quality.get("receipts_with_line_item_labels"))
    grand_total_receipts = _safe_int(
        source_quality.get("receipts_with_grand_total_label")
    )
    date_time_receipts = _safe_int(
        source_quality.get("receipts_with_date_or_time_label")
    )
    receipt_count = _safe_int(source_quality.get("receipt_count")) or 0

    if operation in {"add_line_item", "remove_line_item"}:
        if "no_labeled_line_items" in limitations or line_item_receipts == 0:
            return "source_quality_no_labeled_line_items"
        if "no_grand_total_labels" in limitations or grand_total_receipts == 0:
            return "source_quality_no_grand_total_labels"
    if operation == "add_line_item" and (
        "single_receipt_limits_cross_receipt_grounding" in limitations
        or receipt_count < 2
    ):
        return "source_quality_single_receipt_grounding"
    if operation == "replace_field" and date_time_receipts == 0:
        return "source_quality_no_date_time_labels"
    return None


def _source_quality_operation_blockers(
    source_quality: Dict[str, Any],
) -> Dict[str, str]:
    return {
        operation: blocker
        for operation in SYNTHESIS_OPERATION_FAMILIES
        if (blocker := _source_quality_operation_blocker(source_quality, operation))
    }


def _source_quality_operation_blockers_from_contract(
    contract: Dict[str, Any],
) -> Dict[str, str]:
    operations = contract.get("operation_contracts") or {}
    if not isinstance(operations, dict):
        return {}
    return {
        str(operation): str(operation_contract.get("source_quality_blocker"))
        for operation, operation_contract in operations.items()
        if isinstance(operation_contract, dict)
        and operation_contract.get("source_quality_blocker")
    }


def _source_quality_limitations(
    source_quality: Dict[str, Any],
    *,
    limit: int = 8,
) -> List[str]:
    limitations = [
        str(item) for item in source_quality.get("limitations") or [] if item
    ]
    if "unlabeled_text_requires_label_validation" in limitations:
        limitations.insert(0, "unlabeled_text_requires_label_validation")
    if str(source_quality.get("status") or "").strip().lower() == "limited":
        limitations.insert(0, "source_receipt_quality_limited")
    return list(dict.fromkeys(limitations))[:limit]


def _source_quality_requires_label_validation(source_quality: Dict[str, Any]) -> bool:
    if not isinstance(source_quality, dict) or not source_quality:
        return False
    if source_quality.get("text_structure_status") == "recoverable_unlabeled_text":
        return True
    return "unlabeled_text_requires_label_validation" in set(
        source_quality.get("limitations") or []
    )


def _derive_next_synthesis_actions(
    *,
    readiness_status: Any,
    source_quality_requires_label_validation: bool,
    ready_operations: Any = None,
    accepted_operation_counts: Any = None,
) -> List[str]:
    actions: list[str] = []
    if source_quality_requires_label_validation:
        actions.append("validate_recoverable_unlabeled_receipts")
    if str(readiness_status or "").strip().lower() == "blocked":
        actions.append("resolve_merchant_synthesis_blockers")
        return actions[:8]

    ready_operation_set = {str(operation) for operation in ready_operations or []}
    accepted_counts = (
        accepted_operation_counts if isinstance(accepted_operation_counts, dict) else {}
    )
    for operation in SYNTHESIS_OPERATION_FAMILIES:
        if operation in ready_operation_set and _safe_int(
            accepted_counts.get(operation)
        ):
            actions.append(f"synthesize_{operation}_from_existing_evidence")
        elif operation in ready_operation_set:
            actions.append(f"generate_{operation}_candidate_from_ready_contract")
        elif operation == "hard_negative":
            actions.append("mine_confusion_targets_for_hard_negative_slots")
        elif operation == "add_line_item":
            actions.append("collect_cross_receipt_item_and_category_evidence")
        elif operation == "remove_line_item":
            actions.append("collect_multi_item_non_taxable_receipts_with_totals")
        elif operation == "replace_field":
            actions.append("collect_stable_date_time_examples_for_field_replacement")
    return actions[:8]


def _report_source_quality_fields(
    source_quality: Dict[str, Any],
    *,
    operation_blockers: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    if not isinstance(source_quality, dict) or not source_quality:
        return {}
    blockers = operation_blockers or _source_quality_operation_blockers(source_quality)
    result = {
        "source_quality_status": source_quality.get("status"),
        "source_quality_receipt_count": _safe_int(source_quality.get("receipt_count")),
        "source_quality_labeled_word_count": _safe_int(
            source_quality.get("labeled_word_count")
        ),
        "source_quality_receipts_with_line_item_labels": _safe_int(
            source_quality.get("receipts_with_line_item_labels")
        ),
        "source_quality_receipts_with_grand_total_label": _safe_int(
            source_quality.get("receipts_with_grand_total_label")
        ),
        "source_quality_receipts_with_date_or_time_label": _safe_int(
            source_quality.get("receipts_with_date_or_time_label")
        ),
        "source_quality_text_structure_status": source_quality.get(
            "text_structure_status"
        ),
        "source_quality_line_item_like_text_line_count": _safe_int(
            source_quality.get("line_item_like_text_line_count")
        ),
        "source_quality_total_like_text_line_count": _safe_int(
            source_quality.get("total_like_text_line_count")
        ),
        "source_quality_limitations": _source_quality_limitations(source_quality),
        "source_quality_requires_label_validation": (
            True if _source_quality_requires_label_validation(source_quality) else None
        ),
        "source_quality_operation_blockers": {
            str(operation): str(reason)
            for operation, reason in blockers.items()
            if reason
        },
    }
    return {
        key: value for key, value in result.items() if value not in (None, "", [], {})
    }


def _source_quality_by_merchant(value: Any) -> Dict[str, Dict[str, Any]]:
    summary = _compact_source_receipt_quality(value)
    return {
        str(row.get("merchant_name")): row
        for row in summary.get("merchants", [])
        if isinstance(row, dict) and row.get("merchant_name")
    }


def _summarize_source_receipt_quality_from_artifacts(
    artifacts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return _summarize_source_receipt_quality_rows(
        [
            artifact.get("source_receipt_quality")
            for artifact in artifacts
            if isinstance(artifact.get("source_receipt_quality"), dict)
        ]
    )


def _summarize_contract_operations(
    contracts: List[Dict[str, Any]],
) -> Dict[str, int]:
    counts: Counter[str] = Counter()
    for contract in contracts:
        operation_contracts = contract.get("operation_contracts") or {}
        if not isinstance(operation_contracts, dict):
            continue
        for operation, evidence in operation_contracts.items():
            if isinstance(evidence, dict) and evidence.get("ready") is True:
                counts[str(operation)] += 1
    return dict(counts)


def _summarize_contract_fields(
    contracts: List[Dict[str, Any]],
) -> Dict[str, int]:
    counts: Counter[str] = Counter()
    for contract in contracts:
        operation_contracts = contract.get("operation_contracts") or {}
        if not isinstance(operation_contracts, dict):
            continue
        replace_contract = operation_contracts.get("replace_field") or {}
        fields = (
            replace_contract.get("fields") if isinstance(replace_contract, dict) else {}
        )
        if not isinstance(fields, dict):
            continue
        for label, evidence in fields.items():
            if isinstance(evidence, dict) and evidence.get("safe_to_mutate") is True:
                counts[str(label)] += 1
    return dict(counts)


def _safe_ratio(numerator: Any, denominator: Any) -> float | None:
    top = _safe_int(numerator)
    bottom = _safe_int(denominator)
    if bottom is None:
        return None
    if bottom <= 0:
        return 0.0
    return round((top or 0) / bottom, 3)


def _compact_operation_coverage(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    operations = value.get("operations")
    if not isinstance(operations, dict):
        return {}
    compact_operations: Dict[str, Any] = {}
    for operation in SYNTHESIS_OPERATION_FAMILIES:
        row = operations.get(operation)
        if not isinstance(row, dict):
            continue
        compact_operations[operation] = {
            "ready_merchant_count": _safe_int(row.get("ready_merchant_count")),
            "merchant_count": _safe_int(row.get("merchant_count")),
            "ready_share": _safe_float(row.get("ready_share")),
            "candidate_count": _safe_int(row.get("candidate_count")),
            "ready_merchants": [str(item) for item in row.get("ready_merchants") or []][
                :8
            ],
            "blocked_merchants": [
                {
                    "merchant_name": item.get("merchant_name"),
                    "reasons": [str(reason) for reason in item.get("reasons") or []][
                        :5
                    ],
                }
                for item in (row.get("blocked_merchants") or [])[:8]
                if isinstance(item, dict)
            ],
        }
    return {
        "operation_count": _safe_int(value.get("operation_count"))
        or len(SYNTHESIS_OPERATION_FAMILIES),
        "ready_operation_count": _safe_int(value.get("ready_operation_count")),
        "ready_operation_share": _safe_float(value.get("ready_operation_share")),
        "operations": compact_operations,
        "recommendations": [str(item) for item in value.get("recommendations") or []][
            :8
        ],
    }


def _compact_accepted_operation_coverage(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    operations = value.get("operations")
    if not isinstance(operations, dict):
        return {}
    compact_operations: Dict[str, Any] = {}
    for operation in SYNTHESIS_OPERATION_FAMILIES:
        row = operations.get(operation)
        if not isinstance(row, dict):
            continue
        compact_operations[operation] = {
            "ready_merchant_count": _safe_int(row.get("ready_merchant_count")),
            "accepted_merchant_count": _safe_int(row.get("accepted_merchant_count")),
            "accepted_ready_merchant_count": _safe_int(
                row.get("accepted_ready_merchant_count")
            ),
            "accepted_count": _safe_int(row.get("accepted_count")),
            "ready_acceptance_share": _safe_float(row.get("ready_acceptance_share")),
            "ready_merchants": [str(item) for item in row.get("ready_merchants") or []][
                :8
            ],
            "accepted_merchants": [
                str(item) for item in row.get("accepted_merchants") or []
            ][:8],
            "uncovered_ready_merchants": [
                str(item) for item in row.get("uncovered_ready_merchants") or []
            ][:8],
        }
    return {
        "operation_count": _safe_int(value.get("operation_count"))
        or len(SYNTHESIS_OPERATION_FAMILIES),
        "ready_operation_count": _safe_int(value.get("ready_operation_count")),
        "accepted_operation_count": _safe_int(value.get("accepted_operation_count")),
        "accepted_ready_operation_count": _safe_int(
            value.get("accepted_ready_operation_count")
        ),
        "accepted_ready_operation_share": _safe_float(
            value.get("accepted_ready_operation_share")
        ),
        "uncovered_ready_operations": [
            str(item) for item in value.get("uncovered_ready_operations") or []
        ][:8],
        "operations": compact_operations,
        "recommendations": [str(item) for item in value.get("recommendations") or []][
            :8
        ],
    }


def _derive_operation_coverage_from_merchants(
    rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    merchant_count = len(rows)
    operations: Dict[str, Any] = {}
    for operation in SYNTHESIS_OPERATION_FAMILIES:
        ready_merchants: list[str] = []
        candidate_count = 0
        blocked: list[dict[str, Any]] = []
        for row in rows:
            merchant = str(row.get("merchant_name") or "Unknown merchant")
            ready_operations = set(
                row.get("contract_ready_operations")
                or row.get("ready_operations")
                or row.get("supported_operations")
                or []
            )
            counts = row.get("candidate_operation_counts") or row.get(
                "operation_counts"
            )
            if isinstance(counts, dict):
                operation_count = _safe_int(counts.get(operation)) or 0
                candidate_count += operation_count
            else:
                operation_count = 0
            if operation in ready_operations or operation_count > 0:
                ready_merchants.append(merchant)
            else:
                reasons = list(row.get("blockers") or row.get("limitations") or [])
                if reasons:
                    blocked.append(
                        {
                            "merchant_name": merchant,
                            "reasons": [str(reason) for reason in reasons[:5]],
                        }
                    )
        operations[operation] = {
            "ready_merchant_count": len(ready_merchants),
            "merchant_count": merchant_count,
            "ready_share": _safe_ratio(len(ready_merchants), merchant_count),
            "candidate_count": candidate_count,
            "ready_merchants": ready_merchants[:8],
            "blocked_merchants": blocked[:8],
        }
    ready_operation_count = sum(
        1
        for row in operations.values()
        if (_safe_int(row.get("ready_merchant_count")) or 0) > 0
    )
    return {
        "operation_count": len(SYNTHESIS_OPERATION_FAMILIES),
        "ready_operation_count": ready_operation_count,
        "ready_operation_share": _safe_ratio(
            ready_operation_count,
            len(SYNTHESIS_OPERATION_FAMILIES),
        ),
        "operations": operations,
        "recommendations": [],
    }


def _derive_accepted_operation_coverage_from_merchants(
    rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    operations: Dict[str, Any] = {}
    uncovered_ready_operations: list[str] = []
    ready_operation_count = 0
    accepted_operation_count = 0
    accepted_ready_operation_count = 0

    for operation in SYNTHESIS_OPERATION_FAMILIES:
        ready_merchants: list[str] = []
        accepted_merchants: list[str] = []
        ready_accepted_merchants: list[str] = []
        uncovered_ready_merchants: list[str] = []
        accepted_count = 0
        for row in rows:
            merchant = str(row.get("merchant_name") or "Unknown merchant")
            ready_operations = set(
                row.get("contract_ready_operations")
                or row.get("ready_operations")
                or row.get("supported_operations")
                or []
            )
            accepted_counts = row.get("accepted_operation_counts")
            operation_accepted_count = (
                _safe_int(accepted_counts.get(operation))
                if isinstance(accepted_counts, dict)
                else None
            ) or 0
            is_ready = operation in ready_operations
            if is_ready:
                ready_merchants.append(merchant)
            if operation_accepted_count > 0:
                accepted_merchants.append(merchant)
                accepted_count += operation_accepted_count
            if is_ready and operation_accepted_count > 0:
                ready_accepted_merchants.append(merchant)
            elif is_ready:
                uncovered_ready_merchants.append(merchant)

        ready_count = len(ready_merchants)
        accepted_ready_count = len(ready_accepted_merchants)
        if ready_count:
            ready_operation_count += 1
        if accepted_count:
            accepted_operation_count += 1
        if ready_count and accepted_ready_count:
            accepted_ready_operation_count += 1
        if ready_count and not accepted_ready_count:
            uncovered_ready_operations.append(operation)

        operations[operation] = {
            "ready_merchant_count": ready_count,
            "accepted_merchant_count": len(accepted_merchants),
            "accepted_ready_merchant_count": accepted_ready_count,
            "accepted_count": accepted_count,
            "ready_acceptance_share": (
                _safe_ratio(accepted_ready_count, ready_count) if ready_count else None
            ),
            "ready_merchants": ready_merchants[:8],
            "accepted_merchants": accepted_merchants[:8],
            "uncovered_ready_merchants": uncovered_ready_merchants[:8],
        }

    recommendations = (
        ["cover_ready_operations_before_training"] if uncovered_ready_operations else []
    )
    return {
        "operation_count": len(SYNTHESIS_OPERATION_FAMILIES),
        "ready_operation_count": ready_operation_count,
        "accepted_operation_count": accepted_operation_count,
        "accepted_ready_operation_count": accepted_ready_operation_count,
        "accepted_ready_operation_share": _safe_ratio(
            accepted_ready_operation_count,
            ready_operation_count,
        ),
        "uncovered_ready_operations": uncovered_ready_operations,
        "operations": operations,
        "recommendations": recommendations,
    }


def _compact_merchant_gap_summary(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    merchants = []
    for row in (value.get("merchants") or [])[:8]:
        if not isinstance(row, dict):
            continue
        merchants.append(
            {
                "merchant_name": row.get("merchant_name"),
                "status": row.get("status"),
                "score": _safe_float(row.get("score")),
                "candidate_count": _safe_int(row.get("candidate_count")),
                "accepted_count": _safe_int(row.get("accepted_count")),
                "ready_operation_count": _safe_int(row.get("ready_operation_count")),
                "missing_operations": list(row.get("missing_operations") or [])[:8],
                "operation_gap_reasons": row.get("operation_gap_reasons") or {},
                "blockers": list(row.get("blockers") or [])[:5],
                "limitations": list(row.get("limitations") or [])[:5],
            }
        )
    return {
        "blocked_merchant_count": _safe_int(value.get("blocked_merchant_count")),
        "merchant_gap_count": _safe_int(value.get("merchant_gap_count")),
        "top_blockers": _compact_count_map(value.get("top_blockers")),
        "top_limitations": _compact_count_map(value.get("top_limitations")),
        "merchants": merchants,
    }


def _contract_ready_operations(contract: Dict[str, Any]) -> List[str]:
    operation_contracts = contract.get("operation_contracts") or {}
    if not isinstance(operation_contracts, dict):
        return list(contract.get("ready_operations") or [])[:8]
    return [
        str(operation)
        for operation, evidence in operation_contracts.items()
        if isinstance(evidence, dict) and evidence.get("ready") is True
    ][:8]


def _compact_quality_examples(rows: Any) -> List[Dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    examples: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        example = {
            "candidate_id": row.get("candidate_id"),
            "receipt_key": row.get("receipt_key"),
            "operation": row.get("operation"),
            "category": row.get("category"),
            "changed_text": _clip_text(str(row.get("changed_text") or ""), 96),
            "label": row.get("label"),
            "structure_similarity": _safe_float(row.get("structure_similarity")),
            "candidate_quality": _compact_candidate_quality(
                row.get("candidate_quality")
            ),
            "accuracy_checks": [
                str(check) for check in (row.get("accuracy_checks") or [])[:8]
            ],
            "receipt_shape": row.get("receipt_shape") or {},
            "preview_lines": [
                {
                    "line_number": _safe_int(line.get("line_number")),
                    "text": _clip_text(str(line.get("text") or ""), 96),
                    "role": line.get("role"),
                    "synthetic_insert": line.get("synthetic_insert") is True,
                    "modified_labels": list(line.get("modified_labels") or [])[:4],
                }
                for line in (row.get("preview_lines") or [])[:3]
                if isinstance(line, dict)
            ],
        }
        selection_evidence = _compact_selection_evidence(row.get("selection_evidence"))
        if selection_evidence:
            example["selection_evidence"] = selection_evidence
        source_lineage = _compact_source_lineage(row.get("source_lineage"))
        if source_lineage:
            example["source_lineage"] = source_lineage
        structure_evidence = _compact_structure_accuracy_evidence(
            row.get("structure_evidence")
        )
        if structure_evidence:
            example["structure_evidence"] = structure_evidence
        total_change = row.get("total_change")
        if isinstance(total_change, dict):
            example["total_change"] = {
                "old_grand_total": total_change.get("old_grand_total"),
                "new_grand_total": total_change.get("new_grand_total"),
                "tax_delta": total_change.get("tax_delta"),
            }
        field_replacement = row.get("field_replacement")
        if isinstance(field_replacement, dict):
            example["field_replacement"] = {
                "old_text": field_replacement.get("old_text"),
                "new_text": field_replacement.get("new_text"),
                "format": field_replacement.get("format"),
            }
        catalog_grounding = row.get("catalog_grounding")
        if isinstance(catalog_grounding, dict):
            example["catalog_grounding"] = _compact_catalog_grounding_evidence(
                catalog_grounding
            )
        category_placement = _compact_category_placement(row.get("category_placement"))
        if category_placement:
            example["category_placement"] = category_placement
        removal_context = _compact_removal_context(row.get("removal_context"))
        if removal_context:
            example["removal_context"] = removal_context
        layout_integrity = _compact_layout_integrity_evidence(
            row.get("layout_integrity")
        )
        if layout_integrity:
            example["layout_integrity"] = layout_integrity
        examples.append(
            {
                key: value
                for key, value in example.items()
                if value not in (None, "", [], {})
            }
        )
        if len(examples) >= 3:
            break
    return examples


def _compact_selection_evidence(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    selected_score = value.get("selected_score")
    selected_score = selected_score if isinstance(selected_score, dict) else {}
    compact_score = {
        "candidate_quality": _safe_float(selected_score.get("candidate_quality")),
        "high_fidelity": (
            selected_score.get("high_fidelity")
            if isinstance(selected_score.get("high_fidelity"), bool)
            else None
        ),
        "structure_similarity": _safe_float(selected_score.get("structure_similarity")),
        "structure_component_pass_rate": _safe_float(
            selected_score.get("structure_component_pass_rate")
        ),
        "layout_integrity": _safe_float(selected_score.get("layout_integrity")),
        "token_budget": _safe_float(selected_score.get("token_budget")),
        "within_real_score_range": (
            selected_score.get("within_real_score_range")
            if isinstance(selected_score.get("within_real_score_range"), bool)
            else None
        ),
        "delta_from_min": _safe_float(selected_score.get("delta_from_min")),
        "baseline_pair_count": _safe_int(selected_score.get("baseline_pair_count")),
        "token_count": _safe_int(selected_score.get("token_count")),
    }
    result = {
        "schema_version": value.get("schema_version"),
        "selected_from_candidate_count": _safe_int(
            value.get("selected_from_candidate_count")
        ),
        "selected_input_index": _safe_int(value.get("selected_input_index")),
        "ranked_by": [str(item) for item in (value.get("ranked_by") or [])[:8] if item],
        "selected_score": {
            key: item
            for key, item in compact_score.items()
            if item not in (None, "", [], {})
        },
        "selection_policy": _clip_text(str(value.get("selection_policy") or ""), 160),
    }
    return {key: item for key, item in result.items() if item not in (None, "", [], {})}


def _compact_candidate_quality(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    components = {
        str(name): score
        for name, raw_score in (value.get("components") or {}).items()
        if (score := _safe_float(raw_score)) is not None
    }
    result = {
        "score": _safe_float(value.get("score")),
        "high_fidelity": value.get("high_fidelity") is True,
        "components": components,
    }
    structure_gate = value.get("structure_gate")
    if isinstance(structure_gate, dict):
        result["structure_gate"] = {
            "min_structure_similarity": _safe_float(
                structure_gate.get("min_structure_similarity")
            ),
            "structure_similarity_passed": (
                structure_gate.get("structure_similarity_passed") is True
            ),
            "component_thresholds": {
                str(name): threshold
                for name, raw_threshold in (
                    structure_gate.get("component_thresholds") or {}
                ).items()
                if (threshold := _safe_float(raw_threshold)) is not None
            },
            "passed_components": [
                str(item)
                for item in (structure_gate.get("passed_components") or [])[:8]
                if item
            ],
            "failed_components": {
                str(name): {
                    "value": _safe_float(details.get("value")),
                    "threshold": _safe_float(details.get("threshold")),
                }
                for name, details in (
                    structure_gate.get("failed_components") or {}
                ).items()
                if isinstance(details, dict)
            },
            "missing_components": [
                str(item)
                for item in (structure_gate.get("missing_components") or [])[:8]
                if item
            ],
            "pass_rate": _safe_float(structure_gate.get("pass_rate")),
            "passed": structure_gate.get("passed") is True,
        }
    return {key: item for key, item in result.items() if item not in (None, {}, [])}


def _compact_operation_label_names(value: Any) -> List[str]:
    labels: List[str] = []
    for item in (value or [])[:8]:
        label = str(item or "").strip().upper()
        if label in SYNTHESIS_OPERATION_LABEL_NAMES:
            labels.append(label)
    return labels


def _compact_operation_readiness_rows(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: List[Dict[str, Any]] = []
    for row in value[:8]:
        if not isinstance(row, dict):
            continue
        evidence = row.get("evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        mutable_fields = evidence.get("mutable_fields")
        compact_evidence = {
            "labels": _compact_operation_label_names(evidence.get("labels")),
            "hard_negative_label_count": _safe_int(
                evidence.get("hard_negative_label_count")
            ),
            "grounded_candidate_count": _safe_int(
                evidence.get("grounded_candidate_count")
            ),
            "removable_item_candidate_count": _safe_int(
                evidence.get("removable_item_candidate_count")
            ),
            "mutable_field_count": _safe_int(evidence.get("mutable_field_count")),
            "mutable_fields": (
                _compact_operation_label_names(sorted(mutable_fields.keys()))
                if isinstance(mutable_fields, dict)
                else []
            ),
        }
        compact = {
            "operation": row.get("operation"),
            "ready": row.get("ready") is True,
            "supported": row.get("supported") is True,
            "candidate_count": _safe_int(row.get("candidate_count")),
            "evidence_candidate_count": _safe_int(row.get("evidence_candidate_count")),
            "evidence": {
                key: item
                for key, item in compact_evidence.items()
                if item not in (None, "", [], {})
            },
            "blockers": [str(item) for item in (row.get("blockers") or [])[:8] if item],
        }
        rows.append(
            {
                key: item
                for key, item in compact.items()
                if item not in (None, "", [], {})
            }
        )
    return rows


def _compact_report_merchant(row: Dict[str, Any]) -> Dict[str, Any]:
    candidate_count = _safe_int(row.get("candidate_count"))
    accepted_count = _safe_int(row.get("accepted_count"))
    source_quality_operation_blockers = row.get("source_quality_operation_blockers")
    if not isinstance(source_quality_operation_blockers, dict):
        source_quality_operation_blockers = {}
    result = {
        "merchant_name": row.get("merchant_name"),
        "readiness_status": row.get("readiness_status") or row.get("status"),
        "readiness_score": _safe_float(row.get("readiness_score") or row.get("score")),
        "source_receipt_count": _safe_int(row.get("source_receipt_count")),
        "source_quality_status": row.get("source_quality_status"),
        "source_quality_receipt_count": _safe_int(
            row.get("source_quality_receipt_count")
        ),
        "source_quality_labeled_word_count": _safe_int(
            row.get("source_quality_labeled_word_count")
        ),
        "source_quality_receipts_with_line_item_labels": _safe_int(
            row.get("source_quality_receipts_with_line_item_labels")
        ),
        "source_quality_receipts_with_grand_total_label": _safe_int(
            row.get("source_quality_receipts_with_grand_total_label")
        ),
        "source_quality_receipts_with_date_or_time_label": _safe_int(
            row.get("source_quality_receipts_with_date_or_time_label")
        ),
        "source_quality_text_structure_status": row.get(
            "source_quality_text_structure_status"
        ),
        "source_quality_line_item_like_text_line_count": _safe_int(
            row.get("source_quality_line_item_like_text_line_count")
        ),
        "source_quality_total_like_text_line_count": _safe_int(
            row.get("source_quality_total_like_text_line_count")
        ),
        "source_quality_limitations": _source_quality_limitations(
            {"limitations": row.get("source_quality_limitations") or []}
        ),
        "source_quality_requires_label_validation": (
            True
            if row.get("source_quality_requires_label_validation") is True
            else None
        ),
        "candidate_count": candidate_count,
        "accepted_count": accepted_count,
        "rejected_count": _safe_int(row.get("rejected_count")),
        "acceptance_rate": (
            _safe_float(row.get("acceptance_rate"))
            if row.get("acceptance_rate") is not None
            else _safe_ratio(accepted_count, candidate_count)
        ),
        "supported_operations": list(row.get("supported_operations") or [])[:8],
        "contract_ready_operations": list(
            row.get("contract_ready_operations") or row.get("ready_operations") or []
        )[:8],
        "accepted_operation_counts": _compact_count_map(
            row.get("accepted_operation_counts")
        ),
        "accepted_category_counts": _compact_count_map(
            row.get("accepted_category_counts")
        ),
        "accepted_field_replacement_counts": _compact_count_map(
            row.get("accepted_field_replacement_counts")
        ),
        "safe_mutable_fields": list(row.get("safe_mutable_fields") or [])[:8],
        "operation_readiness": _compact_operation_readiness_rows(
            row.get("operation_readiness")
        ),
        "missing_operations": [
            str(item) for item in (row.get("missing_operations") or [])[:8] if item
        ],
        "next_synthesis_actions": [
            str(item) for item in (row.get("next_synthesis_actions") or [])[:8] if item
        ],
        "accepted_structure_similarity": _compact_score_summary(
            row.get("accepted_structure_similarity")
        ),
        "rejection_reasons": _compact_count_map(row.get("rejection_reasons")),
        "blockers": list(row.get("blockers") or [])[:5],
        "limitations": list(row.get("limitations") or [])[:5],
        "accepted_examples": _compact_quality_examples(row.get("accepted_examples")),
        "rejected_examples": [
            item
            for item in (row.get("rejected_examples") or [])[:5]
            if isinstance(item, dict)
        ],
    }
    accepted_components = _compact_component_score_summary(
        row.get("accepted_structure_components")
    )
    if accepted_components:
        result["accepted_structure_components"] = accepted_components
    accepted_real_baseline = _compact_real_baseline_comparison_summary(
        row.get("accepted_real_baseline_comparison")
    )
    if _safe_int(accepted_real_baseline.get("count")):
        result["accepted_real_baseline_comparison"] = accepted_real_baseline
    accepted_candidate_quality = _compact_score_summary(
        row.get("accepted_candidate_quality")
    )
    if accepted_candidate_quality:
        result["accepted_candidate_quality"] = accepted_candidate_quality
    accepted_quality_components = _compact_component_score_summary(
        row.get("accepted_candidate_quality_components")
    )
    if accepted_quality_components:
        result["accepted_candidate_quality_components"] = accepted_quality_components
    accepted_source_lineage = _compact_source_lineage_summary(
        row.get("accepted_source_lineage")
    )
    if accepted_source_lineage:
        result["accepted_source_lineage"] = accepted_source_lineage
    if source_quality_operation_blockers:
        result["source_quality_operation_blockers"] = {
            str(operation): str(reason)
            for operation, reason in source_quality_operation_blockers.items()
            if reason
        }
    return {key: value for key, value in result.items() if value not in (None, "", [])}


def _compact_synthesis_quality_report(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    summary = value.get("summary") or {}
    quality_gates = value.get("quality_gates") or {}
    compact_summary = {
        "merchant_count": _safe_int(summary.get("merchant_count")),
        "accepted_merchant_count": _safe_int(summary.get("accepted_merchant_count")),
        "candidate_count": _safe_int(summary.get("candidate_count")),
        "accepted_count": _safe_int(summary.get("accepted_count")),
        "rejected_count": _safe_int(summary.get("rejected_count")),
        "acceptance_rate": _safe_float(summary.get("acceptance_rate")),
        "accepted_operation_counts": _compact_count_map(
            summary.get("accepted_operation_counts")
        ),
        "accepted_category_counts": _compact_count_map(
            summary.get("accepted_category_counts")
        ),
        "accepted_field_replacement_counts": _compact_count_map(
            summary.get("accepted_field_replacement_counts")
        ),
        "next_synthesis_action_counts": _compact_count_map(
            summary.get("next_synthesis_action_counts")
        ),
        "accepted_structure_similarity": _compact_score_summary(
            summary.get("accepted_structure_similarity")
        ),
        "rejection_reasons": _compact_count_map(summary.get("rejection_reasons")),
        "contract_count": _safe_int(summary.get("contract_count")),
        "ready_contract_count": _safe_int(summary.get("ready_contract_count")),
        "source_quality_status_counts": _compact_count_map(
            summary.get("source_quality_status_counts")
        ),
        "blocked_source_quality_merchant_count": _safe_int(
            summary.get("blocked_source_quality_merchant_count")
        ),
    }
    accepted_components = _compact_component_score_summary(
        summary.get("accepted_structure_components")
    )
    if accepted_components:
        compact_summary["accepted_structure_components"] = accepted_components
    accepted_real_baseline = _compact_real_baseline_comparison_summary(
        summary.get("accepted_real_baseline_comparison")
    )
    if _safe_int(accepted_real_baseline.get("count")):
        compact_summary["accepted_real_baseline_comparison"] = accepted_real_baseline
    accepted_mix_balance = _compact_mix_balance(summary.get("accepted_mix_balance"))
    if accepted_mix_balance:
        compact_summary["accepted_mix_balance"] = accepted_mix_balance
    accepted_source_lineage = _compact_source_lineage_summary(
        summary.get("accepted_source_lineage")
    )
    if accepted_source_lineage:
        compact_summary["accepted_source_lineage"] = accepted_source_lineage
    accepted_candidate_quality = _compact_score_summary(
        summary.get("accepted_candidate_quality")
    )
    if accepted_candidate_quality:
        compact_summary["accepted_candidate_quality"] = accepted_candidate_quality
    accepted_quality_components = _compact_component_score_summary(
        summary.get("accepted_candidate_quality_components")
    )
    if accepted_quality_components:
        compact_summary["accepted_candidate_quality_components"] = (
            accepted_quality_components
        )
    llm_execution = _compact_llm_execution_summary(summary.get("llm_execution"))
    if llm_execution:
        compact_summary["llm_execution"] = llm_execution
    raw_training_ready = value.get("training_ready")
    training_ready = (
        raw_training_ready
        if isinstance(raw_training_ready, bool)
        else value.get("ready") is True
    )
    coverage_gate = quality_gates.get("accepted_operation_coverage_gate")
    compact_coverage_gate = (
        {
            "enabled": coverage_gate.get("enabled") is True,
            "passed": coverage_gate.get("passed") is True,
            "ready_operation_count": _safe_int(
                coverage_gate.get("ready_operation_count")
            ),
            "accepted_ready_operation_count": _safe_int(
                coverage_gate.get("accepted_ready_operation_count")
            ),
            "uncovered_ready_operations": [
                str(item)
                for item in (coverage_gate.get("uncovered_ready_operations") or [])[:8]
            ],
        }
        if isinstance(coverage_gate, dict) and coverage_gate
        else {}
    )
    llm_freshness_gate = quality_gates.get("llm_model_freshness_gate")
    compact_llm_freshness_gate = {}
    if isinstance(llm_freshness_gate, dict) and llm_freshness_gate:
        compact_llm_freshness_gate = {
            "enabled": llm_freshness_gate.get("enabled") is True,
            "passed": llm_freshness_gate.get("passed") is True,
            "requires_current_model_guidance": (
                llm_freshness_gate.get("requires_current_model_guidance") is True
            ),
            "api_call_allowed_count": _safe_int(
                llm_freshness_gate.get("api_call_allowed_count")
            ),
            "llm_assisted_mode_count": _safe_int(
                llm_freshness_gate.get("llm_assisted_mode_count")
            ),
            "latest_model_verified_at": llm_freshness_gate.get(
                "latest_model_verified_at"
            ),
            "latest_model_age_days": _safe_int(
                llm_freshness_gate.get("latest_model_age_days")
            ),
            "max_age_days": _safe_int(llm_freshness_gate.get("max_age_days")),
            "latest_openai_models": [
                str(item)
                for item in (llm_freshness_gate.get("latest_openai_models") or [])[:3]
                if item
            ],
            "latest_model_sources": [
                str(item)
                for item in (llm_freshness_gate.get("latest_model_sources") or [])[:3]
                if item
            ],
            "reason": llm_freshness_gate.get("reason"),
        }
        compact_llm_freshness_gate = {
            key: item
            for key, item in compact_llm_freshness_gate.items()
            if item not in (None, "", [])
        }
    return {
        "ready": value.get("ready") is True,
        "training_ready": training_ready,
        "training_ready_reasons": [
            str(item) for item in (value.get("training_ready_reasons") or [])[:8]
        ],
        "bundle_ready": value.get("bundle_ready") is True,
        "bundle_reasons": list(value.get("bundle_reasons") or [])[:10],
        "summary": compact_summary,
        "operation_coverage": _compact_operation_coverage(
            value.get("operation_coverage")
        ),
        "accepted_operation_coverage": _compact_accepted_operation_coverage(
            value.get("accepted_operation_coverage")
        ),
        "merchant_gap_summary": _compact_merchant_gap_summary(
            value.get("merchant_gap_summary")
        ),
        "training_batch_policy": _compact_training_batch_policy(
            value.get("training_batch_policy")
            or value.get("synthetic_training_batch_policy")
        ),
        "quality_gates": {
            "validation_policy": quality_gates.get("validation_policy"),
            "train_only_examples": quality_gates.get("train_only_examples") is True,
            "contract_gate": quality_gates.get("contract_gate") or {},
            "max_per_merchant": _safe_int(quality_gates.get("max_per_merchant")),
            "max_per_merchant_operation": _safe_int(
                quality_gates.get("max_per_merchant_operation")
            ),
            "min_structure_similarity": _safe_float(
                quality_gates.get("min_structure_similarity")
            ),
            "structure_component_thresholds": {
                str(name): threshold
                for name, value in (
                    quality_gates.get("structure_component_thresholds") or {}
                ).items()
                if (threshold := _safe_float(value)) is not None
            },
            "accepted_operation_coverage_gate": compact_coverage_gate,
            "llm_model_freshness_gate": compact_llm_freshness_gate,
        },
        "recommendations": [
            str(item) for item in (value.get("recommendations") or [])[:8]
        ],
        "merchants": [
            _compact_report_merchant(row)
            for row in (value.get("merchants") or [])[:8]
            if isinstance(row, dict)
        ],
    }


def _compact_training_batch_policy(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {}
    compact = {
        "schema_version": value.get("schema_version"),
        "status": value.get("status"),
        "recommended_example_count": _safe_int(value.get("recommended_example_count")),
        "accepted_candidate_count": _safe_int(value.get("accepted_candidate_count")),
        "selected_candidate_count": _safe_int(value.get("selected_candidate_count")),
        "candidate_quality_count": _safe_int(value.get("candidate_quality_count")),
        "high_fidelity_candidate_count": _safe_int(
            value.get("high_fidelity_candidate_count")
        ),
        "max_synthetic_train_share": _safe_float(
            value.get("max_synthetic_train_share")
        ),
        "max_per_merchant": _safe_int(value.get("max_per_merchant")),
        "max_per_merchant_operation": _safe_int(
            value.get("max_per_merchant_operation")
        ),
        "overtraining_risk_level": value.get("overtraining_risk_level"),
        "risk_reasons": [
            str(item) for item in (value.get("risk_reasons") or [])[:8] if item
        ],
        "hold_reasons": [
            str(item) for item in (value.get("hold_reasons") or [])[:8] if item
        ],
        "requires_real_validation_split": (
            value.get("requires_real_validation_split") is True
        ),
        "review_required": value.get("review_required") is True,
    }
    return {key: item for key, item in compact.items() if item not in (None, "", [])}


def _derive_synthesis_quality_report(
    *,
    bundle: Dict[str, Any],
    candidate_mix: Dict[str, Any],
    selection: Dict[str, Any],
    contracts: List[Dict[str, Any]],
    candidate_examples: List[Dict[str, Any]],
) -> Dict[str, Any]:
    mix_rows = [
        row for row in candidate_mix.get("merchants") or [] if isinstance(row, dict)
    ]
    contracts_by_merchant = {
        str(contract.get("merchant_name") or "Unknown merchant"): contract
        for contract in contracts
        if isinstance(contract, dict)
    }
    source_quality_by_merchant = _source_quality_by_merchant(
        bundle.get("source_receipt_quality")
    )
    examples_by_merchant: Dict[str, list[dict[str, Any]]] = {}
    for example in candidate_examples:
        merchant = str(example.get("merchant_name") or "Unknown merchant")
        accuracy = (
            example.get("accuracy_evidence")
            if isinstance(example.get("accuracy_evidence"), dict)
            else {}
        )
        examples_by_merchant.setdefault(merchant, []).append(
            {
                "candidate_id": example.get("candidate_id"),
                "operation": example.get("operation"),
                "category": example.get("category") or accuracy.get("category"),
                "changed_text": (
                    accuracy.get("changed_text")
                    or example.get("item_text")
                    or example.get("new_text")
                ),
                "label": example.get("field_label"),
                "structure_similarity": example.get("structure_similarity"),
                "candidate_quality": example.get("candidate_quality"),
                "selection_evidence": example.get("selection_evidence"),
                "source_lineage": example.get("source_lineage"),
                "structure_evidence": accuracy.get("structure_similarity"),
                "accuracy_checks": accuracy.get("checks") or [],
                "layout_integrity": accuracy.get("layout_integrity"),
                "catalog_grounding": accuracy.get("catalog_grounding"),
                "category_placement": _compact_category_placement(
                    accuracy.get("category_placement")
                ),
                "receipt_shape": {
                    "line_count": (example.get("receipt_preview") or {}).get(
                        "line_count"
                    ),
                    "token_count": (example.get("receipt_preview") or {}).get(
                        "token_count"
                    ),
                    "truncated": (example.get("receipt_preview") or {}).get(
                        "truncated"
                    ),
                },
                "preview_lines": (example.get("receipt_preview") or {}).get("lines")
                or [],
            }
        )
    merchant_names = sorted(
        {str(row.get("merchant_name") or "Unknown merchant") for row in mix_rows}
        | set(contracts_by_merchant)
        | set(examples_by_merchant)
    )
    mix_by_merchant = {
        str(row.get("merchant_name") or "Unknown merchant"): row for row in mix_rows
    }
    merchant_rows = []
    for merchant in merchant_names:
        mix = mix_by_merchant.get(merchant, {})
        contract = contracts_by_merchant.get(merchant, {})
        source_quality = source_quality_by_merchant.get(merchant) or (
            contract.get("source_receipt_quality") or {}
        )
        source_quality_operation_blockers = (
            _source_quality_operation_blockers_from_contract(contract)
            or _source_quality_operation_blockers(source_quality)
        )
        source_quality_requires_label_validation = (
            _source_quality_requires_label_validation(source_quality)
        )
        candidate_count = _safe_int(mix.get("candidate_count"))
        accepted_count = _safe_int(mix.get("accepted_count"))
        accepted_examples = examples_by_merchant.get(merchant, [])[:3]
        accepted_source_lineage = _compact_source_lineage_summary(
            mix.get("accepted_source_lineage")
        )
        if not accepted_source_lineage and accepted_count:
            accepted_source_lineage = _accepted_source_lineage_from_candidates(
                examples_by_merchant.get(merchant, []),
                expected_candidate_count=accepted_count,
            )
        contract_ready_operations = _contract_ready_operations(contract)
        accepted_operation_counts = _compact_count_map(
            mix.get("accepted_operation_counts")
            or (contract.get("bundle_acceptance") or {}).get(
                "accepted_operation_counts"
            )
        )
        merchant_row = {
            "merchant_name": merchant,
            "readiness_status": contract.get("status"),
            "readiness_score": _safe_float(contract.get("score")),
            "source_receipt_count": _safe_int(contract.get("source_receipt_count")),
            "candidate_count": candidate_count,
            "accepted_count": accepted_count,
            "rejected_count": _safe_int(mix.get("rejected_count")),
            "acceptance_rate": _safe_ratio(accepted_count, candidate_count),
            "supported_operations": list(contract.get("supported_operations") or [])[
                :8
            ],
            "contract_ready_operations": contract_ready_operations,
            "operation_counts": _compact_count_map(mix.get("operation_counts")),
            "candidate_operation_counts": _compact_count_map(
                mix.get("candidate_operation_counts")
            ),
            "accepted_operation_counts": accepted_operation_counts,
            "accepted_category_counts": _compact_count_map(
                mix.get("accepted_category_counts")
                or (contract.get("bundle_acceptance") or {}).get(
                    "accepted_category_counts"
                )
            ),
            "accepted_field_replacement_counts": _compact_count_map(
                (contract.get("bundle_acceptance") or {}).get(
                    "accepted_field_replacement_counts"
                )
            ),
            "accepted_structure_similarity": _compact_score_summary(
                mix.get("accepted_structure_similarity")
            ),
            "accepted_real_baseline_comparison": (
                _compact_real_baseline_comparison_summary(
                    mix.get("accepted_real_baseline_comparison")
                )
            ),
            "accepted_structure_components": _compact_component_score_summary(
                mix.get("accepted_structure_components")
            ),
            "accepted_candidate_quality": _compact_score_summary(
                mix.get("accepted_candidate_quality")
            ),
            "accepted_candidate_quality_components": (
                _compact_component_score_summary(
                    mix.get("accepted_candidate_quality_components")
                )
            ),
            "accepted_source_lineage": accepted_source_lineage,
            "rejection_reasons": _compact_count_map(mix.get("rejection_reasons")),
            "blockers": list(contract.get("blockers") or [])[:5],
            "limitations": list(contract.get("limitations") or [])[:5],
            "source_quality_requires_label_validation": (
                source_quality_requires_label_validation
            ),
            "next_synthesis_actions": _derive_next_synthesis_actions(
                readiness_status=contract.get("status"),
                source_quality_requires_label_validation=(
                    source_quality_requires_label_validation
                ),
                ready_operations=contract_ready_operations,
                accepted_operation_counts=accepted_operation_counts,
            ),
            "accepted_examples": accepted_examples,
        }
        merchant_row.update(
            _report_source_quality_fields(
                source_quality,
                operation_blockers=source_quality_operation_blockers,
            )
        )
        merchant_rows.append(merchant_row)
    candidate_count = _safe_int(candidate_mix.get("candidate_count"))
    accepted_count = _safe_int(candidate_mix.get("accepted_count"))
    accepted_source_lineage = _compact_source_lineage_summary(
        candidate_mix.get("accepted_source_lineage")
    )
    if not accepted_source_lineage and accepted_count:
        accepted_source_lineage = _accepted_source_lineage_from_candidates(
            candidate_examples,
            expected_candidate_count=accepted_count,
        )
    recommendations: list[str] = []
    if candidate_mix.get("rejection_reasons", {}).get("merchant_synthesis_not_ready"):
        recommendations.append("collect_more_receipts_for_not_ready_merchants")
    if any(
        str(row.get("source_quality_status") or "").lower() == "blocked"
        for row in merchant_rows
    ):
        recommendations.append("fix_source_receipt_quality_before_synthesis")
    if any(
        row.get("source_quality_requires_label_validation") is True
        or row.get("source_quality_text_structure_status")
        == "recoverable_unlabeled_text"
        or "unlabeled_text_requires_label_validation"
        in (
            set(row.get("source_quality_limitations") or [])
            | set(row.get("limitations") or [])
        )
        for row in merchant_rows
    ):
        recommendations.append("validate_recoverable_unlabeled_receipts")
    if candidate_mix.get("rejection_reasons", {}).get(
        "operation_not_supported_by_contract"
    ):
        recommendations.append("enable_only_contract_supported_mutations")
    if _safe_int(candidate_mix.get("accepted_arithmetic_candidate_count")):
        recommendations.append("verify_total_and_tax_reconciliation_in_preview")
    if _safe_int(candidate_mix.get("accepted_grounded_candidate_count")):
        recommendations.append("prefer_cross_receipt_grounded_item_mutations")
    operation_coverage = _derive_operation_coverage_from_merchants(merchant_rows)
    accepted_operation_coverage = _derive_accepted_operation_coverage_from_merchants(
        merchant_rows
    )
    llm_execution = _compact_llm_execution_summary(
        (bundle.get("preflight") or {}).get("llm_execution")
    )
    llm_model_freshness_gate = _llm_model_freshness_gate(llm_execution)
    for recommendation in accepted_operation_coverage.get("recommendations") or []:
        if recommendation not in recommendations:
            recommendations.append(str(recommendation))
    training_ready_reasons: list[str] = []
    if bundle.get("ready") is False:
        training_ready_reasons.append("resolve_bundle_reasons_before_training")
    if not accepted_count:
        training_ready_reasons.append("collect_more_receipts_or_fix_parameterization")
    if any(contract.get("status") != "ready" for contract in contracts):
        training_ready_reasons.append("collect_more_receipts_for_not_ready_merchants")
    if any(
        str(row.get("source_quality_status") or "").lower() == "blocked"
        for row in merchant_rows
    ):
        training_ready_reasons.append("fix_source_receipt_quality_before_synthesis")
    if any(
        row.get("source_quality_requires_label_validation") is True
        or row.get("source_quality_text_structure_status")
        == "recoverable_unlabeled_text"
        or "unlabeled_text_requires_label_validation"
        in (
            set(row.get("source_quality_limitations") or [])
            | set(row.get("limitations") or [])
        )
        for row in merchant_rows
    ):
        training_ready_reasons.append("validate_recoverable_unlabeled_receipts")
    if accepted_operation_coverage.get("uncovered_ready_operations"):
        training_ready_reasons.append("cover_ready_operations_before_training")
    if (
        llm_model_freshness_gate.get("requires_current_model_guidance") is True
        and llm_model_freshness_gate.get("passed") is not True
    ):
        training_ready_reasons.append("refresh_latest_model_guidance_before_synthesis")
    if _accepted_source_lineage_incomplete(
        accepted_source_lineage,
        accepted_count=accepted_count,
    ):
        training_ready_reasons.append("complete_source_lineage_before_training")
    balance = candidate_mix.get("accepted_mix_balance") or {}
    if str(balance.get("risk_level") or "").lower() in {"medium", "high"}:
        training_ready_reasons.append("rebalance_synthetic_mix_before_training")
    merchant_gap_summary = {
        "blocked_merchant_count": sum(
            1
            for row in merchant_rows
            if str(row.get("readiness_status") or "").lower()
            not in {"ready", "partial"}
        ),
        "merchant_gap_count": sum(
            1 for row in merchant_rows if row.get("rejected_count")
        ),
        "top_blockers": _count_values(
            blocker
            for row in merchant_rows
            for blocker in row.get("blockers", [])
            if blocker
        ),
        "top_limitations": _count_values(
            limitation
            for row in merchant_rows
            for limitation in row.get("limitations", [])
            if limitation
        ),
        "merchants": [
            {
                "merchant_name": row.get("merchant_name"),
                "status": row.get("readiness_status"),
                "score": row.get("readiness_score"),
                "candidate_count": row.get("candidate_count"),
                "accepted_count": row.get("accepted_count"),
                "ready_operation_count": len(
                    row.get("contract_ready_operations") or []
                ),
                "missing_operations": [],
                "operation_gap_reasons": {},
                "blockers": row.get("blockers") or [],
                "limitations": row.get("limitations") or [],
            }
            for row in merchant_rows[:8]
        ],
    }
    return _compact_synthesis_quality_report(
        {
            "ready": bundle.get("ready") is not False and bool(accepted_count),
            "training_ready": not training_ready_reasons,
            "training_ready_reasons": training_ready_reasons,
            "bundle_ready": bundle.get("ready") is not False,
            "bundle_reasons": list(bundle.get("reasons") or [])[:10],
            "summary": {
                "merchant_count": candidate_mix.get("merchant_count"),
                "accepted_merchant_count": candidate_mix.get("accepted_merchant_count"),
                "candidate_count": candidate_count,
                "accepted_count": accepted_count,
                "rejected_count": candidate_mix.get("rejected_count"),
                "acceptance_rate": _safe_ratio(accepted_count, candidate_count),
                "accepted_operation_counts": candidate_mix.get(
                    "accepted_operation_counts"
                ),
                "accepted_category_counts": candidate_mix.get(
                    "accepted_category_counts"
                ),
                "accepted_field_replacement_counts": candidate_mix.get(
                    "accepted_field_replacement_counts"
                ),
                "accepted_structure_similarity": candidate_mix.get(
                    "accepted_structure_similarity"
                ),
                "accepted_structure_components": candidate_mix.get(
                    "accepted_structure_components"
                ),
                "accepted_real_baseline_comparison": candidate_mix.get(
                    "accepted_real_baseline_comparison"
                ),
                "accepted_candidate_quality": candidate_mix.get(
                    "accepted_candidate_quality"
                ),
                "accepted_candidate_quality_components": candidate_mix.get(
                    "accepted_candidate_quality_components"
                ),
                "accepted_source_lineage": accepted_source_lineage,
                "accepted_mix_balance": candidate_mix.get("accepted_mix_balance"),
                "llm_execution": llm_execution,
                "rejection_reasons": candidate_mix.get("rejection_reasons"),
                "next_synthesis_action_counts": _next_synthesis_action_counts(
                    merchant_rows
                ),
                "contract_count": len(contracts),
                "ready_contract_count": sum(
                    1 for contract in contracts if contract.get("status") == "ready"
                ),
                "source_quality_status_counts": _count_values(
                    str(row.get("source_quality_status"))
                    for row in merchant_rows
                    if row.get("source_quality_status")
                ),
                "blocked_source_quality_merchant_count": sum(
                    1
                    for row in merchant_rows
                    if str(row.get("source_quality_status") or "").lower() == "blocked"
                ),
            },
            "operation_coverage": operation_coverage,
            "accepted_operation_coverage": accepted_operation_coverage,
            "merchant_gap_summary": merchant_gap_summary,
            "training_batch_policy": (
                bundle.get("synthetic_training_batch_policy")
                or selection.get("training_batch_policy")
                or {}
            ),
            "quality_gates": {
                "validation_policy": bundle.get("validation_policy")
                or "real_receipts_only",
                "train_only_examples": True,
                "contract_gate": selection.get("contract_gate") or {},
                "max_per_merchant": selection.get("max_per_merchant"),
                "max_per_merchant_operation": selection.get(
                    "max_per_merchant_operation"
                ),
                "min_structure_similarity": selection.get("min_structure_similarity"),
                "structure_component_thresholds": selection.get(
                    "structure_component_thresholds"
                )
                or {},
                "accepted_operation_coverage_gate": {
                    "enabled": True,
                    "passed": not accepted_operation_coverage.get(
                        "uncovered_ready_operations"
                    ),
                    "ready_operation_count": accepted_operation_coverage.get(
                        "ready_operation_count"
                    ),
                    "accepted_ready_operation_count": accepted_operation_coverage.get(
                        "accepted_ready_operation_count"
                    ),
                    "uncovered_ready_operations": list(
                        accepted_operation_coverage.get("uncovered_ready_operations")
                        or []
                    )[:8],
                },
                "llm_model_freshness_gate": llm_model_freshness_gate,
            },
            "recommendations": recommendations,
            "merchants": merchant_rows,
        }
    )


def _compact_merchant_synthesis_contracts(
    rows: List[Any],
) -> List[Dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        operation_contracts = row.get("operation_contracts") or {}
        ready_operations = [
            str(operation)
            for operation, evidence in (
                operation_contracts.items()
                if isinstance(operation_contracts, dict)
                else []
            )
            if isinstance(evidence, dict) and evidence.get("ready") is True
        ]
        acceptance = row.get("bundle_acceptance") or {}
        compact_row = {
            "merchant_name": row.get("merchant_name"),
            "status": row.get("status"),
            "score": _safe_float(row.get("score")),
            "source_receipt_count": _safe_int(row.get("source_receipt_count")),
            "supported_operations": list(row.get("supported_operations") or [])[:8],
            "ready_operations": ready_operations[:8],
            "accepted_operation_counts": _compact_count_map(
                acceptance.get("accepted_operation_counts")
            ),
            "accepted_category_counts": _compact_count_map(
                acceptance.get("accepted_category_counts")
            ),
            "accepted_field_replacement_counts": _compact_count_map(
                acceptance.get("accepted_field_replacement_counts")
            ),
            "blockers": list(row.get("blockers") or [])[:5],
            "limitations": list(row.get("limitations") or [])[:5],
        }
        tax_contract = row.get("tax_contract")
        if isinstance(tax_contract, dict):
            compact_row["tax_contract"] = {
                "supported_policy": tax_contract.get("supported_policy"),
                "taxable_item_count": _safe_int(tax_contract.get("taxable_item_count")),
                "tax_rate_observation_count": _safe_int(
                    tax_contract.get("tax_rate_observation_count")
                ),
                "stable_tax_rate": tax_contract.get("stable_tax_rate") is True,
                "avg_tax_rate_percent": tax_contract.get("avg_tax_rate_percent"),
                "tax_changing_synthesis_ready": (
                    tax_contract.get("tax_changing_synthesis_ready") is True
                ),
                "tax_changing_synthesis_blockers": list(
                    tax_contract.get("tax_changing_synthesis_blockers") or []
                )[:5],
            }
        compact.append(compact_row)
        if len(compact) >= 8:
            break
    return compact


def _compact_candidate_mix_merchants(rows: List[Any]) -> List[Dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        next_row = {
            "merchant_name": row.get("merchant_name"),
            "candidate_count": _safe_int(row.get("candidate_count")),
            "accepted_count": _safe_int(row.get("accepted_count")),
            "rejected_count": _safe_int(row.get("rejected_count")),
            "rejection_reasons": _compact_count_map(row.get("rejection_reasons")),
            "accepted_operation_counts": _compact_count_map(
                row.get("accepted_operation_counts")
            ),
            "accepted_category_counts": _compact_count_map(
                row.get("accepted_category_counts")
            ),
            "accepted_grounded_candidate_count": _safe_int(
                row.get("accepted_grounded_candidate_count")
            ),
            "accepted_arithmetic_candidate_count": _safe_int(
                row.get("accepted_arithmetic_candidate_count")
            ),
            "accepted_structure_similarity": _compact_score_summary(
                row.get("accepted_structure_similarity")
            ),
            "accepted_candidate_quality": _compact_score_summary(
                row.get("accepted_candidate_quality")
            ),
        }
        accepted_source_lineage = _compact_source_lineage_summary(
            row.get("accepted_source_lineage")
        )
        if accepted_source_lineage:
            next_row["accepted_source_lineage"] = accepted_source_lineage
        accepted_real_baseline = _compact_real_baseline_comparison_summary(
            row.get("accepted_real_baseline_comparison")
        )
        if _safe_int(accepted_real_baseline.get("count")):
            next_row["accepted_real_baseline_comparison"] = accepted_real_baseline
        accepted_components = _compact_component_score_summary(
            row.get("accepted_structure_components")
        )
        if accepted_components:
            next_row["accepted_structure_components"] = accepted_components
        accepted_quality_components = _compact_component_score_summary(
            row.get("accepted_candidate_quality_components")
        )
        if accepted_quality_components:
            next_row["accepted_candidate_quality_components"] = (
                accepted_quality_components
            )
        compact.append(
            {
                key: value
                for key, value in next_row.items()
                if value not in (None, "", [])
            }
        )
        if len(compact) >= 8:
            break
    return compact


def _compact_catalog_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "product_text": item.get("product_text"),
        "category": item.get("category"),
        "observed_count": item.get("observed_count"),
    }


def _compact_readiness(
    readiness: Dict[str, Any],
    *,
    merchant: str,
) -> Dict[str, Any]:
    return {
        "merchant_name": readiness.get("merchant_name") or merchant,
        "status": readiness.get("status"),
        "score": _safe_float(readiness.get("score")),
        "supported_operations": list(readiness.get("supported_operations") or [])[:5],
        "candidate_capacity": _safe_int(readiness.get("candidate_capacity")),
        "catalog_item_count": _safe_int(readiness.get("catalog_item_count")),
        "category_count": _safe_int(readiness.get("category_count")),
        "grounded_add_item_candidate_count": _safe_int(
            readiness.get("grounded_add_item_candidate_count")
        ),
        "removable_item_candidate_count": _safe_int(
            readiness.get("removable_item_candidate_count")
        ),
        "blockers": list(readiness.get("blockers") or [])[:5],
        "limitations": list(readiness.get("limitations") or [])[:5],
    }


def _compact_candidate_example(
    candidate: Dict[str, Any],
    *,
    merchant: str,
) -> Dict[str, Any]:
    metadata = candidate.get("metadata") or {}
    added_item = metadata.get("added_item") or {}
    removed_item = metadata.get("removed_item") or {}
    replacement = metadata.get("field_replacement") or {}
    mutable_evidence = metadata.get("mutable_field_evidence") or {}
    observed = metadata.get("observed_item_evidence") or {}
    structure = metadata.get("structure_similarity") or {}
    preview = metadata.get("synthetic_receipt_preview") or {}
    accuracy_evidence = metadata.get("synthesis_accuracy_evidence") or {}
    candidate_quality = _compact_candidate_quality(metadata.get("candidate_quality"))
    evidence_receipts = _unique_source_keys(
        observed.get("product_seen_outside_base"),
        added_item.get("source_receipt_keys"),
    )
    accuracy_structure = accuracy_evidence.get("structure_similarity")
    accuracy_structure = (
        accuracy_structure if isinstance(accuracy_structure, dict) else {}
    )
    nearest_key = structure.get("nearest_real_receipt_key") or accuracy_structure.get(
        "nearest_real_receipt_key"
    )
    result = {
        "candidate_id": candidate.get("candidate_id"),
        "merchant_name": merchant,
        "source": metadata.get("source"),
        "operation": metadata.get("operation"),
        "actual_label": metadata.get("actual_label"),
        "predicted_label": metadata.get("predicted_label"),
        "item_text": added_item.get("product_text") or removed_item.get("product_text"),
        "category": added_item.get("category") or removed_item.get("category"),
        "line_total": added_item.get("line_total") or removed_item.get("line_total"),
        "seen_in_other_receipt": added_item.get("seen_in_other_receipt"),
        "evidence_receipt_count": len(evidence_receipts),
        "evidence_receipts_redacted": bool(evidence_receipts),
        "structure_similarity": _safe_float(structure.get("score")),
    }
    if nearest_key:
        result["nearest_real_receipt_available"] = True
        result["nearest_real_receipt_key_redacted"] = True
    if candidate_quality:
        result["candidate_quality"] = candidate_quality
    selection_evidence = _compact_selection_evidence(metadata.get("selection_evidence"))
    if selection_evidence:
        result["selection_evidence"] = selection_evidence
    source_lineage = _candidate_source_lineage(candidate)
    if source_lineage:
        result["source_lineage"] = source_lineage
    compact_preview = _compact_synthetic_receipt_preview(preview)
    if compact_preview:
        result["receipt_preview"] = compact_preview
    compact_accuracy = _compact_synthesis_accuracy_evidence(accuracy_evidence)
    if compact_accuracy:
        result["accuracy_evidence"] = compact_accuracy
    if replacement:
        result.update(
            {
                "field_label": replacement.get("label"),
                "old_text": replacement.get("old_text"),
                "new_text": replacement.get("new_text"),
                "field_format": replacement.get("format"),
                "field_observed_count": _safe_int(
                    mutable_evidence.get("observed_count")
                ),
            }
        )
    return result


def _compact_synthetic_receipt_preview(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    raw_text = str(value.get("text") or "").strip()
    lines = [
        line
        for line in value.get("lines") or []
        if isinstance(line, dict) and str(line.get("text") or "").strip()
    ]
    if not raw_text and not lines:
        return {}
    compact_lines: list[dict[str, Any]] = []
    for line in lines[:6]:
        compact_lines.append(
            {
                "line_number": _safe_int(line.get("line_number")),
                "text": _clip_text(str(line.get("text") or ""), 96),
                "role": line.get("role"),
                "synthetic_insert": line.get("synthetic_insert") is True,
                "modified_labels": list(line.get("modified_labels") or [])[:4],
            }
        )
    text = raw_text or "\n".join(str(line.get("text") or "") for line in compact_lines)
    return {
        "coordinate_system": value.get("coordinate_system"),
        "line_count": _safe_int(value.get("line_count")),
        "token_count": _safe_int(value.get("token_count")),
        "truncated": value.get("truncated") is True or len(lines) > len(compact_lines),
        "text": _clip_text(text, 420),
        "lines": compact_lines,
    }


def _compact_synthesis_accuracy_evidence(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    catalog_grounding = value.get("catalog_grounding")
    category_placement = _compact_category_placement(value.get("category_placement"))
    removal_context = _compact_removal_context(value.get("removal_context"))
    layout_integrity = _compact_layout_integrity_evidence(value.get("layout_integrity"))
    structure_similarity = _compact_structure_accuracy_evidence(
        value.get("structure_similarity")
    )
    result = {
        "operation": value.get("operation"),
        "checks": [str(check) for check in (value.get("checks") or [])[:8]],
        "changed_text": value.get("changed_text"),
        "label": value.get("label"),
        "old_text": value.get("old_text"),
        "new_text": value.get("new_text"),
        "category": value.get("category"),
        "tax_delta": value.get("tax_delta"),
        "catalog_grounding": _compact_catalog_grounding_evidence(catalog_grounding),
        "category_placement": category_placement,
        "removal_context": removal_context,
        "layout_integrity": layout_integrity,
        "structure_similarity": structure_similarity,
    }
    return {key: item for key, item in result.items() if item not in (None, "", [], {})}


def _compact_category_placement(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = {
        "category": value.get("category"),
        "insert_y": _safe_float(value.get("insert_y")),
        "line_step": _safe_int(value.get("line_step")),
        "shifted_lower_lines_by": _safe_int(value.get("shifted_lower_lines_by")),
        "shifted_line_count": _safe_int(value.get("shifted_line_count")),
        "shifted_lower_line_shift_min": _safe_int(
            value.get("shifted_lower_line_shift_min")
        ),
        "shifted_lower_line_shift_max": _safe_int(
            value.get("shifted_lower_line_shift_max")
        ),
        "category_item_count_before": _safe_int(
            value.get("category_item_count_before")
        ),
        "nearest_category_item_y": _safe_float(value.get("nearest_category_item_y")),
        "nearest_lower_line_y": _safe_float(value.get("nearest_lower_line_y")),
        "same_category_section": (
            value.get("same_category_section")
            if isinstance(value.get("same_category_section"), bool)
            else None
        ),
        "selection_reason": _clip_text(str(value.get("selection_reason") or ""), 180),
        "base_receipt_has_category": (
            value.get("base_receipt_has_category")
            if isinstance(value.get("base_receipt_has_category"), bool)
            else None
        ),
        "category_seen_count": _safe_int(value.get("category_seen_count")),
        "category_heading_seen_count": _safe_int(
            value.get("category_heading_seen_count")
        ),
        "category_alignment": value.get("category_alignment"),
    }
    return {
        key: item for key, item in result.items() if item not in (None, "", [], {})
    }


def _compact_removal_context(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = {
        "category": value.get("category"),
        "removed_y": _safe_float(value.get("removed_y")),
        "line_step": _safe_int(value.get("line_step")),
        "shifted_lower_lines_by": _safe_int(value.get("shifted_lower_lines_by")),
        "shifted_line_count": _safe_int(value.get("shifted_line_count")),
        "shifted_lower_line_shift_min": _safe_int(
            value.get("shifted_lower_line_shift_min")
        ),
        "shifted_lower_line_shift_max": _safe_int(
            value.get("shifted_lower_line_shift_max")
        ),
        "category_item_count_before": _safe_int(
            value.get("category_item_count_before")
        ),
        "category_item_count_after": _safe_int(
            value.get("category_item_count_after")
        ),
        "selection_reason": _clip_text(str(value.get("selection_reason") or ""), 180),
    }
    return {
        key: item for key, item in result.items() if item not in (None, "", [], {})
    }


def _compact_catalog_grounding_evidence(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    product_keys = _unique_source_keys(value.get("product_seen_outside_base"))
    category_keys = _unique_source_keys(value.get("category_seen_in_receipts"))
    product_redacted = (
        value.get("product_seen_outside_base_redacted")
        if isinstance(value.get("product_seen_outside_base_redacted"), bool)
        else bool(product_keys)
    )
    category_redacted = (
        value.get("category_seen_in_receipts_redacted")
        if isinstance(value.get("category_seen_in_receipts_redacted"), bool)
        else bool(category_keys)
    )
    result = {
        "product_observed_count": _safe_int(value.get("product_observed_count")),
        "product_seen_receipt_count": _safe_int(
            value.get("product_seen_receipt_count")
        ),
        "product_seen_outside_base_count": _safe_int(
            value.get("product_seen_outside_base_count")
        )
        or len(product_keys),
        "product_seen_outside_base_redacted": product_redacted,
        "category": value.get("category"),
        "category_seen_count": _safe_int(value.get("category_seen_count")),
        "category_heading_seen_count": _safe_int(
            value.get("category_heading_seen_count")
        ),
        "category_seen_receipt_count": _safe_int(
            value.get("category_seen_receipt_count")
        )
        or len(category_keys),
        "category_seen_in_receipts_redacted": category_redacted,
    }
    return {key: item for key, item in result.items() if item not in (None, "", [], {})}


def _compact_layout_integrity_evidence(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = {
        "score": _safe_float(value.get("score")),
        "passed": (
            value.get("passed") if isinstance(value.get("passed"), bool) else None
        ),
        "line_count": _safe_int(value.get("line_count")),
        "word_count": _safe_int(value.get("word_count")),
        "overlap_pair_count": _safe_int(value.get("overlap_pair_count")),
        "out_of_bounds_word_count": _safe_int(value.get("out_of_bounds_word_count")),
        "invalid_word_box_count": _safe_int(value.get("invalid_word_box_count")),
        "line_order_valid": (
            value.get("line_order_valid")
            if isinstance(value.get("line_order_valid"), bool)
            else None
        ),
    }
    for key in (
        "overlap_examples",
        "out_of_bounds_examples",
        "invalid_word_examples",
    ):
        examples = value.get(key)
        if isinstance(examples, list) and examples:
            result[key] = examples[:3]
    return {key: item for key, item in result.items() if item not in (None, "", [], {})}


def _compact_structure_accuracy_evidence(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    components = {
        str(key): score
        for key, raw_score in (value.get("components") or {}).items()
        if (score := _safe_float(raw_score)) is not None
    }
    shape_deltas = {
        str(key): delta
        for key, raw_delta in (value.get("shape_deltas") or {}).items()
        if (delta := _safe_float(raw_delta)) is not None
    }
    match_summary = value.get("match_summary")
    compact_match_summary = None
    if isinstance(match_summary, dict):
        compact_match_summary = {
            "matched_components": [
                str(item)
                for item in (match_summary.get("matched_components") or [])[:8]
            ],
            "weak_components": [
                str(item) for item in (match_summary.get("weak_components") or [])[:8]
            ],
            "shape_checks": [
                str(item) for item in (match_summary.get("shape_checks") or [])[:8]
            ],
        }
    real_baseline = value.get("real_baseline_comparison")
    compact_real_baseline = None
    if isinstance(real_baseline, dict):
        within_real_range = real_baseline.get("within_real_score_range")
        compact_real_baseline = {
            "baseline_receipt_count": _safe_int(
                real_baseline.get("baseline_receipt_count")
            ),
            "baseline_pair_count": _safe_int(real_baseline.get("baseline_pair_count")),
            "candidate_score": _safe_float(real_baseline.get("candidate_score")),
            "baseline_avg": _safe_float(real_baseline.get("baseline_avg")),
            "baseline_min": _safe_float(real_baseline.get("baseline_min")),
            "baseline_max": _safe_float(real_baseline.get("baseline_max")),
            "within_real_score_range": (
                within_real_range if isinstance(within_real_range, bool) else None
            ),
            "delta_from_avg": _safe_float(real_baseline.get("delta_from_avg")),
            "delta_from_min": _safe_float(real_baseline.get("delta_from_min")),
        }
        compact_real_baseline = {
            key: item for key, item in compact_real_baseline.items() if item is not None
        }
    result = {
        "score": _safe_float(value.get("score")),
        "components": components,
        "shape_deltas": shape_deltas,
        "match_summary": compact_match_summary,
        "real_baseline_comparison": compact_real_baseline,
    }
    nearest_available = value.get("nearest_real_receipt_available") is True or bool(
        value.get("nearest_real_receipt_key")
    )
    nearest_redacted = value.get("nearest_real_receipt_key_redacted") is True or bool(
        value.get("nearest_real_receipt_key")
    )
    if nearest_available:
        result["nearest_real_receipt_available"] = True
    if nearest_redacted:
        result["nearest_real_receipt_key_redacted"] = True
    return {key: item for key, item in result.items() if item not in (None, "", [], {})}


def _clip_text(value: str, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


def _field_replacement_label(candidate: Dict[str, Any]) -> str | None:
    metadata = candidate.get("metadata") or {}
    replacement = metadata.get("field_replacement") or {}
    label = replacement.get("label")
    if not label:
        return None
    return str(label)


def _summarize_field_replacements(candidates: List[Any]) -> Dict[str, int]:
    counts: Counter[str] = Counter()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        label = _field_replacement_label(candidate)
        if label:
            counts[label] += 1
    return dict(counts)


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
