"""Viz-cache helper for Pattern Discovery and Geometric Anomaly data.

Reads LangSmith trace parquet exports and extracts:
  - Per-merchant patterns from ``llm_pattern_discovery`` spans (via
    ``UnifiedPatternBuilder`` root traces).
  - Geometric anomaly summaries from ``flag_geometric_anomalies`` spans
    (inside ``ReceiptEvaluation`` root traces), aggregated by merchant.

The public entry point is :func:`build_patterns_cache` which returns a
list of per-merchant pattern dicts ready for JSON serialization.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from receipt_langsmith.spark.utils import parse_json_object, to_s3a

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_all_parquet(parquet_dir: str) -> "list[dict[str, Any]]":
    """Read parquet rows from local paths or S3 paths.

    Supports:
        - local directory trees containing parquet files
        - local single parquet file
        - s3:// / s3a:// parquet paths (via active Spark session)
    """
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

    # pylint: disable=import-outside-toplevel
    import pyarrow.parquet as pq  # local import to keep module importable

    # pylint: enable=import-outside-toplevel

    root = Path(parquet_dir)
    files = [root] if root.is_file() else sorted(root.rglob("*.parquet"))
    if not files:
        return []

    rows: list[dict[str, Any]] = []
    for path in files:
        table = pq.ParquetFile(str(path)).read()
        rows.extend(table.to_pylist())
    return rows


def _extract_merchant_from_extra(extra_raw: Any) -> str:
    """Return ``merchant_name`` from the ``extra.metadata`` JSON blob."""
    extra = parse_json_object(extra_raw)
    metadata_raw = extra.get("metadata", {})
    if not isinstance(metadata_raw, dict):
        return "Unknown"
    merchant = metadata_raw.get("merchant_name")
    return merchant if isinstance(merchant, str) and merchant else "Unknown"


def _parse_pattern_from_raw_response(
    raw_response: str,
) -> dict[str, Any] | None:
    """Parse the JSON object embedded in an LLM raw_response string."""
    start = raw_response.find("{")
    end = raw_response.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(raw_response[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _is_root(row: dict[str, Any]) -> bool:
    """Return True when the row looks like a root span."""
    if row.get("is_root"):
        return True
    return row.get("parent_run_id") in (None, "")


# ---------------------------------------------------------------------------
# Core builders
# ---------------------------------------------------------------------------


def _build_merchant_patterns(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Extract per-merchant patterns from ``llm_pattern_discovery`` spans.

    Returns a dict mapping merchant_name -> pattern dict.
    """
    # Map trace_id -> merchant_name via root UnifiedPatternBuilder spans
    trace_merchant: dict[str, str] = {}
    for row in rows:
        if _is_root(row) and row.get("name") == "UnifiedPatternBuilder":
            trace_id = row.get("trace_id")
            if not trace_id:
                continue
            merchant = _extract_merchant_from_extra(row.get("extra"))
            trace_merchant[trace_id] = merchant

    # Parse patterns from llm_pattern_discovery outputs
    patterns: dict[str, dict[str, Any]] = {}
    trace_ids_per_merchant: dict[str, list[str]] = defaultdict(list)

    for row in rows:
        if row.get("name") != "llm_pattern_discovery":
            continue
        outputs = parse_json_object(row.get("outputs"))
        raw = outputs.get("raw_response", "")
        if not raw:
            continue
        parsed = _parse_pattern_from_raw_response(raw)
        if parsed is None:
            continue

        # Determine merchant from parsed output or trace root
        merchant = parsed.get("merchant", "Unknown")
        trace_id = row.get("trace_id", "")
        if merchant == "Unknown" and trace_id in trace_merchant:
            merchant = trace_merchant[trace_id]

        if merchant in patterns:
            logger.warning(
                (
                    "Overwriting existing pattern for merchant=%s "
                    "trace_id=%s parsed=%s"
                ),
                merchant,
                trace_id,
                parsed,
            )
        trace_ids_per_merchant[merchant].append(trace_id)
        patterns[merchant] = parsed

    # Attach trace_ids
    result: dict[str, dict[str, Any]] = {}
    for merchant, pattern in patterns.items():
        result[merchant] = {
            "trace_ids": trace_ids_per_merchant.get(merchant, []),
            "pattern": {
                "receipt_type": pattern.get("receipt_type"),
                "receipt_type_reason": pattern.get("receipt_type_reason"),
                "item_structure": pattern.get("item_structure"),
                "lines_per_item": pattern.get("lines_per_item"),
                "label_positions": pattern.get("label_positions"),
                "barcode_pattern": pattern.get("barcode_pattern"),
                "special_markers": pattern.get("special_markers"),
                "grouping_rule": pattern.get("grouping_rule"),
            },
        }

    return result


def _build_geometric_summary(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], int]:
    """Aggregate geometric anomaly issues per merchant.

    Returns ``(merchant_summaries, total_issue_count)``.
    """
    # Map trace_id -> merchant_name via root ReceiptEvaluation spans
    trace_merchant: dict[str, str] = {}
    for row in rows:
        if _is_root(row) and row.get("name") == "ReceiptEvaluation":
            trace_id = row.get("trace_id")
            if not trace_id:
                continue
            merchant = _extract_merchant_from_extra(row.get("extra"))
            trace_merchant[trace_id] = merchant

    summaries: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "total_issues": 0,
            "issue_types": Counter(),
            "top_suggested_labels": Counter(),
        }
    )
    total_issues = 0

    for row in rows:
        if row.get("name") != "flag_geometric_anomalies":
            continue
        outputs = parse_json_object(row.get("outputs"))
        issues = outputs.get("issues_found", [])
        if not issues:
            continue

        trace_id = row.get("trace_id", "")
        merchant = trace_merchant.get(trace_id, "Unknown")
        summary = summaries[merchant]

        for issue in issues:
            total_issues += 1
            summary["total_issues"] += 1
            issue_type = issue.get("issue_type", "unknown")
            summary["issue_types"][issue_type] += 1
            suggested = issue.get("suggested_label")
            if suggested:
                summary["top_suggested_labels"][suggested] += 1

    # Convert Counters to plain dicts for JSON serialization
    result: dict[str, dict[str, Any]] = {}
    for merchant, summary in summaries.items():
        result[merchant] = {
            "total_issues": summary["total_issues"],
            "issue_types": dict(summary["issue_types"]),
            "top_suggested_labels": dict(summary["top_suggested_labels"]),
        }

    return result, total_issues


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_patterns_cache(
    parquet_dir: str | None = None,
    *,
    rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build per-merchant pattern discovery cache from parquet trace exports.

    Parameters
    ----------
    parquet_dir:
        Path to the root directory containing LangSmith parquet exports
        (traversed recursively).
    rows:
        Optional preloaded trace rows.

    Returns
    -------
    list[dict]
        One dict per merchant containing ``merchant_name``, ``trace_ids``,
        ``pattern``, and ``geometric_summary`` fields.
    """
    if rows is None:
        if parquet_dir is None:
            raise ValueError("Either parquet_dir or rows must be provided")
        logger.info("Reading parquet traces from %s", parquet_dir)
        rows = _read_all_parquet(parquet_dir)
    logger.info("Loaded %d trace rows", len(rows))

    merchant_patterns = _build_merchant_patterns(rows)
    logger.info("Extracted patterns for %d merchants", len(merchant_patterns))

    geo_summaries, total_geo_issues = _build_geometric_summary(rows)
    logger.info(
        "Aggregated %d geometric issues across %d merchants",
        total_geo_issues,
        len(geo_summaries),
    )

    # Merge patterns + geometric summaries into per-merchant output
    all_merchants = set(merchant_patterns.keys()) | set(geo_summaries.keys())
    cache: list[dict[str, Any]] = []

    for merchant in sorted(all_merchants):
        entry: dict[str, Any] = {"merchant_name": merchant}

        pat = merchant_patterns.get(merchant)
        if pat:
            entry["trace_ids"] = pat["trace_ids"]
            entry["pattern"] = pat["pattern"]
        else:
            entry["trace_ids"] = []
            entry["pattern"] = None

        geo = geo_summaries.get(merchant)
        if geo:
            entry["geometric_summary"] = geo
        else:
            entry["geometric_summary"] = {
                "total_issues": 0,
                "issue_types": {},
                "top_suggested_labels": {},
            }

        cache.append(entry)

    logger.info("Built cache for %d merchants", len(cache))
    return cache
