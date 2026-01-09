"""Helper functions for trace processing.

This module provides reusable utilities for common trace operations:
- Parent-child trace mapping via TraceIndex
- Metadata extraction
- Decision counting
- S3 result loading
- Timing calculations
"""

import json
import logging
from datetime import datetime
from typing import Any

from receipt_langsmith.entities.visualization import (
    DecisionCounts,
    EvaluatorResult,
    GeometricResult,
    ReceiptIdentifier,
)

logger = logging.getLogger(__name__)


class TraceIndex:
    """Index of traces by parent ID for efficient child lookups.

    Builds a parent-child mapping from a flat list of traces once,
    then provides O(1) child lookups.

    Args:
        traces: List of raw trace dicts from Parquet.
        parent_name_filter: Only include parents with this name.

    Example:
        ```python
        index = TraceIndex(traces)
        for parent in index.parents:
            children = index.get_children_by_name(parent["id"])
            currency = children.get("EvaluateCurrencyLabels")
        ```
    """

    def __init__(
        self,
        traces: list[dict[str, Any]],
        parent_name_filter: str = "ReceiptEvaluation",
    ):
        self.parents: list[dict[str, Any]] = []
        self.children_by_parent: dict[str, list[dict[str, Any]]] = {}

        for trace in traces:
            parent_id = trace.get("parent_run_id")
            if parent_id:
                if parent_id not in self.children_by_parent:
                    self.children_by_parent[parent_id] = []
                self.children_by_parent[parent_id].append(trace)
            elif trace.get("name") == parent_name_filter:
                self.parents.append(trace)

    def get_children(self, parent_id: str) -> list[dict[str, Any]]:
        """Get children for a parent trace.

        Args:
            parent_id: Parent trace ID.

        Returns:
            List of child traces (empty if none).
        """
        return self.children_by_parent.get(parent_id, [])

    def get_children_by_name(
        self, parent_id: str
    ) -> dict[str, dict[str, Any]]:
        """Get children mapped by name for O(1) lookup.

        Args:
            parent_id: Parent trace ID.

        Returns:
            Dict mapping trace name to trace dict.
        """
        children = self.get_children(parent_id)
        return {
            name: c for c in children if (name := c.get("name")) is not None
        }


def extract_metadata(trace: dict[str, Any]) -> dict[str, Any]:
    """Extract metadata from extra.metadata path.

    Handles the common pattern: trace["extra"]["metadata"]
    with safe fallbacks for missing keys.

    Args:
        trace: Raw trace dict.

    Returns:
        Metadata dict (may be empty).
    """
    extra = trace.get("extra", {}) or {}
    return extra.get("metadata", {}) or {}


def build_receipt_identifier(
    metadata: dict[str, Any],
    parent_id: str = "",
) -> ReceiptIdentifier:
    """Build ReceiptIdentifier from metadata dict.

    Args:
        metadata: Dict from extract_metadata().
        parent_id: Parent trace ID for execution_id fallback.

    Returns:
        ReceiptIdentifier with fields populated.
    """
    return ReceiptIdentifier(
        image_id=metadata.get("image_id"),
        receipt_id=metadata.get("receipt_id"),
        merchant_name=metadata.get("merchant_name", "Unknown"),
        execution_id=metadata.get(
            "execution_id",
            str(parent_id)[:8] if parent_id else "",
        ),
    )


def count_decisions(decisions: list[dict[str, Any]]) -> DecisionCounts:
    """Count VALID/INVALID/NEEDS_REVIEW decisions.

    Handles both nested (llm_review.decision) and flat (decision) formats.

    Args:
        decisions: List of decision dicts.

    Returns:
        DecisionCounts with totals.
    """
    counts = DecisionCounts()

    for d in decisions:
        llm_review = d.get("llm_review", {}) or {}
        decision = llm_review.get("decision", d.get("decision", ""))

        if decision == "VALID":
            counts.VALID += 1
        elif decision == "INVALID":
            counts.INVALID += 1
        elif decision == "NEEDS_REVIEW":
            counts.NEEDS_REVIEW += 1

    return counts


def is_all_needs_review(decisions: list[dict[str, Any]]) -> bool:
    """Check if all decisions are NEEDS_REVIEW (parsing failures).

    Args:
        decisions: List of decision dicts.

    Returns:
        True if all decisions are NEEDS_REVIEW.
    """
    if not decisions:
        return False
    counts = count_decisions(decisions)
    return counts.NEEDS_REVIEW == len(decisions)


def parse_datetime(value: Any) -> datetime | None:
    """Parse datetime from various formats.

    Args:
        value: Datetime value (datetime object, ISO string, or None).

    Returns:
        Parsed datetime or None if parsing fails.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def get_duration_seconds(trace: dict[str, Any]) -> float | None:
    """Extract duration from trace start/end times.

    Args:
        trace: Trace dict with start_time and end_time.

    Returns:
        Duration in seconds, or None if times unavailable.
    """
    if not trace:
        return None

    start = parse_datetime(trace.get("start_time"))
    end = parse_datetime(trace.get("end_time"))

    if start and end:
        return (end - start).total_seconds()
    return None


def get_relative_timing(
    trace: dict[str, Any],
    parent_start: datetime | None,
) -> tuple[int, int]:
    """Get timing relative to parent start.

    Args:
        trace: Child trace dict.
        parent_start: Parent trace start time.

    Returns:
        Tuple of (start_ms, duration_ms) relative to parent.
    """
    start = parse_datetime(trace.get("start_time"))
    end = parse_datetime(trace.get("end_time"))

    if not (start and end):
        return (0, 0)

    duration_ms = int((end - start).total_seconds() * 1000)

    start_ms = 0
    if parent_start and start:
        start_ms = max(0, int((start - parent_start).total_seconds() * 1000))

    return (start_ms, duration_ms)


def load_s3_result(
    s3_client: Any,
    bucket: str,
    result_type: str,
    execution_id: str,
    image_id: str,
    receipt_id: int,
) -> dict[str, Any] | None:
    """Load evaluation result from S3 batch bucket.

    Args:
        s3_client: Boto3 S3 client.
        bucket: S3 bucket name.
        result_type: Type folder (currency, metadata, financial, results).
        execution_id: Step Function execution ID.
        image_id: Receipt image ID.
        receipt_id: Receipt ID within image.

    Returns:
        Parsed JSON dict or None if not found.
    """
    key = f"{result_type}/{execution_id}/{image_id}_{receipt_id}.json"

    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except Exception:
        return None


def build_evaluator_result(
    s3_result: dict[str, Any] | None,
    decisions_key: str = "all_decisions",
) -> EvaluatorResult:
    """Build EvaluatorResult from S3 result dict.

    Args:
        s3_result: Result dict from load_s3_result().
        decisions_key: Key for decisions list (all_decisions or
            reviewed_issues).

    Returns:
        EvaluatorResult with populated fields.
    """
    if not s3_result:
        return EvaluatorResult()

    decisions = s3_result.get(decisions_key, [])
    return EvaluatorResult(
        decisions=count_decisions(decisions),
        all_decisions=decisions,
        duration_seconds=s3_result.get("duration_seconds", 0.0),
    )


def build_geometric_result(
    s3_result: dict[str, Any] | None,
) -> GeometricResult:
    """Build GeometricResult from S3 result dict.

    Args:
        s3_result: Result dict from load_s3_result().

    Returns:
        GeometricResult with populated fields.
    """
    if not s3_result:
        return GeometricResult()

    return GeometricResult(
        issues_found=s3_result.get("issues_found", 0),
        issues=s3_result.get("issues", []),
        duration_seconds=s3_result.get("duration_seconds", 0.0),
    )


def build_geometric_from_trace(
    trace: dict[str, Any],
) -> GeometricResult:
    """Build GeometricResult from EvaluateLabels trace outputs.

    Args:
        trace: EvaluateLabels trace dict.

    Returns:
        GeometricResult with issues from trace.
    """
    if not trace:
        return GeometricResult()

    outputs = trace.get("outputs", {}) or {}
    issues = outputs.get("issues", [])

    return GeometricResult(
        issues_found=len(issues),
        issues=issues,
        duration_seconds=get_duration_seconds(trace) or 0.1,
    )


def get_decisions_from_trace(
    children_by_name: dict[str, dict[str, Any]],
    trace_name: str,
    decisions_key: str = "all_decisions",
) -> list[dict[str, Any]]:
    """Extract decisions list from a named child trace.

    Args:
        children_by_name: Dict mapping trace name to trace dict.
        trace_name: Name of trace to extract from.
        decisions_key: Key for decisions in outputs.

    Returns:
        List of decisions (empty if not found).
    """
    child = children_by_name.get(trace_name, {})
    outputs = child.get("outputs", {}) or {}
    return outputs.get(decisions_key, [])
