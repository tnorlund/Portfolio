"""Helper utilities for financial math overlay visualization cache generation.

Reads LangSmith trace parquet exports and builds viz-cache JSON dicts that
describe the financial validation equations found in each receipt evaluation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


def _read_all_parquet(parquet_dir: str) -> pa.Table:
    """Read all parquet files from a directory tree into one Arrow table."""
    import glob
    import os

    pattern = os.path.join(parquet_dir, "**", "*.parquet")
    files = sorted(glob.glob(pattern, recursive=True))
    if not files:
        raise FileNotFoundError(f"No parquet files found under {parquet_dir}")

    logger.info("Reading %d parquet files from %s", len(files), parquet_dir)
    tables: list[pa.Table] = []
    for f in files:
        pf = pq.ParquetFile(f)
        tables.append(pf.read())

    return pa.concat_tables(tables, promote_options="permissive")


def _parse_json(raw: str | None) -> Any:
    """Safely parse a JSON string, returning None on failure."""
    if not raw:
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
        "bbox": _extract_bbox(word) if word else {"x": 0, "y": 0, "width": 0, "height": 0},
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


def build_financial_math_cache(parquet_dir: str) -> list[dict]:
    """Read local parquet traces and return financial math viz-cache dicts.

    Each dict represents one receipt that had financial validation issues
    and follows the output format documented in the task spec.

    Args:
        parquet_dir: Path to directory containing LangSmith parquet exports.

    Returns:
        List of viz-cache dicts, one per receipt with financial issues.
    """
    table = _read_all_parquet(parquet_dir)

    names = table.column("name").to_pylist()
    trace_ids_col = table.column("trace_id").to_pylist()
    inputs_col = table.column("inputs").to_pylist()
    outputs_col = table.column("outputs").to_pylist()
    extra_col = table.column("extra").to_pylist()

    # --- Build root ReceiptEvaluation metadata lookup: trace_id -> metadata ---
    root_meta: dict[str, dict[str, Any]] = {}
    for i, name in enumerate(names):
        if name == "ReceiptEvaluation":
            tid = trace_ids_col[i]
            extra = _parse_json(extra_col[i])
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
    for i, name in enumerate(names):
        if name != "financial_validation":
            continue

        outputs = _parse_json(outputs_col[i])
        if not outputs:
            continue
        output_list = outputs.get("output", [])
        if not output_list:
            continue

        inputs = _parse_json(inputs_col[i])
        if not inputs:
            continue

        # Get receipt identity from inputs (preferred) or root metadata
        image_id = inputs.get("image_id")
        receipt_id = inputs.get("receipt_id")
        merchant_name = inputs.get("merchant_name")
        trace_id = trace_ids_col[i]

        # Fallback to root metadata if inputs lack identity
        if not image_id and trace_id in root_meta:
            meta = root_meta[trace_id]
            image_id = image_id or meta.get("image_id")
            receipt_id = receipt_id if receipt_id is not None else meta.get("receipt_id")
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
