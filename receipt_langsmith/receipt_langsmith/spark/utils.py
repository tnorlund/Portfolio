"""Shared Spark job utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def to_s3a(path: str) -> str:
    """Convert s3:// URIs to s3a:// for Spark."""
    if path.startswith("s3://"):
        return f"s3a://{path[5:]}"
    return path


def parse_s3_path(s3_path: str) -> tuple[str, str]:
    """Parse an s3://bucket/key path into bucket and key."""
    if not s3_path.startswith("s3://"):
        raise ValueError(f"Invalid S3 path: {s3_path}")
    path = s3_path[5:]
    if "/" not in path:
        raise ValueError(f"Invalid S3 path: {s3_path}")
    bucket, key = path.split("/", 1)
    if not bucket or not key:
        raise ValueError(f"Invalid S3 path: {s3_path}")
    return bucket, key


TRACE_BASE_COLUMNS: list[str] = [
    "id",
    "trace_id",
    "parent_run_id",
    "name",
    "run_type",
    "status",
]


def parse_json_object(payload: Any) -> dict[str, Any]:
    """Parse a JSON object from a dict or JSON string."""
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def read_all_parquet_rows(parquet_dir: str) -> list[dict[str, Any]]:
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

    root = Path(parquet_dir)
    files = [root] if root.is_file() else sorted(root.rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet files found under {parquet_dir}")

    import pyarrow.parquet as pq  # pylint: disable=import-outside-toplevel

    rows: list[dict[str, Any]] = []
    for path in files:
        table = pq.ParquetFile(str(path)).read()
        rows.extend(table.to_pylist())
    return rows
