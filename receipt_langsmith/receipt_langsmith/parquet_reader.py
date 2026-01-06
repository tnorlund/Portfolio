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
