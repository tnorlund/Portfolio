"""Decision journey viz-cache builder for receipt evaluator traces.

Builds a per-receipt journey cache that tracks each word's evaluation path
across phases (currency_evaluation, metadata_evaluation, financial_validation,
phase3_llm_review), detects conflicts where the same word receives different
decisions in different phases, and produces a JSON structure suitable for
visualization.
"""

from __future__ import annotations

import logging
from typing import Any

from receipt_langsmith.spark.utils import parse_json_object
from receipt_langsmith.spark.utils import read_all_parquet_rows

logger = logging.getLogger(__name__)

PHASE_NAMES = (
    "currency_evaluation",
    "metadata_evaluation",
    "financial_validation",
    "phase3_llm_review",
)
PHASE_ORDER = {name: i for i, name in enumerate(PHASE_NAMES)}


def _extract_root_metadata(row: dict[str, Any]) -> dict[str, Any]:
    """Pull image_id, receipt_id, merchant_name from a ReceiptEvaluation root."""
    extra = parse_json_object(row.get("extra"))
    meta = extra.get("metadata", {})
    return {
        "image_id": meta.get("image_id"),
        "receipt_id": meta.get("receipt_id"),
        "merchant_name": meta.get("merchant_name"),
    }


def _parse_phase_decisions(
    row: dict[str, Any], phase_name: str
) -> list[dict[str, Any]]:
    """Parse the output list from a phase span into flat decision dicts."""
    raw = row.get("outputs")
    if not raw:
        return []
    parsed = parse_json_object(raw)
    output_list = parsed.get("output", [])
    if not isinstance(output_list, list):
        return []

    decisions = []
    for item in output_list:
        issue = item.get("issue", {})
        llm_review = item.get("llm_review", {})
        decisions.append({
            "phase": phase_name,
            "line_id": issue.get("line_id"),
            "word_id": issue.get("word_id"),
            "word_text": issue.get("word_text", ""),
            "current_label": issue.get("current_label", ""),
            "decision": llm_review.get("decision"),
            "confidence": llm_review.get("confidence"),
            "reasoning": llm_review.get("reasoning"),
            "suggested_label": llm_review.get("suggested_label"),
            "start_time": (
                str(row.get("start_time"))
                if row.get("start_time") is not None
                else None
            ),
            "end_time": (
                str(row.get("end_time"))
                if row.get("end_time") is not None
                else None
            ),
        })
    return decisions


def build_journey_cache(
    parquet_dir: str | None = None,
    *,
    rows: list[dict[str, Any]] | None = None,
) -> list[dict]:
    """Build decision journey cache from parquet trace exports.

    Parameters
    ----------
    parquet_dir:
        Root directory containing LangSmith parquet exports.
    rows:
        Optional preloaded trace rows.

    Returns
    -------
    list[dict]
        One dict per receipt with the structure documented in the module
        docstring.
    """
    if rows is None:
        if parquet_dir is None:
            raise ValueError("Either parquet_dir or rows must be provided")
        rows = read_all_parquet_rows(parquet_dir)
    logger.info("Loaded %d spans from parquet", len(rows))

    # Index root ReceiptEvaluation runs by trace_id
    roots = [
        row
        for row in rows
        if row.get("name") == "ReceiptEvaluation"
        and row.get("parent_run_id") in (None, "")
    ]
    root_by_trace: dict[str, dict[str, Any]] = {}
    for row in roots:
        trace_id = row.get("trace_id")
        if not trace_id:
            continue
        root_by_trace[trace_id] = _extract_root_metadata(row)

    logger.info("Found %d root ReceiptEvaluation traces", len(root_by_trace))

    # Collect all decisions across phases, grouped by trace_id
    # trace_id -> list of flat decision dicts
    decisions_by_trace: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        phase = row.get("name")
        if phase not in PHASE_NAMES:
            continue
        trace_id = row.get("trace_id")
        if not trace_id:
            continue
        decs = _parse_phase_decisions(row, phase)
        if decs:
            decisions_by_trace.setdefault(trace_id, []).extend(decs)

    logger.info(
        "Collected decisions for %d traces across %d phases",
        len(decisions_by_trace),
        len(PHASE_NAMES),
    )

    # Build per-receipt journey objects
    results: list[dict] = []
    for trace_id, root_meta in root_by_trace.items():
        all_decisions = decisions_by_trace.get(trace_id, [])

        # Group decisions by (line_id, word_id)
        word_groups: dict[tuple[int, int], list[dict]] = {}
        for dec in all_decisions:
            line_id = dec.get("line_id")
            word_id = dec.get("word_id")
            if line_id is None or word_id is None:
                continue
            key = (line_id, word_id)
            word_groups.setdefault(key, []).append(dec)

        journeys: list[dict] = []
        for (line_id, word_id), phases_list in sorted(word_groups.items()):
            # Sort phases by predefined order
            phases_list.sort(key=lambda d: PHASE_ORDER.get(d["phase"], 99))

            # Detect conflicts: different non-null decisions across phases
            unique_decisions = {
                d["decision"]
                for d in phases_list
                if d["decision"] is not None
            }
            has_conflict = len(unique_decisions) > 1

            # Final outcome is the last phase's decision
            final_outcome = phases_list[-1]["decision"]

            phase_entries = []
            for d in phases_list:
                phase_entries.append({
                    "phase": d["phase"],
                    "decision": d["decision"],
                    "confidence": d["confidence"],
                    "reasoning": d["reasoning"],
                    "suggested_label": d["suggested_label"],
                    "start_time": d["start_time"],
                    "end_time": d["end_time"],
                })

            journeys.append({
                "line_id": line_id,
                "word_id": word_id,
                "word_text": phases_list[0]["word_text"],
                "current_label": phases_list[0]["current_label"],
                "phases": phase_entries,
                "has_conflict": has_conflict,
                "final_outcome": final_outcome,
            })

        multi_phase_words = sum(
            1 for j in journeys if len(j["phases"]) > 1
        )
        words_with_conflicts = sum(
            1 for j in journeys if j["has_conflict"]
        )

        results.append({
            "image_id": root_meta.get("image_id"),
            "receipt_id": root_meta.get("receipt_id"),
            "merchant_name": root_meta.get("merchant_name"),
            "trace_id": trace_id,
            "journeys": journeys,
            "summary": {
                "total_words_evaluated": len(journeys),
                "multi_phase_words": multi_phase_words,
                "words_with_conflicts": words_with_conflicts,
            },
        })

    logger.info("Built journey cache for %d receipts", len(results))
    return results
