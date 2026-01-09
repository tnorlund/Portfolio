"""LangSmith query helpers for receipt evaluation visualization caches.

This module provides functions to query LangSmith traces for visualization
data instead of scanning S3 buckets.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from langsmith import Client

logger = logging.getLogger(__name__)


def get_langsmith_client() -> Client:
    """Get LangSmith client from environment.

    Uses LANGCHAIN_API_KEY environment variable automatically.
    """
    return Client()


def query_recent_receipt_traces(
    project_name: str,
    hours_back: int = 168,
    limit: int = 100,
) -> list[Any]:
    """Query recent ReceiptEvaluation traces.

    Args:
        project_name: LangSmith project name (e.g., "label-evaluator-dev")
        hours_back: How many hours back to search (default 7 days)
        limit: Maximum number of traces to return

    Returns:
        List of Run objects with outputs
    """
    client = get_langsmith_client()
    start_time = datetime.now(timezone.utc) - timedelta(hours=hours_back)

    try:
        runs = client.list_runs(
            project_name=project_name,
            run_type="chain",
            filter='eq(name, "ReceiptEvaluation")',
            start_time=start_time,
            limit=limit,
        )
        # Filter to only runs with outputs
        return [run for run in runs if run.outputs]
    except Exception:
        logger.exception("Error querying LangSmith traces")
        return []


def get_child_traces(client: Client, parent_run_id: str) -> dict[str, dict]:
    """Get child traces for a receipt.

    Args:
        client: LangSmith client
        parent_run_id: Parent run ID (ReceiptEvaluation trace)

    Returns:
        Dict mapping child trace name to its outputs
        Keys: EvaluateLabels, EvaluateCurrencyLabels, EvaluateMetadataLabels,
              ValidateFinancialMath, LLMReview
    """
    try:
        children = client.list_runs(parent_run_id=parent_run_id)
        return {run.name: run.outputs for run in children if run.outputs}
    except Exception:
        logger.exception("Error getting child traces for %s", parent_run_id)
        return {}


def find_receipts_with_anomalies(
    project_name: str,
    hours_back: int = 168,
) -> list[dict[str, Any]]:
    """Find receipts that have geometric/constellation anomalies.

    Args:
        project_name: LangSmith project name
        hours_back: How many hours back to search

    Returns:
        List of receipt info dicts with:
        - run_id: LangSmith run ID
        - image_id: Receipt image ID
        - receipt_id: Receipt ID
        - merchant_name: Merchant name
        - issues: List of anomaly issues
        - flagged_words: List of flagged words
        - execution_id: Step Function execution ID
    """
    client = get_langsmith_client()
    traces = query_recent_receipt_traces(project_name, hours_back)
    receipts_with_anomalies = []

    for trace in traces:
        metadata = trace.metadata or {}

        # Get issues from the EvaluateLabels child trace
        children = get_child_traces(client, str(trace.id))
        evaluate_labels_outputs = children.get("EvaluateLabels", {})

        issues = evaluate_labels_outputs.get("issues", [])
        flagged_words = evaluate_labels_outputs.get("flagged_words", [])

        if not issues:
            continue

        # Check for anomaly types
        anomaly_types = {
            "geometric_anomaly",
            "position_anomaly",
            "constellation_anomaly",
        }
        geometric_issues = [
            issue for issue in issues if issue.get("type") in anomaly_types
        ]

        if geometric_issues:
            # Extract execution_id from metadata
            execution_id = metadata.get("execution_id", "")

            # If execution_id not in metadata, try to extract from trace name/tags
            if not execution_id:
                # Fallback: use run_id as execution_id
                execution_id = str(trace.id)[:8]

            receipts_with_anomalies.append({
                "run_id": str(trace.id),
                "image_id": metadata.get("image_id"),
                "receipt_id": metadata.get("receipt_id"),
                "merchant_name": metadata.get("merchant_name", "Unknown"),
                "issues": geometric_issues,
                "all_issues": issues,
                "flagged_words": flagged_words,
                "execution_id": execution_id,
            })

    logger.info(
        "Found %d receipts with anomalies from %d traces",
        len(receipts_with_anomalies),
        len(traces),
    )
    return receipts_with_anomalies


def find_receipts_with_llm_decisions(
    project_name: str,
    hours_back: int = 168,
) -> list[dict[str, Any]]:
    """Find receipts that have LLM evaluation decisions.

    Queries child traces (Currency, Metadata, Financial, LLMReview) for
    receipts with any LLM decisions (VALID, INVALID, or NEEDS_REVIEW).

    Args:
        project_name: LangSmith project name
        hours_back: How many hours back to search

    Returns:
        List of receipt info dicts with all evaluation decisions
    """
    client = get_langsmith_client()
    traces = query_recent_receipt_traces(project_name, hours_back)

    interesting_receipts = []

    for trace in traces:
        metadata = trace.metadata or {}

        # Get child traces for this receipt
        children = get_child_traces(client, str(trace.id))

        # Get decisions from each evaluator child trace
        currency_outputs = children.get("EvaluateCurrencyLabels", {})
        metadata_outputs = children.get("EvaluateMetadataLabels", {})
        financial_outputs = children.get("ValidateFinancialMath", {})
        llm_review_outputs = children.get("LLMReview", {})

        currency_decisions = currency_outputs.get("all_decisions", [])
        metadata_decisions = metadata_outputs.get("all_decisions", [])
        financial_decisions = financial_outputs.get("all_decisions", [])
        reviewed_issues = llm_review_outputs.get("reviewed_issues", [])

        # Combine all decisions
        all_decisions = (
            currency_decisions
            + metadata_decisions
            + financial_decisions
            + reviewed_issues
        )

        # Check for INVALID decisions (interesting for visualization)
        has_invalid = any(
            d.get("llm_review", {}).get("decision") == "INVALID"
            for d in all_decisions
        )

        # Also include if there are any decisions at all
        has_decisions = len(all_decisions) > 0

        if has_invalid or has_decisions:
            execution_id = metadata.get("execution_id", str(trace.id)[:8])

            interesting_receipts.append({
                "run_id": str(trace.id),
                "image_id": metadata.get("image_id"),
                "receipt_id": metadata.get("receipt_id"),
                "merchant_name": metadata.get("merchant_name", "Unknown"),
                "execution_id": execution_id,
                "currency_decisions": currency_decisions,
                "metadata_decisions": metadata_decisions,
                "financial_decisions": financial_decisions,
                "reviewed_issues": reviewed_issues,
                "has_invalid": has_invalid,
            })

    logger.info(
        "Found %d receipts with LLM decisions from %d traces",
        len(interesting_receipts),
        len(traces),
    )
    return interesting_receipts
