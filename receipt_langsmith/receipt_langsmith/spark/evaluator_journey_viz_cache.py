"""Decision journey viz-cache builder for receipt evaluator traces.

Builds a per-receipt journey cache that tracks each word's evaluation path
across phases (currency_evaluation, metadata_evaluation, financial_validation,
phase3_llm_review), detects conflicts where the same word receives different
decisions in different phases, and produces a JSON structure suitable for
visualization.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

PHASE_NAMES = (
    "currency_evaluation",
    "metadata_evaluation",
    "financial_validation",
    "phase3_llm_review",
)


def _read_all_parquet(parquet_dir: str):
    """Read all parquet files under *parquet_dir* into a single pandas DataFrame."""
    import pandas as pd

    frames = []
    for root, _dirs, files in os.walk(parquet_dir):
        for fname in sorted(files):
            if not fname.endswith(".parquet"):
                continue
            path = os.path.join(root, fname)
            table = pq.ParquetFile(path).read()
            frames.append(table.to_pandas())

    if not frames:
        raise FileNotFoundError(f"No parquet files found under {parquet_dir}")
    return pd.concat(frames, ignore_index=True)


def _extract_root_metadata(row) -> dict[str, Any]:
    """Pull image_id, receipt_id, merchant_name from a ReceiptEvaluation root."""
    extra = json.loads(row["extra"]) if row["extra"] else {}
    meta = extra.get("metadata", {})
    return {
        "image_id": meta.get("image_id"),
        "receipt_id": meta.get("receipt_id"),
        "merchant_name": meta.get("merchant_name"),
    }


def _parse_phase_decisions(
    row, phase_name: str
) -> list[dict[str, Any]]:
    """Parse the output list from a phase span into flat decision dicts."""
    raw = row["outputs"]
    if not raw:
        return []
    parsed = json.loads(raw) if isinstance(raw, str) else raw
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
            "start_time": str(row["start_time"]) if row["start_time"] is not None else None,
            "end_time": str(row["end_time"]) if row["end_time"] is not None else None,
        })
    return decisions


def build_journey_cache(parquet_dir: str) -> list[dict]:
    """Build decision journey cache from parquet trace exports.

    Parameters
    ----------
    parquet_dir:
        Root directory containing LangSmith parquet exports.

    Returns
    -------
    list[dict]
        One dict per receipt with the structure documented in the module
        docstring.
    """
    df = _read_all_parquet(parquet_dir)
    logger.info("Loaded %d spans from parquet", len(df))

    # Index root ReceiptEvaluation runs by trace_id
    roots = df[df["name"] == "ReceiptEvaluation"]
    root_by_trace: dict[str, dict[str, Any]] = {}
    for _, row in roots.iterrows():
        root_by_trace[row["trace_id"]] = _extract_root_metadata(row)

    logger.info("Found %d root ReceiptEvaluation traces", len(root_by_trace))

    # Collect all decisions across phases, grouped by trace_id
    # trace_id -> list of flat decision dicts
    decisions_by_trace: dict[str, list[dict[str, Any]]] = {}
    for phase in PHASE_NAMES:
        phase_df = df[df["name"] == phase]
        for _, row in phase_df.iterrows():
            trace_id = row["trace_id"]
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
            key = (dec["line_id"], dec["word_id"])
            word_groups.setdefault(key, []).append(dec)

        journeys: list[dict] = []
        for (line_id, word_id), phases_list in sorted(word_groups.items()):
            # Sort phases by predefined order
            phase_order = {name: i for i, name in enumerate(PHASE_NAMES)}
            phases_list.sort(key=lambda d: phase_order.get(d["phase"], 99))

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
