"""Helper utilities for financial math overlay visualization cache generation.

Reads LangSmith trace parquet exports and builds viz-cache JSON dicts that
describe the financial validation equations found in each receipt evaluation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from receipt_langsmith.spark.utils import to_s3a

logger = logging.getLogger(__name__)


def _read_all_parquet_rows(parquet_dir: str) -> list[dict[str, Any]]:
    """Read all parquet rows from local paths or S3 paths."""
    if parquet_dir.startswith(("s3://", "s3a://")):
        # Import lazily so local unit tests do not require pyspark.
        # pylint: disable=import-outside-toplevel
        from pyspark.sql import SparkSession
        # pylint: enable=import-outside-toplevel

        spark = SparkSession.getActiveSession()
        if spark is None:
            raise RuntimeError(
                "SparkSession is required for S3 parquet input paths"
            )
        df = spark.read.parquet(to_s3a(parquet_dir))
        return [row.asDict(recursive=True) for row in df.toLocalIterator()]

    root = Path(parquet_dir)
    files = [root] if root.is_file() else sorted(root.rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet files found under {parquet_dir}")

    logger.info("Reading %d parquet files from %s", len(files), parquet_dir)
    rows: list[dict[str, Any]] = []
    for path in files:
        table = pq.ParquetFile(str(path)).read()
        rows.extend(table.to_pylist())
    return rows


def _parse_json(raw: Any) -> Any:
    """Safely parse a JSON string, returning None on failure."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return None
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _build_word_lookup(
    visual_lines: list[dict],
) -> dict[tuple[int, int], dict]:
    """Build a (line_id, word_id) -> word dict lookup from visual_lines."""
    lookup: dict[tuple[int, int], dict] = {}
    for vl in visual_lines:
        for w in vl.get("words", []):
            word = w.get("word", {})
            lid = word.get("line_id")
            wid = word.get("word_id")
            if lid is not None and wid is not None:
                lookup[(lid, wid)] = word
    return lookup


def _extract_bbox(word: dict) -> dict[str, float]:
    """Extract a normalized bounding box dict from a word."""
    bb = word.get("bounding_box", {})
    return {
        "x": bb.get("x", 0.0),
        "y": bb.get("y", 0.0),
        "width": bb.get("width", 0.0),
        "height": bb.get("height", 0.0),
    }


def _build_involved_word(
    decision: dict,
    word_lookup: dict[tuple[int, int], dict],
) -> dict[str, Any]:
    """Build an involved-word entry from a single financial validation decision."""
    issue = decision["issue"]
    lid = issue["line_id"]
    wid = issue["word_id"]
    word = word_lookup.get((lid, wid), {})

    llm_review = decision.get("llm_review", {})

    entry: dict[str, Any] = {
        "line_id": lid,
        "word_id": wid,
        "word_text": issue.get("word_text", ""),
        "current_label": issue.get("current_label", ""),
        "bbox": (
            _extract_bbox(word)
            if word
            else {"x": 0, "y": 0, "width": 0, "height": 0}
        ),
        "decision": llm_review.get("decision"),
        "confidence": llm_review.get("confidence"),
        "reasoning": llm_review.get("reasoning"),
    }
    suggested = llm_review.get("suggested_label")
    if suggested:
        entry["suggested_label"] = suggested
    return entry


def _build_equations(
    decisions: list[dict],
    word_lookup: dict[tuple[int, int], dict],
) -> list[dict[str, Any]]:
    """Group decisions by description (equation) and build equation dicts."""
    # Group by description
    groups: dict[str, list[dict]] = {}
    for d in decisions:
        desc = d["issue"]["description"]
        groups.setdefault(desc, []).append(d)

    equations: list[dict[str, Any]] = []
    for desc, group in groups.items():
        # All decisions in a group share the same equation metadata
        first_issue = group[0]["issue"]
        involved_words = [
            _build_involved_word(d, word_lookup) for d in group
        ]
        equations.append(
            {
                "issue_type": first_issue.get("issue_type", ""),
                "description": desc,
                "expected_value": first_issue.get("expected_value"),
                "actual_value": first_issue.get("actual_value"),
                "difference": first_issue.get("difference"),
                "involved_words": involved_words,
            }
        )

    return equations


def _build_summary(equations: list[dict[str, Any]]) -> dict[str, Any]:
    """Build summary stats from equation list."""
    has_invalid = False
    has_needs_review = False
    for eq in equations:
        for w in eq.get("involved_words", []):
            decision = (w.get("decision") or "").upper()
            if decision == "INVALID":
                has_invalid = True
            elif decision == "NEEDS_REVIEW":
                has_needs_review = True

    return {
        "total_equations": len(equations),
        "has_invalid": has_invalid,
        "has_needs_review": has_needs_review,
    }


def build_financial_math_cache(
    parquet_dir: str | None = None,
    *,
    rows: list[dict[str, Any]] | None = None,
) -> list[dict]:
    """Return financial math viz-cache dicts.

    Each dict represents one receipt that had financial validation issues
    and follows the output format documented in the task spec.

    Args:
        parquet_dir: Path containing LangSmith parquet exports.
        rows: Optional preloaded trace rows.

    Returns:
        List of viz-cache dicts, one per receipt with financial issues.
    """
    if rows is None:
        if parquet_dir is None:
            raise ValueError("Either parquet_dir or rows must be provided")
        rows = _read_all_parquet_rows(parquet_dir)

    # --- Build root ReceiptEvaluation metadata lookup: trace_id -> metadata ---
    root_meta: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = row.get("name")
        if name == "ReceiptEvaluation":
            tid = row.get("trace_id")
            extra = _parse_json(row.get("extra"))
            if extra and tid:
                meta = extra.get("metadata", {})
                root_meta[tid] = {
                    "image_id": meta.get("image_id"),
                    "receipt_id": meta.get("receipt_id"),
                    "merchant_name": meta.get("merchant_name"),
                }

    logger.info("Found %d ReceiptEvaluation root spans", len(root_meta))

    # --- Process financial_validation spans ---
    results: list[dict] = []
    for row in rows:
        name = row.get("name")
        if name != "financial_validation":
            continue

        outputs = _parse_json(row.get("outputs"))
        if not outputs:
            continue
        output_list = outputs.get("output", [])
        if not output_list:
            continue

        inputs = _parse_json(row.get("inputs"))
        if not inputs:
            continue

        # Get receipt identity from inputs (preferred) or root metadata
        image_id = inputs.get("image_id")
        receipt_id = inputs.get("receipt_id")
        merchant_name = inputs.get("merchant_name")
        trace_id = row.get("trace_id")

        # Fallback to root metadata if inputs lack identity
        if not image_id and trace_id in root_meta:
            meta = root_meta[trace_id]
            image_id = image_id or meta.get("image_id")
            if receipt_id is None:
                receipt_id = meta.get("receipt_id")
            merchant_name = merchant_name or meta.get("merchant_name")

        if not image_id:
            logger.debug("Skipping financial_validation span without image_id")
            continue

        # Build word lookup from visual_lines
        visual_lines = inputs.get("visual_lines", [])
        word_lookup = _build_word_lookup(visual_lines)

        # Build equations grouped by description
        equations = _build_equations(output_list, word_lookup)
        summary = _build_summary(equations)

        results.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant_name": merchant_name,
                "trace_id": trace_id,
                "equations": equations,
                "summary": summary,
            }
        )

    logger.info(
        "Built financial math cache for %d receipts (%d total equations)",
        len(results),
        sum(r["summary"]["total_equations"] for r in results),
    )
    return results
