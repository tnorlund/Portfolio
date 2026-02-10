"""Helper to extract ChromaDB evidence data from LangSmith trace parquet exports.

Reads parquet trace exports, finds ``ReceiptEvaluation`` root spans and their
``phase3_llm_review`` children, then returns per-receipt evidence summaries
suitable for visualization cache generation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parquet reading helpers
# ---------------------------------------------------------------------------


def _list_parquet_files(parquet_dir: str) -> list[Path]:
    """Recursively find all .parquet files under *parquet_dir*."""
    root = Path(parquet_dir)
    if root.is_file():
        return [root]
    return sorted(root.rglob("*.parquet"))


def _read_all_rows(parquet_dir: str) -> list[dict[str, Any]]:
    """Read every row from all parquet files into a list of dicts."""
    files = _list_parquet_files(parquet_dir)
    if not files:
        logger.warning("No parquet files found in %s", parquet_dir)
        return []

    rows: list[dict[str, Any]] = []
    for path in files:
        try:
            table = pq.read_table(str(path))
            rows.extend(table.to_pylist())
        except Exception:  # noqa: BLE001
            logger.exception("Failed to read %s", path)
    logger.info("Read %d rows from %d parquet files", len(rows), len(files))
    return rows


def _parse_json(value: Any) -> Any:
    """Parse a JSON string, returning the original value if not a string."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return {}
    if isinstance(value, dict):
        return value
    return {}


# ---------------------------------------------------------------------------
# Trace extraction
# ---------------------------------------------------------------------------


def _find_root_spans(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return rows that are ``ReceiptEvaluation`` root spans."""
    roots: list[dict[str, Any]] = []
    for row in rows:
        if row.get("name") != "ReceiptEvaluation":
            continue
        if row.get("parent_run_id") is not None:
            continue
        roots.append(row)
    return roots


def _extract_metadata(row: dict[str, Any]) -> dict[str, Any]:
    """Pull image_id, receipt_id, merchant_name from ``extra.metadata``."""
    extra = _parse_json(row.get("extra"))
    metadata = extra.get("metadata", {}) if isinstance(extra, dict) else {}
    return {
        "image_id": metadata.get("image_id", ""),
        "receipt_id": metadata.get("receipt_id"),
        "merchant_name": metadata.get("merchant_name", ""),
    }


def _find_child_span(
    rows: list[dict[str, Any]],
    trace_id: str,
    child_name: str,
) -> dict[str, Any] | None:
    """Find the first child span matching *child_name* in the same trace."""
    for row in rows:
        if row.get("trace_id") != trace_id:
            continue
        if row.get("name") == child_name and row.get("parent_run_id") is not None:
            return row
    return None


# ---------------------------------------------------------------------------
# Evidence parsing
# ---------------------------------------------------------------------------


def _parse_review_decisions(
    child_span: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract ``review_all_decisions`` from the child span outputs."""
    outputs = _parse_json(child_span.get("outputs"))
    decisions = outputs.get("review_all_decisions")
    if isinstance(decisions, list):
        return decisions
    return []


def _build_issue_entry(decision: dict[str, Any]) -> dict[str, Any]:
    """Convert a single review_all_decisions entry to the output format."""
    issue = decision.get("issue", {})
    llm_review = decision.get("llm_review", {})
    evidence = decision.get("evidence", [])

    entry = {
        "line_id": issue.get("line_id"),
        "word_id": issue.get("word_id"),
        "word_text": issue.get("word_text", ""),
        "current_label": issue.get("current_label", ""),
        "issue_type": issue.get("type", ""),
        "suggested_label": issue.get("suggested_label", ""),
        "decision": llm_review.get("decision", ""),
        "confidence": llm_review.get("confidence", ""),
        "reasoning": llm_review.get("reasoning", ""),
        "consensus_score": decision.get("consensus_score", 0.0),
        "similar_word_count": decision.get("similar_word_count", 0),
        "evidence": evidence[:10] if isinstance(evidence, list) else [],
    }
    corners = issue.get("corners")
    if corners:
        entry["corners"] = corners
    return entry


def _build_summary(issues: list[dict[str, Any]]) -> dict[str, Any]:
    """Build aggregate summary from the list of issue entries."""
    total = len(issues)
    with_evidence = sum(1 for i in issues if i.get("similar_word_count", 0) > 0)

    consensus_scores = [
        i["consensus_score"]
        for i in issues
        if isinstance(i.get("consensus_score"), (int, float))
    ]
    avg_consensus = (
        sum(consensus_scores) / len(consensus_scores) if consensus_scores else 0.0
    )

    all_top_sims: list[float] = []
    for issue in issues:
        for ev in issue.get("evidence", []):
            score = ev.get("similarity_score")
            if isinstance(score, (int, float)):
                all_top_sims.append(float(score))
    avg_similarity = (
        sum(all_top_sims) / len(all_top_sims) if all_top_sims else 0.0
    )

    decisions: dict[str, int] = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}
    for issue in issues:
        d = (issue.get("decision") or "").upper()
        if d in decisions:
            decisions[d] += 1

    return {
        "total_issues_reviewed": total,
        "issues_with_evidence": with_evidence,
        "avg_consensus_score": round(avg_consensus, 4),
        "avg_similarity": round(avg_similarity, 4),
        "decisions": decisions,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_evidence_cache(parquet_dir: str) -> list[dict[str, Any]]:
    """Build per-receipt evidence cache from LangSmith parquet exports.

    Args:
        parquet_dir: Path to a directory containing parquet files
            (or a single parquet file).

    Returns:
        List of per-receipt dicts with ``image_id``, ``receipt_id``,
        ``merchant_name``, ``trace_id``, ``issues_with_evidence``,
        and ``summary``.
    """
    rows = _read_all_rows(parquet_dir)
    if not rows:
        return []

    roots = _find_root_spans(rows)
    logger.info("Found %d ReceiptEvaluation root spans", len(roots))

    results: list[dict[str, Any]] = []
    for root in roots:
        trace_id = root.get("trace_id", "")
        meta = _extract_metadata(root)

        child = _find_child_span(rows, trace_id, "phase3_llm_review")
        if child is None:
            continue

        decisions = _parse_review_decisions(child)
        if not decisions:
            continue

        issues = [_build_issue_entry(d) for d in decisions]

        results.append({
            "image_id": meta["image_id"],
            "receipt_id": meta["receipt_id"],
            "merchant_name": meta["merchant_name"],
            "trace_id": trace_id,
            "issues_with_evidence": issues,
            "summary": _build_summary(issues),
        })

    logger.info("Built evidence cache for %d receipts", len(results))
    return results
