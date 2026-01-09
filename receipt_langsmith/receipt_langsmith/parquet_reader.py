"""Parquet reader for LangSmith bulk-exported traces.

This module reads traces from Parquet files exported by LangSmith's bulk export
feature and provides the same interface as the queries module but without
rate limit issues.
"""

import json
import logging
import os
from io import BytesIO
from typing import Any

import boto3
import pyarrow.parquet as pq

from receipt_langsmith.entities.visualization import (
    EvaluatorResult,
    EvaluatorTiming,
    GeometricResult,
    ReceiptWithAnomalies,
    ReceiptWithDecisions,
    VisualizationReceipt,
)
from receipt_langsmith.parsers.trace_helpers import (
    TraceIndex,
    build_evaluator_result,
    build_geometric_from_trace,
    build_receipt_identifier,
    extract_metadata,
    get_decisions_from_trace,
    get_duration_seconds,
    get_relative_timing,
    is_all_needs_review,
    load_s3_result,
    parse_datetime,
)

logger = logging.getLogger(__name__)


# --- Constants ---

EVALUATOR_NAMES = [
    "EvaluateCurrencyLabels",
    "EvaluateMetadataLabels",
    "ValidateFinancialMath",
    "EvaluateLabels",
]

ANOMALY_TYPES = {
    "geometric_anomaly",
    "position_anomaly",
    "constellation_anomaly",
}


# --- Core Reading ---


def _parse_json_field(value: Any) -> Any:
    """Parse JSON string field, returning empty dict if parsing fails."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def read_traces_from_parquet(
    bucket: str, prefix: str = "traces/"
) -> list[dict[str, Any]]:
    """Read all traces from exported Parquet files in S3.

    Args:
        bucket: S3 bucket name containing Parquet exports
        prefix: S3 prefix for Parquet files (default: "traces/")

    Returns:
        List of trace dicts with id, name, outputs, metadata, etc.
    """
    s3 = boto3.client("s3")
    traces: list[dict[str, Any]] = []

    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".parquet"):
                    continue

                logger.debug("Reading Parquet file: s3://%s/%s", bucket, key)

                try:
                    response = s3.get_object(Bucket=bucket, Key=key)
                    table = pq.read_table(BytesIO(response["Body"].read()))
                    raw_traces = table.to_pylist()

                    # Parse JSON string fields (outputs, inputs, extra)
                    for trace in raw_traces:
                        trace["outputs"] = _parse_json_field(
                            trace.get("outputs")
                        )
                        trace["inputs"] = _parse_json_field(
                            trace.get("inputs")
                        )
                        trace["extra"] = _parse_json_field(trace.get("extra"))

                    traces.extend(raw_traces)
                except Exception:
                    logger.exception("Error reading Parquet file: %s", key)
                    continue

        logger.info("Read %d traces from %s/%s", len(traces), bucket, prefix)
        return traces

    except Exception:
        logger.exception(
            "Error listing Parquet files in %s/%s", bucket, prefix
        )
        return []


# --- Receipt Extraction Functions ---


def find_receipts_with_decisions_from_parquet(
    bucket: str,
    prefix: str = "traces/",
) -> list[dict[str, Any]]:
    """Find receipts with LLM decisions from Parquet export.

    This function reads exported traces and extracts receipt evaluations
    with timing data for visualization.

    Args:
        bucket: S3 bucket name containing Parquet exports
        prefix: S3 prefix for Parquet files

    Returns:
        List of receipt info dicts with:
        - run_id: Parent trace ID
        - image_id: Receipt image ID
        - receipt_id: Receipt ID
        - merchant_name: Merchant name
        - execution_id: Step Function execution ID
        - timing: Dict of evaluator timings
        - currency_decisions: List of currency label decisions
        - metadata_decisions: List of metadata label decisions
        - financial_decisions: List of financial validation decisions
    """
    traces = read_traces_from_parquet(bucket, prefix)
    if not traces:
        logger.warning("No traces found in Parquet export")
        return []

    index = TraceIndex(traces)
    logger.info("Found %d ReceiptEvaluation traces", len(index.parents))

    results = []
    for parent in index.parents:
        receipt = _process_decisions_receipt(parent, index)
        if receipt:
            results.append(receipt.model_dump())

    logger.info("Found %d receipts with decisions/timing", len(results))
    return results


def _process_decisions_receipt(
    parent: dict[str, Any],
    index: TraceIndex,
) -> ReceiptWithDecisions | None:
    """Process a parent trace into ReceiptWithDecisions."""
    parent_id = parent.get("id", "")
    metadata = extract_metadata(parent)
    children_by_name = index.get_children_by_name(parent_id)
    parent_start = parse_datetime(parent.get("start_time"))

    # Build timing dict
    timing = _build_evaluator_timings(children_by_name, parent_start)

    # Get decisions from outputs
    currency_decisions = get_decisions_from_trace(
        children_by_name, "EvaluateCurrencyLabels"
    )
    metadata_decisions = get_decisions_from_trace(
        children_by_name, "EvaluateMetadataLabels"
    )
    financial_decisions = get_decisions_from_trace(
        children_by_name, "ValidateFinancialMath"
    )

    # Only include receipts with some decisions or timing
    has_data = (
        currency_decisions
        or metadata_decisions
        or financial_decisions
        or timing
    )
    if not has_data:
        return None

    identifier = build_receipt_identifier(metadata, parent_id)
    return ReceiptWithDecisions(
        run_id=parent_id,
        image_id=identifier.image_id,
        receipt_id=identifier.receipt_id,
        merchant_name=identifier.merchant_name,
        execution_id=identifier.execution_id,
        timing=timing,
        currency_decisions=currency_decisions,
        metadata_decisions=metadata_decisions,
        financial_decisions=financial_decisions,
    )


def _build_evaluator_timings(
    children_by_name: dict[str, dict[str, Any]],
    parent_start: Any,
) -> dict[str, EvaluatorTiming]:
    """Build timing dict for evaluator traces."""
    timing: dict[str, EvaluatorTiming] = {}

    for name in EVALUATOR_NAMES:
        child = children_by_name.get(name)
        if child:
            start_ms, duration_ms = get_relative_timing(child, parent_start)
            if duration_ms > 0:
                timing[name] = EvaluatorTiming(
                    start_ms=start_ms, duration_ms=duration_ms
                )

    return timing


def find_receipts_with_anomalies_from_parquet(
    bucket: str,
    prefix: str = "traces/",
) -> list[dict[str, Any]]:
    """Find receipts with geometric anomalies from Parquet export.

    Args:
        bucket: S3 bucket name containing Parquet exports
        prefix: S3 prefix for Parquet files

    Returns:
        List of receipt info dicts with anomaly data
    """
    traces = read_traces_from_parquet(bucket, prefix)
    if not traces:
        return []

    index = TraceIndex(traces)

    results = []
    for parent in index.parents:
        receipt = _process_anomalies_receipt(parent, index)
        if receipt:
            results.append(receipt.model_dump())

    logger.info("Found %d receipts with anomalies", len(results))
    return results


def _process_anomalies_receipt(
    parent: dict[str, Any],
    index: TraceIndex,
) -> ReceiptWithAnomalies | None:
    """Process a parent trace into ReceiptWithAnomalies."""
    parent_id = parent.get("id", "")
    metadata = extract_metadata(parent)
    children = index.get_children(parent_id)

    # Find EvaluateLabels child
    for child in children:
        if child.get("name") != "EvaluateLabels":
            continue

        outputs = child.get("outputs", {}) or {}
        issues = outputs.get("issues", [])
        flagged_words = outputs.get("flagged_words", [])

        # Filter for geometric anomaly types
        geometric_issues = [
            i for i in issues if i.get("type") in ANOMALY_TYPES
        ]

        if geometric_issues:
            identifier = build_receipt_identifier(metadata, parent_id)
            return ReceiptWithAnomalies(
                run_id=parent_id,
                image_id=identifier.image_id,
                receipt_id=identifier.receipt_id,
                merchant_name=identifier.merchant_name,
                execution_id=identifier.execution_id,
                issues=geometric_issues,
                all_issues=issues,
                flagged_words=flagged_words,
            )

    return None


def find_visualization_receipts_from_parquet(
    bucket: str,
    prefix: str = "traces/",
    max_receipts: int = 20,
    batch_bucket: str | None = None,
) -> list[dict[str, Any]]:
    """Extract receipt data for scanner visualization from Parquet exports.

    This function extracts all data needed for the LabelEvaluatorVisualization
    component, including evaluator decisions, timing, geometric issues, and
    line item discovery duration.

    Note: Evaluation results (currency, metadata, financial) are loaded from S3
    rather than LangSmith traces because the trace hierarchy is incomplete.

    Args:
        bucket: S3 bucket name containing Parquet exports
        prefix: S3 prefix for Parquet files
        max_receipts: Maximum number of receipts to return
        batch_bucket: S3 bucket containing Step Function results

    Returns:
        List of receipt visualization dicts
    """
    batch_bucket = _resolve_batch_bucket(batch_bucket)
    traces = read_traces_from_parquet(bucket, prefix)
    if not traces:
        logger.warning("No traces found in Parquet export")
        return []

    index = TraceIndex(traces)
    logger.info("Found %d ReceiptEvaluation traces", len(index.parents))

    s3 = boto3.client("s3")
    with_review: list[dict[str, Any]] = []
    without_review: list[dict[str, Any]] = []

    for parent in index.parents:
        receipt = _process_visualization_receipt(
            parent, index, s3, batch_bucket
        )
        if receipt is None:
            continue

        receipt_dict = receipt.model_dump()
        if receipt.review:
            with_review.append(receipt_dict)
        else:
            without_review.append(receipt_dict)

    results = with_review + without_review
    logger.info(
        "Found %d visualization receipts (%d with review, %d without)",
        len(results),
        len(with_review),
        len(without_review),
    )

    return results[:max_receipts]


def _resolve_batch_bucket(batch_bucket: str | None) -> str:
    """Resolve batch bucket from parameter or environment."""
    if batch_bucket:
        return batch_bucket
    batch_bucket = os.environ.get("BATCH_BUCKET", "")
    if not batch_bucket:
        raise ValueError(
            "batch_bucket required (via parameter or BATCH_BUCKET env var)"
        )
    return batch_bucket


def _process_visualization_receipt(
    parent: dict[str, Any],
    index: TraceIndex,
    s3: Any,
    batch_bucket: str,
) -> VisualizationReceipt | None:
    """Process a single parent trace into VisualizationReceipt."""
    metadata = extract_metadata(parent)
    execution_id = metadata.get("execution_id", "")
    image_id = metadata.get("image_id")
    receipt_id = metadata.get("receipt_id")

    if not (execution_id and image_id and receipt_id is not None):
        return None

    children = index.get_children_by_name(parent.get("id", ""))
    geometric = build_geometric_from_trace(children.get("EvaluateLabels", {}))

    # Load all S3 results
    results = _load_all_evaluator_results(
        s3, batch_bucket, execution_id, image_id, receipt_id
    )

    # Check for meaningful data
    if not _has_visualization_data(geometric, results):
        return None

    # Skip parsing failures
    if _should_skip_receipt(image_id, receipt_id, results):
        return None

    identifier = build_receipt_identifier(metadata, parent.get("id", ""))
    return VisualizationReceipt(
        image_id=identifier.image_id,
        receipt_id=identifier.receipt_id,
        merchant_name=identifier.merchant_name,
        execution_id=identifier.execution_id,
        geometric=geometric,
        currency=results["currency"],
        metadata=results["metadata"],
        financial=results["financial"],
        review=results["review"] if results["review"].all_decisions else None,
        line_item_duration_seconds=get_duration_seconds(
            children.get("DiscoverPatterns", {})
        ),
    )


def _load_all_evaluator_results(
    s3: Any,
    bucket: str,
    execution_id: str,
    image_id: str,
    receipt_id: int,
) -> dict[str, EvaluatorResult]:
    """Load all evaluator results from S3."""
    return {
        "currency": _load_evaluator_from_s3(
            s3, bucket, execution_id, image_id, receipt_id, "currency"
        ),
        "metadata": _load_evaluator_from_s3(
            s3, bucket, execution_id, image_id, receipt_id, "metadata"
        ),
        "financial": _load_evaluator_from_s3(
            s3, bucket, execution_id, image_id, receipt_id, "financial"
        ),
        "review": _load_review_from_s3(
            s3, bucket, execution_id, image_id, receipt_id
        )
        or EvaluatorResult(),
    }


def _has_visualization_data(
    geometric: GeometricResult,
    results: dict[str, EvaluatorResult],
) -> bool:
    """Check if receipt has any visualization data."""
    return bool(
        results["currency"].all_decisions
        or results["metadata"].all_decisions
        or results["financial"].all_decisions
        or geometric.issues
    )


def _should_skip_receipt(
    image_id: str | None,
    receipt_id: int | None,
    results: dict[str, EvaluatorResult],
) -> bool:
    """Check if receipt should be skipped (all NEEDS_REVIEW)."""
    if is_all_needs_review(results["currency"].all_decisions):
        logger.debug(
            "Skipping %s_%s: all currency decisions are NEEDS_REVIEW",
            image_id,
            receipt_id,
        )
        return True

    if is_all_needs_review(results["metadata"].all_decisions):
        logger.debug(
            "Skipping %s_%s: all metadata decisions are NEEDS_REVIEW",
            image_id,
            receipt_id,
        )
        return True

    return False


def _load_evaluator_from_s3(
    s3: Any,
    bucket: str,
    execution_id: str,
    image_id: str,
    receipt_id: int,
    result_type: str,
) -> EvaluatorResult:
    """Load and build EvaluatorResult from S3."""
    result = load_s3_result(
        s3, bucket, result_type, execution_id, image_id, receipt_id
    )
    return build_evaluator_result(result)


def _load_review_from_s3(
    s3: Any,
    bucket: str,
    execution_id: str,
    image_id: str,
    receipt_id: int,
) -> EvaluatorResult | None:
    """Load review results from S3."""
    result = load_s3_result(
        s3, bucket, "reviewed", execution_id, image_id, receipt_id
    )
    if not result:
        return None
    return build_evaluator_result(result, decisions_key="reviewed_issues")
