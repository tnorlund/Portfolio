"""Parquet reader for LangSmith bulk-exported traces.

This module reads traces from Parquet files exported by LangSmith's bulk export
feature and provides the same interface as the queries module but without
rate limit issues.
"""

import logging
from datetime import datetime
from io import BytesIO
from typing import Any

import boto3
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


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
                    traces.extend(table.to_pylist())
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
        metadata = parent.get("metadata", {}) or {}
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
        metadata = parent.get("metadata", {}) or {}
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


def find_visualization_receipts_from_parquet(
    bucket: str,
    prefix: str = "traces/",
    max_receipts: int = 20,
) -> list[dict[str, Any]]:
    """Extract receipt data for scanner visualization from LangSmith Parquet exports.

    This function extracts all data needed for the LabelEvaluatorVisualization
    component, including evaluator decisions, timing, geometric issues, and
    line item discovery duration.

    Args:
        bucket: S3 bucket name containing Parquet exports
        prefix: S3 prefix for Parquet files
        max_receipts: Maximum number of receipts to return (prioritizes those with review data)

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
            if parent_id not in children_by_parent:
                children_by_parent[parent_id] = []
            children_by_parent[parent_id].append(trace)
        elif trace.get("name") == "ReceiptEvaluation":
            parents.append(trace)

    logger.info(f"Found {len(parents)} ReceiptEvaluation traces")

    # Process each parent with its children
    with_review = []
    without_review = []

    for parent in parents:
        parent_id = parent.get("id")
        metadata = parent.get("metadata", {}) or {}
        children = children_by_parent.get(parent_id, [])

        # Build children lookup by name
        children_by_name: dict[str, dict] = {}
        for c in children:
            name = c.get("name")
            if name:
                children_by_name[name] = c

        # Extract geometric issues from EvaluateLabels
        evaluate_labels = children_by_name.get("EvaluateLabels", {})
        evaluate_labels_outputs = evaluate_labels.get("outputs", {}) or {}
        geometric_issues = evaluate_labels_outputs.get("issues", [])
        geometric_duration = _get_duration_seconds(evaluate_labels)

        # Extract currency decisions
        currency_child = children_by_name.get("EvaluateCurrencyLabels", {})
        currency_outputs = currency_child.get("outputs", {}) or {}
        currency_decisions = currency_outputs.get("all_decisions", [])
        currency_duration = (
            currency_outputs.get("duration_seconds")
            or _get_duration_seconds(currency_child)
        )

        # Extract metadata decisions
        metadata_child = children_by_name.get("EvaluateMetadataLabels", {})
        metadata_outputs = metadata_child.get("outputs", {}) or {}
        metadata_decisions = metadata_outputs.get("all_decisions", [])
        metadata_duration = (
            metadata_outputs.get("duration_seconds")
            or _get_duration_seconds(metadata_child)
        )

        # Extract financial decisions
        financial_child = children_by_name.get("ValidateFinancialMath", {})
        financial_outputs = financial_child.get("outputs", {}) or {}
        financial_decisions = financial_outputs.get("all_decisions", [])
        financial_duration = (
            financial_outputs.get("duration_seconds")
            or _get_duration_seconds(financial_child)
        )

        # Extract review decisions (LLMReview - only exists if geometric issues found)
        review_child = children_by_name.get("LLMReview", {})
        review_outputs = review_child.get("outputs", {}) or {}
        review_decisions = review_outputs.get("all_decisions", [])
        review_duration = (
            review_outputs.get("duration_seconds")
            or _get_duration_seconds(review_child)
        )

        # Extract line item duration from DiscoverPatterns span
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
