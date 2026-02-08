"""Helper utilities for dedup/conflict resolution visualization cache."""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from receipt_langsmith.spark.utils import parse_json_object, to_s3a

logger = logging.getLogger(__name__)


def build_dedup_cache(
    parquet_dir: str | None = None,
    *,
    rows: list[dict[str, Any]] | None = None,
) -> list[dict]:
    """Build dedup conflict resolution cache from LangSmith trace parquet.

    Reads all parquet files under *parquet_dir*, finds ``ReceiptEvaluation``
    root spans, extracts ``apply_phase1_corrections`` child spans, and
    returns one dict per receipt with dedup stats and resolution details.

    Args:
        parquet_dir: Local directory containing LangSmith parquet exports.
        rows: Optional preloaded trace rows.

    Returns:
        List of per-receipt dicts with dedup stats and resolutions.
    """
    if rows is None:
        if parquet_dir is None:
            raise ValueError("Either parquet_dir or rows must be provided")
        rows = _read_parquet_rows(parquet_dir)
    if not rows:
        if parquet_dir is None:
            logger.warning("No rows provided to build_dedup_cache")
        else:
            logger.warning("No rows found in %s", parquet_dir)
        return []

    roots, children_by_trace = _partition_spans(rows)
    logger.info(
        "Found %d ReceiptEvaluation roots, %d total child spans",
        len(roots),
        sum(len(v) for v in children_by_trace.values()),
    )

    results: list[dict] = []
    for root in roots:
        receipt = _build_receipt_entry(root, children_by_trace)
        if receipt is not None:
            results.append(receipt)

    logger.info("Built dedup cache for %d receipts", len(results))
    return results


# ---------------------------------------------------------------------------
# Parquet I/O
# ---------------------------------------------------------------------------


def _read_parquet_rows(parquet_dir: str) -> list[dict[str, Any]]:
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
        s3_rows = [row.asDict(recursive=True) for row in df.toLocalIterator()]
        logger.info(
            "Read %d rows from S3 parquet path %s", len(s3_rows), parquet_dir
        )
        return s3_rows

    from pathlib import Path  # pylint: disable=import-outside-toplevel

    import pyarrow.parquet as pq  # pylint: disable=import-outside-toplevel

    root = Path(parquet_dir)
    files = [root] if root.is_file() else sorted(root.rglob("*.parquet"))
    if not files:
        logger.warning("No parquet files found in %s", parquet_dir)
        return []

    local_rows: list[dict[str, Any]] = []
    for path in files:
        try:
            table = pq.ParquetFile(str(path)).read()
            local_rows.extend(table.to_pylist())
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Failed to read parquet file %s", path)
    return local_rows


# ---------------------------------------------------------------------------
# Span partitioning
# ---------------------------------------------------------------------------


def _partition_spans(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Split rows into root spans and children grouped by trace_id."""
    roots: list[dict[str, Any]] = []
    children_by_trace: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        name = row.get("name", "")
        trace_id = row.get("trace_id")
        if not trace_id:
            # Some exports omit trace_id on rows where id is still stable per
            # trace; keep this fallback so children remain grouped with roots.
            trace_id = row.get("id", "")

        if name == "ReceiptEvaluation" and _is_root(row):
            roots.append(row)
        else:
            if trace_id:
                children_by_trace.setdefault(trace_id, []).append(row)

    return roots, children_by_trace


def _is_root(row: dict[str, Any]) -> bool:
    """Return True when the row looks like a root span."""
    if row.get("is_root"):
        return True
    return row.get("parent_run_id") in (None, "")


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------


def _extract_metadata(
    row: dict[str, Any],
) -> tuple[str, int | None, str]:
    """Extract (image_id, receipt_id, merchant_name) from extra.metadata."""
    extra = parse_json_object(row.get("extra"))
    metadata = extra.get("metadata", {}) if isinstance(extra, dict) else {}

    image_id = metadata.get("image_id", "")
    receipt_id_raw = metadata.get("receipt_id")
    merchant_name = metadata.get("merchant_name", "")

    receipt_id: int | None = None
    if receipt_id_raw is not None:
        try:
            receipt_id = int(receipt_id_raw)
        except (ValueError, TypeError):
            pass

    return image_id, receipt_id, merchant_name


# ---------------------------------------------------------------------------
# Per-receipt assembly
# ---------------------------------------------------------------------------


def _build_receipt_entry(
    root: dict[str, Any],
    children_by_trace: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    """Assemble a single receipt entry from a root span and its children."""
    image_id, receipt_id, merchant_name = _extract_metadata(root)
    if not image_id:
        return None

    trace_id = root.get("trace_id") or root.get("id", "")
    children = children_by_trace.get(trace_id, [])

    phase1_span = _find_phase1_span(children)

    if phase1_span is None:
        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
            "trace_id": trace_id,
            "dedup_stats": None,
            "resolutions": [],
            "summary": _build_summary([]),
        }

    inputs = parse_json_object(phase1_span.get("inputs"))
    outputs = parse_json_object(phase1_span.get("outputs"))
    resolutions = _normalize_resolutions(
        outputs.get("resolutions", []), trace_id
    )

    dedup_stats = {
        "currency_invalid_count": inputs.get("currency_invalid_count", 0),
        "metadata_invalid_count": inputs.get("metadata_invalid_count", 0),
        "overlapping_words": inputs.get("overlapping_words", 0),
        "conflicting_words": inputs.get("conflicting_words", 0),
        "dedup_removed": outputs.get("dedup_removed", 0),
        "total_corrections_applied": outputs.get(
            "total_corrections_applied", 0
        ),
        "resolution_strategy": outputs.get("resolution_strategy", ""),
    }

    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant_name": merchant_name,
        "trace_id": trace_id,
        "dedup_stats": dedup_stats,
        "resolutions": resolutions,
        "summary": _build_summary(resolutions),
    }


def _normalize_resolutions(
    raw_resolutions: Any,
    trace_id: str,
) -> list[dict[str, Any]]:
    """Normalize resolutions payload to a safe list of dicts."""
    if isinstance(raw_resolutions, dict):
        candidates = [raw_resolutions]
    elif isinstance(raw_resolutions, list):
        candidates = raw_resolutions
    else:
        logger.warning(
            "Ignoring non-list resolutions payload for trace %s (type=%s)",
            trace_id,
            type(raw_resolutions).__name__,
        )
        return []

    normalized = [item for item in candidates if isinstance(item, dict)]
    dropped = len(candidates) - len(normalized)
    if dropped:
        logger.warning(
            "Dropped %d non-dict resolution item(s) for trace %s",
            dropped,
            trace_id,
        )
    return normalized


def _find_phase1_span(
    children: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the ``apply_phase1_corrections`` child span."""
    for child in children:
        if child.get("name") == "apply_phase1_corrections":
            return child
    return None


# ---------------------------------------------------------------------------
# Summary computation
# ---------------------------------------------------------------------------


def _build_summary(resolutions: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a summary dict from the resolutions list."""
    has_conflicts = len(resolutions) > 0

    resolution_reasons = Counter(
        r.get("resolution_reason", "") for r in resolutions
    )
    winner_counts = Counter(r.get("winner", "") for r in resolutions)
    labels_affected = sorted(
        {r.get("current_label", "") for r in resolutions} - {""}
    )

    return {
        "has_conflicts": has_conflicts,
        "resolution_breakdown": {
            "higher_confidence": resolution_reasons.get(
                "higher_confidence", 0
            ),
            "financial_label_priority": resolution_reasons.get(
                "financial_label_priority", 0
            ),
            "currency_priority_default": resolution_reasons.get(
                "currency_priority_default", 0
            ),
        },
        "winner_breakdown": {
            "currency": winner_counts.get("currency", 0),
            "metadata": winner_counts.get("metadata", 0),
        },
        "labels_affected": labels_affected,
    }
