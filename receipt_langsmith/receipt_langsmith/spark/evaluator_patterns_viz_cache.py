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

import boto3
from botocore.exceptions import ClientError

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


def _extract_sample_receipt(
    rows_by_trace: dict[str, list[dict[str, Any]]],
    trace_ids: list[str],
) -> dict[str, Any] | None:
    """Extract a sample receipt (image + words with bboxes) from trace data.

    Iterates through the merchant's trace_ids, finds a ``ReceiptEvaluation``
    root span with image metadata, then pulls word data from the first
    ``currency_evaluation`` or ``metadata_evaluation`` child span.

    Returns a dict with ``image_id``, ``receipt_id``, and ``words`` list,
    or ``None`` if no suitable trace is found.
    """
    for trace_id in trace_ids:
        trace_rows = rows_by_trace.get(trace_id, [])
        if not trace_rows:
            continue

        # Find root ReceiptEvaluation span for image metadata
        image_id: str | None = None
        receipt_id: int | None = None
        for row in trace_rows:
            if row.get("name") != "ReceiptEvaluation":
                continue
            if not _is_root(row):
                continue
            extra = parse_json_object(row.get("extra"))
            meta = extra.get("metadata", {})
            if not isinstance(meta, dict):
                continue
            img = meta.get("image_id")
            rid = meta.get("receipt_id")
            if img and rid is not None:
                image_id = str(img)
                try:
                    receipt_id = int(rid)
                except (TypeError, ValueError):
                    continue
                break

        if image_id is None or receipt_id is None:
            continue

        # Find currency_evaluation or metadata_evaluation child span with words
        for row in trace_rows:
            if row.get("name") not in (
                "currency_evaluation",
                "metadata_evaluation",
            ):
                continue
            inputs = parse_json_object(row.get("inputs"))
            visual_lines = inputs.get("visual_lines", [])
            if not visual_lines:
                continue

            words: list[dict[str, Any]] = []
            for line in visual_lines:
                for entry in line.get("words", []):
                    w = entry.get("word", {})
                    cl = entry.get("current_label")

                    label: str | None = None
                    if isinstance(cl, dict):
                        label = cl.get("label") or None
                    elif isinstance(cl, str) and cl:
                        label = cl

                    bbox = w.get("bounding_box", {})
                    line_id = w.get("line_id")
                    word_id = w.get("word_id")
                    if line_id is None or word_id is None:
                        continue
                    words.append(
                        {
                            "line_id": line_id,
                            "word_id": word_id,
                            "text": w.get("text", ""),
                            "label": label,
                            "bbox": {
                                "x": bbox.get("x", 0),
                                "y": bbox.get("y", 0),
                                "width": bbox.get("width", 0),
                                "height": bbox.get("height", 0),
                            },
                        }
                    )

            if words:
                return {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "words": words,
                }

    return None


def _build_constellation_data(
    batch_bucket: str,
    execution_id: str,
) -> dict[str, dict[str, Any]]:
    """Read S3 pattern files and extract constellation geometry data.

    Parameters
    ----------
    batch_bucket:
        S3 bucket containing pattern files.
    execution_id:
        Execution ID used as prefix (``patterns/{execution_id}/``).

    Returns
    -------
    dict[str, dict]
        Mapping of merchant_name to dict with ``receipt_count``,
        ``label_positions``, ``constellations``, and ``label_pairs``.
    """
    s3 = boto3.client("s3")
    prefix = f"patterns/{execution_id}/"
    result: dict[str, dict[str, Any]] = {}

    # List all pattern files for this execution
    keys: list[str] = []
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=batch_bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj.get("Key", "")
                if key.endswith(".json"):
                    keys.append(key)
    except ClientError:
        logger.exception(
            "Failed to list pattern files from s3://%s/%s",
            batch_bucket,
            prefix,
        )
        return result

    logger.info(
        "Found %d pattern files in s3://%s/%s", len(keys), batch_bucket, prefix
    )

    for key in keys:
        try:
            resp = s3.get_object(Bucket=batch_bucket, Key=key)
            data = json.loads(resp["Body"].read().decode("utf-8"))
        except (ClientError, json.JSONDecodeError):
            logger.warning("Failed to read pattern file %s", key)
            continue

        merchant_name = data.get("merchant_name")
        patterns = data.get("patterns")
        if not merchant_name or not isinstance(patterns, dict):
            continue

        entry: dict[str, Any] = {
            "receipt_count": patterns.get("receipt_count", 0),
        }

        # Extract numeric label positions
        raw_positions = patterns.get("label_positions", {})
        if isinstance(raw_positions, dict):
            label_positions: dict[str, dict[str, Any]] = {}
            for label, stats in raw_positions.items():
                if isinstance(stats, dict) and "mean_y" in stats:
                    label_positions[label] = {
                        "mean_y": stats["mean_y"],
                        "std_y": stats.get("std_y", 0),
                        "count": stats.get("count", 0),
                    }
            entry["label_positions"] = label_positions

        # Extract constellation geometry
        raw_constellations = patterns.get("constellation_geometry", [])
        if isinstance(raw_constellations, list):
            constellations: list[dict[str, Any]] = []
            for c in raw_constellations:
                if not isinstance(c, dict):
                    continue
                constellation: dict[str, Any] = {
                    "labels": c.get("labels", []),
                    "observation_count": c.get("observation_count", 0),
                }
                raw_rel = c.get("relative_positions", {})
                if isinstance(raw_rel, dict):
                    rel: dict[str, dict[str, float]] = {}
                    for label, pos in raw_rel.items():
                        if isinstance(pos, dict):
                            rel[label] = {
                                "mean_dx": pos.get("mean_dx", 0),
                                "mean_dy": pos.get("mean_dy", 0),
                                "std_dx": pos.get("std_dx", 0),
                                "std_dy": pos.get("std_dy", 0),
                            }
                    constellation["relative_positions"] = rel
                constellations.append(constellation)
            entry["constellations"] = constellations

        # Extract label pair geometry
        raw_pairs = patterns.get("label_pair_geometry", [])
        if isinstance(raw_pairs, list):
            label_pairs: list[dict[str, Any]] = []
            for p in raw_pairs:
                if not isinstance(p, dict):
                    continue
                label_pairs.append(
                    {
                        "labels": p.get("labels", []),
                        "mean_dx": p.get("mean_dx", 0),
                        "mean_dy": p.get("mean_dy", 0),
                        "std_dx": p.get("std_dx", 0),
                        "std_dy": p.get("std_dy", 0),
                        "count": p.get("count", 0),
                    }
                )
            entry["label_pairs"] = label_pairs

        result[merchant_name] = entry

    logger.info(
        "Extracted constellation data for %d merchants", len(result)
    )
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_patterns_cache(
    parquet_dir: str | None = None,
    *,
    rows: list[dict[str, Any]] | None = None,
    batch_bucket: str | None = None,
    execution_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build per-merchant pattern discovery cache from parquet trace exports.

    Parameters
    ----------
    parquet_dir:
        Path to the root directory containing LangSmith parquet exports
        (traversed recursively).
    rows:
        Optional preloaded trace rows.
    batch_bucket:
        S3 bucket containing pattern files for constellation data.
    execution_id:
        Execution ID for pattern file lookup.

    Returns
    -------
    list[dict]
        One dict per merchant containing ``merchant_name``, ``trace_ids``,
        ``receipt_count``, ``pattern``, ``geometric_summary``,
        ``label_positions``, ``constellations``, and ``label_pairs`` fields.
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

    # Build trace_id -> rows index for sample receipt extraction
    rows_by_trace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        tid = row.get("trace_id")
        if tid:
            rows_by_trace[tid].append(row)

    # Read constellation data from S3 pattern files
    constellation_data: dict[str, dict[str, Any]] = {}
    if batch_bucket and execution_id:
        constellation_data = _build_constellation_data(
            batch_bucket, execution_id
        )
    else:
        logger.info(
            "Skipping constellation data (batch_bucket=%s, execution_id=%s)",
            batch_bucket,
            execution_id,
        )

    # Merge patterns + geometric summaries + constellation data
    all_merchants = (
        set(merchant_patterns.keys())
        | set(geo_summaries.keys())
        | set(constellation_data.keys())
    )
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

        # Merge constellation / label position / label pair data from S3
        cdata = constellation_data.get(merchant)
        if cdata:
            entry["receipt_count"] = cdata.get("receipt_count", 0)
            entry["label_positions"] = cdata.get("label_positions", {})
            entry["constellations"] = cdata.get("constellations", [])
            entry["label_pairs"] = cdata.get("label_pairs", [])
        else:
            entry["receipt_count"] = 0
            entry["label_positions"] = {}
            entry["constellations"] = []
            entry["label_pairs"] = []

        # Extract a sample receipt with word bboxes for frontend rendering
        entry["sample_receipt"] = _extract_sample_receipt(
            rows_by_trace, entry["trace_ids"]
        )

        cache.append(entry)

    logger.info("Built cache for %d merchants", len(cache))
    return cache
