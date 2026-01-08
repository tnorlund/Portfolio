"""Parquet reader for LangSmith bulk-exported traces.

This module reads traces from Parquet files exported by LangSmith's bulk export
feature and provides the same interface as the queries module but without
rate limit issues.
"""

import json
import logging
from datetime import datetime
from io import BytesIO
from typing import Any

import boto3
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


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


def read_traces_from_parquet(bucket: str, prefix: str = "traces/") -> list[dict]:
    """Read all traces from exported Parquet files in S3.

    Args:
        bucket: S3 bucket name containing Parquet exports
        prefix: S3 prefix for Parquet files (default: "traces/")

    Returns:
        List of trace dicts with id, name, outputs, metadata, etc.
    """
    s3 = boto3.client("s3")
    traces = []

    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".parquet"):
                    continue

                logger.debug(f"Reading Parquet file: s3://{bucket}/{key}")

                try:
                    response = s3.get_object(Bucket=bucket, Key=key)
                    table = pq.read_table(BytesIO(response["Body"].read()))
                    raw_traces = table.to_pylist()

                    # Parse JSON string fields (outputs, inputs, extra)
                    for trace in raw_traces:
                        trace["outputs"] = _parse_json_field(trace.get("outputs"))
                        trace["inputs"] = _parse_json_field(trace.get("inputs"))
                        trace["extra"] = _parse_json_field(trace.get("extra"))

                    traces.extend(raw_traces)
                except Exception:
                    logger.exception(f"Error reading Parquet file: {key}")
                    continue

        logger.info(f"Read {len(traces)} traces from {bucket}/{prefix}")
        return traces

    except Exception:
        logger.exception(f"Error listing Parquet files in {bucket}/{prefix}")
        return []


def _parse_datetime(value: Any) -> datetime | None:
    """Parse datetime from various formats."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            # Handle ISO format with or without timezone
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


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

    # Separate parents and children
    parents = []
    children_by_parent: dict[str, list[dict]] = {}

    for trace in traces:
        parent_id = trace.get("parent_run_id")
        if parent_id:
            # This is a child trace
            if parent_id not in children_by_parent:
                children_by_parent[parent_id] = []
            children_by_parent[parent_id].append(trace)
        elif trace.get("name") == "ReceiptEvaluation":
            parents.append(trace)

    logger.info(f"Found {len(parents)} ReceiptEvaluation traces")

    # Process each parent with its children
    results = []
    for parent in parents:
        parent_id = parent.get("id")
        # Metadata is in extra.metadata for LangSmith exports
        extra = parent.get("extra", {}) or {}
        metadata = extra.get("metadata", {}) or {}
        children = children_by_parent.get(parent_id, [])

        # Build children lookup by name
        children_by_name = {c.get("name"): c for c in children}

        # Parse parent start time for relative timing
        parent_start = _parse_datetime(parent.get("start_time"))

        # Get timing from each evaluator
        timing = {}
        for name in [
            "EvaluateCurrencyLabels",
            "EvaluateMetadataLabels",
            "ValidateFinancialMath",
            "EvaluateLabels",
        ]:
            child = children_by_name.get(name)
            if child:
                start = _parse_datetime(child.get("start_time"))
                end = _parse_datetime(child.get("end_time"))

                if start and end:
                    duration_ms = (end - start).total_seconds() * 1000

                    # Calculate relative start (ms from parent start)
                    start_ms = 0
                    if parent_start and start:
                        start_ms = max(
                            0, (start - parent_start).total_seconds() * 1000
                        )

                    timing[name] = {
                        "start_ms": int(start_ms),
                        "duration_ms": int(duration_ms),
                    }

        # Get decisions from outputs
        currency_child = children_by_name.get("EvaluateCurrencyLabels", {})
        currency_outputs = currency_child.get("outputs", {}) or {}

        metadata_child = children_by_name.get("EvaluateMetadataLabels", {})
        metadata_outputs = metadata_child.get("outputs", {}) or {}

        financial_child = children_by_name.get("ValidateFinancialMath", {})
        financial_outputs = financial_child.get("outputs", {}) or {}

        currency_decisions = currency_outputs.get("all_decisions", [])
        metadata_decisions = metadata_outputs.get("all_decisions", [])
        financial_decisions = financial_outputs.get("all_decisions", [])

        # Only include receipts with some decisions
        has_decisions = (
            len(currency_decisions) > 0
            or len(metadata_decisions) > 0
            or len(financial_decisions) > 0
        )

        if has_decisions or timing:
            results.append({
                "run_id": parent_id,
                "image_id": metadata.get("image_id"),
                "receipt_id": metadata.get("receipt_id"),
                "merchant_name": metadata.get("merchant_name", "Unknown"),
                "execution_id": metadata.get(
                    "execution_id", str(parent_id)[:8] if parent_id else ""
                ),
                "timing": timing,
                "currency_decisions": currency_decisions,
                "metadata_decisions": metadata_decisions,
                "financial_decisions": financial_decisions,
            })

    logger.info(f"Found {len(results)} receipts with decisions/timing")
    return results


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

    # Build parent-child mapping
    parents = []
    children_by_parent: dict[str, list[dict]] = {}

    for trace in traces:
        parent_id = trace.get("parent_run_id")
        if parent_id:
            if parent_id not in children_by_parent:
                children_by_parent[parent_id] = []
            children_by_parent[parent_id].append(trace)
        elif trace.get("name") == "ReceiptEvaluation":
            parents.append(trace)

    # Anomaly types to look for
    anomaly_types = {
        "geometric_anomaly",
        "position_anomaly",
        "constellation_anomaly",
    }

    results = []
    for parent in parents:
        parent_id = parent.get("id")
        # Metadata is in extra.metadata for LangSmith exports
        extra = parent.get("extra", {}) or {}
        metadata = extra.get("metadata", {}) or {}
        children = children_by_parent.get(parent_id, [])

        # Find EvaluateLabels child
        for child in children:
            if child.get("name") != "EvaluateLabels":
                continue

            outputs = child.get("outputs", {}) or {}
            issues = outputs.get("issues", [])
            flagged_words = outputs.get("flagged_words", [])

            # Filter for geometric anomaly types
            geometric_issues = [
                i for i in issues if i.get("type") in anomaly_types
            ]

            if geometric_issues:
                results.append({
                    "run_id": parent_id,
                    "image_id": metadata.get("image_id"),
                    "receipt_id": metadata.get("receipt_id"),
                    "merchant_name": metadata.get("merchant_name", "Unknown"),
                    "issues": geometric_issues,
                    "all_issues": issues,
                    "flagged_words": flagged_words,
                    "execution_id": metadata.get(
                        "execution_id", str(parent_id)[:8] if parent_id else ""
                    ),
                })

    logger.info(f"Found {len(results)} receipts with anomalies")
    return results


def _load_s3_result(
    batch_bucket: str,
    execution_id: str,
    result_type: str,
    s3_key_base: str,
) -> dict | None:
    """Load a result file from S3.

    Args:
        batch_bucket: S3 bucket containing Step Function results
        execution_id: Step Function execution ID
        result_type: Type of result (currency, metadata, financial, reviewed)
        s3_key_base: Base key like "image_id_receipt_id.json"

    Returns:
        Parsed JSON dict or None if not found
    """
    s3 = boto3.client("s3")
    s3_key = f"{result_type}/{execution_id}/{s3_key_base}"

    try:
        response = s3.get_object(Bucket=batch_bucket, Key=s3_key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except Exception:
        # File doesn't exist or error reading
        return None


def _get_trace_key(trace: dict) -> tuple[str, int] | None:
    """Extract (image_id, receipt_id) from trace metadata."""
    extra = trace.get("extra", {}) or {}
    metadata = extra.get("metadata", {}) or {}
    image_id = metadata.get("image_id")
    receipt_id = metadata.get("receipt_id")
    if image_id and receipt_id is not None:
        return (image_id, int(receipt_id))
    return None


def find_visualization_receipts_from_parquet(
    bucket: str,
    prefix: str = "traces/",
    max_receipts: int = 20,
    batch_bucket: str | None = None,
) -> list[dict[str, Any]]:
    """Extract receipt data for scanner visualization from LangSmith Parquet exports.

    This function extracts all data needed for the LabelEvaluatorVisualization
    component, including evaluator decisions, timing, geometric issues, and
    line item discovery duration.

    Note: Evaluation results (currency, metadata, financial) are loaded from S3
    rather than LangSmith traces because the trace hierarchy is incomplete.
    Traces from different Step Function Lambdas are matched by image_id/receipt_id.

    Args:
        bucket: S3 bucket name containing Parquet exports
        prefix: S3 prefix for Parquet files
        max_receipts: Maximum number of receipts to return (prioritizes those with review data)
        batch_bucket: S3 bucket containing Step Function results (currency, metadata, etc.)
                     If not provided, defaults to label-evaluator-dev-batch-bucket

    Returns:
        List of receipt visualization dicts with:
        - image_id, receipt_id, merchant_name, execution_id
        - geometric: {issues_found, issues[], duration_seconds}
        - currency: {decisions{}, all_decisions[], duration_seconds}
        - metadata: {decisions{}, all_decisions[], duration_seconds}
        - financial: {decisions{}, all_decisions[], duration_seconds}
        - review: {decisions{}, all_decisions[], duration_seconds} or None
        - line_item_duration_seconds (from DiscoverPatterns span)
    """
    # Default batch bucket for Step Function results
    if not batch_bucket:
        batch_bucket = "label-evaluator-dev-batch-bucket-0c95650"
    traces = read_traces_from_parquet(bucket, prefix)

    if not traces:
        logger.warning("No traces found in Parquet export")
        return []

    # Index traces by (image_id, receipt_id) and name for cross-Lambda matching
    traces_by_key_and_name: dict[tuple[str, int], dict[str, dict]] = {}
    children_by_parent: dict[str, list[dict]] = {}

    for trace in traces:
        parent_id = trace.get("parent_run_id")
        if parent_id:
            if parent_id not in children_by_parent:
                children_by_parent[parent_id] = []
            children_by_parent[parent_id].append(trace)

        # Index by (image_id, receipt_id) for cross-Lambda matching
        key = _get_trace_key(trace)
        if key:
            if key not in traces_by_key_and_name:
                traces_by_key_and_name[key] = {}
            name = trace.get("name")
            if name:
                # Keep the most recent trace if duplicates exist
                traces_by_key_and_name[key][name] = trace

    # Find ReceiptEvaluation traces as the base
    parents = [t for t in traces if t.get("name") == "ReceiptEvaluation"]
    logger.info(f"Found {len(parents)} ReceiptEvaluation traces")

    # Process each receipt
    with_review = []
    without_review = []

    for parent in parents:
        parent_id = parent.get("id")
        extra = parent.get("extra", {}) or {}
        metadata = extra.get("metadata", {}) or {}
        receipt_key = _get_trace_key(parent)

        # Get children of ReceiptEvaluation for geometric evaluation
        children = children_by_parent.get(parent_id, [])
        children_by_name: dict[str, dict] = {}
        for c in children:
            name = c.get("name")
            if name:
                children_by_name[name] = c

        # Get traces matching this receipt from other Lambdas
        receipt_traces = traces_by_key_and_name.get(receipt_key, {}) if receipt_key else {}

        # Extract geometric issues from EvaluateLabels (child of ReceiptEvaluation)
        evaluate_labels = children_by_name.get("EvaluateLabels", {})
        evaluate_labels_outputs = evaluate_labels.get("outputs", {}) or {}
        geometric_issues = evaluate_labels_outputs.get("issues", [])
        geometric_duration = _get_duration_seconds(evaluate_labels)

        # Get execution_id and batch_bucket from metadata to fetch S3 results
        execution_id = metadata.get("execution_id", "")
        image_id = metadata.get("image_id")
        receipt_id = metadata.get("receipt_id")

        # Try to get decisions from S3 results if available
        # The Step Function stores detailed results in S3 rather than LangSmith traces
        currency_decisions = []
        currency_duration = 0
        metadata_decisions = []
        metadata_duration = 0
        financial_decisions = []
        financial_duration = 0
        review_decisions = []
        review_duration = 0

        if execution_id and image_id and receipt_id is not None:
            s3_key_base = f"{image_id}_{receipt_id}.json"

            # Currency results
            currency_result = _load_s3_result(
                batch_bucket, execution_id, "currency", s3_key_base
            )
            if currency_result:
                currency_decisions = currency_result.get("all_decisions", [])
                currency_duration = currency_result.get("duration_seconds", 0)

            # Metadata results
            metadata_result = _load_s3_result(
                batch_bucket, execution_id, "metadata", s3_key_base
            )
            if metadata_result:
                metadata_decisions = metadata_result.get("all_decisions", [])
                metadata_duration = metadata_result.get("duration_seconds", 0)

            # Financial results
            financial_result = _load_s3_result(
                batch_bucket, execution_id, "financial", s3_key_base
            )
            if financial_result:
                financial_decisions = financial_result.get("all_decisions", [])
                financial_duration = financial_result.get("duration_seconds", 0)

            # Review results (LLM review of geometric issues)
            review_result = _load_s3_result(
                batch_bucket, execution_id, "reviewed", s3_key_base
            )
            if review_result:
                review_decisions = review_result.get("reviewed_issues", [])
                review_duration = review_result.get("duration_seconds", 0)

        # Extract line item duration from DiscoverPatterns span (child of ReceiptEvaluation)
        discover_patterns = children_by_name.get("DiscoverPatterns", {})
        line_item_duration = _get_duration_seconds(discover_patterns)

        # Build decision counts
        def count_decisions(decisions: list) -> dict[str, int]:
            counts = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}
            for d in decisions:
                llm_review = d.get("llm_review", {}) or {}
                decision = llm_review.get("decision", d.get("decision", ""))
                if decision in counts:
                    counts[decision] += 1
            return counts

        # Skip receipts with no evaluation data
        has_data = (
            len(currency_decisions) > 0
            or len(metadata_decisions) > 0
            or len(financial_decisions) > 0
            or len(geometric_issues) > 0
        )

        if not has_data:
            continue

        # Skip receipts where all decisions are NEEDS_REVIEW (parsing failures)
        currency_counts = count_decisions(currency_decisions)
        metadata_counts = count_decisions(metadata_decisions)

        if currency_decisions and currency_counts["NEEDS_REVIEW"] == len(currency_decisions):
            logger.debug(
                "Skipping %s_%s: all currency decisions are NEEDS_REVIEW",
                metadata.get("image_id"),
                metadata.get("receipt_id"),
            )
            continue

        if metadata_decisions and metadata_counts["NEEDS_REVIEW"] == len(metadata_decisions):
            logger.debug(
                "Skipping %s_%s: all metadata decisions are NEEDS_REVIEW",
                metadata.get("image_id"),
                metadata.get("receipt_id"),
            )
            continue

        # Build the visualization receipt object
        receipt = {
            "image_id": metadata.get("image_id"),
            "receipt_id": metadata.get("receipt_id"),
            "merchant_name": metadata.get("merchant_name", "Unknown"),
            "execution_id": metadata.get(
                "execution_id", str(parent_id)[:8] if parent_id else ""
            ),
            "geometric": {
                "issues_found": len(geometric_issues),
                "issues": geometric_issues,
                "duration_seconds": geometric_duration or 0.1,
            },
            "currency": {
                "decisions": currency_counts,
                "all_decisions": currency_decisions,
                "duration_seconds": currency_duration or 0,
            },
            "metadata": {
                "decisions": metadata_counts,
                "all_decisions": metadata_decisions,
                "duration_seconds": metadata_duration or 0,
            },
            "financial": {
                "decisions": count_decisions(financial_decisions),
                "all_decisions": financial_decisions,
                "duration_seconds": financial_duration or 0,
            },
            "review": {
                "decisions": count_decisions(review_decisions),
                "all_decisions": review_decisions,
                "duration_seconds": review_duration or 0,
            } if review_decisions else None,
            "line_item_duration_seconds": line_item_duration,
        }

        # Prioritize receipts with review data
        if review_decisions:
            with_review.append(receipt)
        else:
            without_review.append(receipt)

    # Combine with priority: review data first
    results = with_review + without_review

    logger.info(
        f"Found {len(results)} visualization receipts "
        f"({len(with_review)} with review, {len(without_review)} without)"
    )

    return results[:max_receipts]


def _get_duration_seconds(trace: dict) -> float | None:
    """Extract duration in seconds from a trace's start/end times."""
    if not trace:
        return None

    start = _parse_datetime(trace.get("start_time"))
    end = _parse_datetime(trace.get("end_time"))

    if start and end:
        return (end - start).total_seconds()

    return None
