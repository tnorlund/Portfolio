"""Before/After Receipt Diff visualization cache builder.

Reads local LangSmith parquet trace exports and produces per-receipt
diff payloads showing every word with its *before* label (from the
evaluation input) and its *after* label (overlaid with INVALID
suggested_label decisions from the four evaluation sources).

Priority when a word appears in multiple sources:
  financial_validation > currency_evaluation > metadata_evaluation
  > flag_geometric_anomalies
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

# Priority order: higher number wins when a word is flagged by multiple
# sources.  financial > currency > metadata > geometric.
_SOURCE_PRIORITY: dict[str, int] = {
    "flag_geometric_anomalies": 0,
    "metadata_evaluation": 1,
    "currency_evaluation": 2,
    "financial_validation": 3,
}


# ---------------------------------------------------------------------------
# Parquet I/O
# ---------------------------------------------------------------------------

def _read_all_traces(parquet_dir: str) -> pa.Table:
    """Read and concatenate all parquet files under *parquet_dir*."""
    files: list[str] = []
    for root, _dirs, fnames in os.walk(parquet_dir):
        for fname in fnames:
            if fname.endswith(".parquet"):
                files.append(os.path.join(root, fname))
    if not files:
        raise FileNotFoundError(
            f"No parquet files found under {parquet_dir}"
        )
    logger.info("Reading %d parquet files from %s", len(files), parquet_dir)

    tables: list[pa.Table] = []
    for path in files:
        t = pq.ParquetFile(path).read()
        # Normalise dictionary-encoded columns to plain strings so that
        # concat_tables does not choke on schema mismatches.
        for i, field in enumerate(t.schema):
            if pa.types.is_dictionary(field.type):
                t = t.set_column(
                    i, field.name, t.column(field.name).cast(pa.string())
                )
        tables.append(t)

    combined = pa.concat_tables(tables)
    logger.info("Total trace rows: %d", len(combined))
    return combined


# ---------------------------------------------------------------------------
# Index helpers
# ---------------------------------------------------------------------------

def _index_by_trace(
    table: pa.Table,
) -> dict[str, list[int]]:
    """Return {trace_id -> [row indices]} for fast look-ups."""
    trace_ids = table.column("trace_id").to_pylist()
    idx: dict[str, list[int]] = defaultdict(list)
    for i, tid in enumerate(trace_ids):
        if tid is not None:
            idx[tid].append(i)
    return idx


# ---------------------------------------------------------------------------
# Word extraction from evaluation inputs
# ---------------------------------------------------------------------------

def _extract_words_from_input(
    inp: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract the flat word list from a currency/metadata_evaluation input.

    Each returned dict has: line_id, word_id, text, bbox, before_label.
    """
    words: list[dict[str, Any]] = []
    for line in inp.get("visual_lines", []):
        for entry in line.get("words", []):
            w = entry.get("word", {})
            cl = entry.get("current_label")

            before_label: str | None = None
            if isinstance(cl, dict):
                before_label = cl.get("label") or None
            elif isinstance(cl, str) and cl:
                before_label = cl

            bbox = w.get("bounding_box", {})
            words.append(
                {
                    "line_id": w.get("line_id"),
                    "word_id": w.get("word_id"),
                    "text": w.get("text", ""),
                    "bbox": {
                        "x": bbox.get("x", 0),
                        "width": bbox.get("width", 0),
                        "y": bbox.get("y", 0),
                        "height": bbox.get("height", 0),
                    },
                    "before_label": before_label,
                }
            )
    return words


# ---------------------------------------------------------------------------
# Decision extraction from evaluation outputs
# ---------------------------------------------------------------------------

_Decision = dict[str, Any]  # change_source, after_label, confidence, reasoning


def _extract_llm_decisions(
    outputs: dict[str, Any],
    source_name: str,
) -> dict[tuple[int, int], _Decision]:
    """Extract INVALID + suggested_label decisions from currency/metadata/
    financial_validation outputs.

    Returns a dict keyed by (line_id, word_id).
    """
    result: dict[tuple[int, int], _Decision] = {}
    items = outputs.get("output", [])
    if not isinstance(items, list):
        return result
    for item in items:
        lr = item.get("llm_review", {})
        if not isinstance(lr, dict):
            continue
        if lr.get("decision") != "INVALID":
            continue
        suggested = lr.get("suggested_label")
        if not suggested:
            continue

        issue = item.get("issue", {})
        line_id = issue.get("line_id")
        word_id = issue.get("word_id")
        if line_id is None or word_id is None:
            continue

        result[(line_id, word_id)] = {
            "change_source": source_name,
            "after_label": suggested,
            "confidence": lr.get("confidence"),
            "reasoning": lr.get("reasoning"),
        }
    return result


def _extract_geometric_decisions(
    outputs: dict[str, Any],
) -> dict[tuple[int, int], _Decision]:
    """Extract suggested_label decisions from flag_geometric_anomalies."""
    result: dict[tuple[int, int], _Decision] = {}
    items = outputs.get("issues_found", [])
    if not isinstance(items, list):
        return result
    for item in items:
        suggested = item.get("suggested_label")
        if not suggested:
            continue
        w = item.get("word", {})
        line_id = w.get("line_id")
        word_id = w.get("word_id")
        if line_id is None or word_id is None:
            continue
        result[(line_id, word_id)] = {
            "change_source": "flag_geometric_anomalies",
            "after_label": suggested,
            "confidence": None,
            "reasoning": item.get("reasoning"),
        }
    return result


# ---------------------------------------------------------------------------
# Per-receipt diff assembly
# ---------------------------------------------------------------------------

def _build_receipt_diff(
    words: list[dict[str, Any]],
    changes: dict[tuple[int, int], _Decision],
    image_id: str,
    receipt_id: int,
    merchant_name: str | None,
    trace_id: str,
) -> dict[str, Any]:
    """Assemble one receipt diff payload."""
    diff_words: list[dict[str, Any]] = []
    change_count = 0
    before_counts: dict[str, int] = defaultdict(int)
    after_counts: dict[str, int] = defaultdict(int)

    for w in words:
        key = (w["line_id"], w["word_id"])
        before = w["before_label"]
        before_key = before if before else "null"
        before_counts[before_key] += 1

        decision = changes.get(key)
        if decision:
            after = decision["after_label"]
            after_key = after if after else "null"
            after_counts[after_key] += 1
            change_count += 1
            diff_words.append(
                {
                    "line_id": w["line_id"],
                    "word_id": w["word_id"],
                    "text": w["text"],
                    "before_label": before,
                    "after_label": after,
                    "changed": True,
                    "change_source": decision["change_source"],
                    "confidence": decision["confidence"],
                    "reasoning": decision["reasoning"],
                    "bbox": w["bbox"],
                }
            )
        else:
            after_counts[before_key] += 1
            diff_words.append(
                {
                    "line_id": w["line_id"],
                    "word_id": w["word_id"],
                    "text": w["text"],
                    "before_label": before,
                    "after_label": before,
                    "changed": False,
                    "bbox": w["bbox"],
                }
            )

    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant_name": merchant_name,
        "trace_id": trace_id,
        "word_count": len(diff_words),
        "change_count": change_count,
        "words": diff_words,
        "label_summary": {
            "before": dict(before_counts),
            "after": dict(after_counts),
        },
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

_EVALUATOR_NAMES = frozenset(
    {
        "ReceiptEvaluation",
        "currency_evaluation",
        "metadata_evaluation",
        "flag_geometric_anomalies",
        "financial_validation",
    }
)


def build_diff_cache(parquet_dir: str) -> list[dict[str, Any]]:
    """Read local parquet traces and build before/after diff payloads.

    Returns one dict per receipt (see module docstring for schema).
    """
    table = _read_all_traces(parquet_dir)

    names = table.column("name").to_pylist()
    trace_ids = table.column("trace_id").to_pylist()
    inputs_col = table.column("inputs").to_pylist()
    outputs_col = table.column("outputs").to_pylist()
    extras_col = table.column("extra").to_pylist()

    # ---- pass 1: collect root ReceiptEvaluation metadata ----
    # {trace_id -> (image_id, receipt_id, merchant_name)}
    root_meta: dict[str, tuple[str, int, str | None]] = {}
    for i, name in enumerate(names):
        if name != "ReceiptEvaluation":
            continue
        extra = _safe_json(extras_col[i])
        meta = extra.get("metadata", {})
        img = meta.get("image_id")
        rid = meta.get("receipt_id")
        if img is None or rid is None:
            continue
        root_meta[trace_ids[i]] = (
            img,
            int(rid),
            meta.get("merchant_name"),
        )

    logger.info("Found %d ReceiptEvaluation roots", len(root_meta))

    # ---- pass 2: per trace_id, collect words + decisions ----
    # words: use the first of currency_evaluation / metadata_evaluation
    trace_words: dict[str, list[dict]] = {}
    # decisions: accumulate per source, respecting priority
    trace_decisions: dict[str, dict[tuple[int, int], _Decision]] = defaultdict(dict)

    for i, name in enumerate(names):
        tid = trace_ids[i]
        if tid not in root_meta:
            continue

        if name in ("currency_evaluation", "metadata_evaluation"):
            # Extract words from input if we haven't already
            if tid not in trace_words:
                inp = _safe_json(inputs_col[i])
                trace_words[tid] = _extract_words_from_input(inp)

            # Extract decisions from output
            out = _safe_json(outputs_col[i])
            new_decisions = _extract_llm_decisions(out, name)
            _merge_decisions(trace_decisions[tid], new_decisions)

        elif name == "flag_geometric_anomalies":
            out = _safe_json(outputs_col[i])
            new_decisions = _extract_geometric_decisions(out)
            _merge_decisions(trace_decisions[tid], new_decisions)

        elif name == "financial_validation":
            out = _safe_json(outputs_col[i])
            new_decisions = _extract_llm_decisions(out, name)
            _merge_decisions(trace_decisions[tid], new_decisions)

    # ---- pass 3: build diffs ----
    results: list[dict[str, Any]] = []
    for tid, (image_id, receipt_id, merchant_name) in root_meta.items():
        words = trace_words.get(tid)
        if not words:
            logger.debug("No words for trace %s", tid)
            continue
        changes = trace_decisions.get(tid, {})
        diff = _build_receipt_diff(
            words, changes, image_id, receipt_id, merchant_name, tid
        )
        results.append(diff)

    logger.info(
        "Built %d diffs (%d with changes)",
        len(results),
        sum(1 for r in results if r["change_count"] > 0),
    )
    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _merge_decisions(
    target: dict[tuple[int, int], _Decision],
    incoming: dict[tuple[int, int], _Decision],
) -> None:
    """Merge *incoming* into *target*, keeping the higher-priority source."""
    for key, decision in incoming.items():
        existing = target.get(key)
        if existing is None:
            target[key] = decision
        else:
            existing_prio = _SOURCE_PRIORITY.get(
                existing["change_source"], -1
            )
            incoming_prio = _SOURCE_PRIORITY.get(
                decision["change_source"], -1
            )
            if incoming_prio > existing_prio:
                target[key] = decision
