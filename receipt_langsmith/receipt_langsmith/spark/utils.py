"""Shared Spark job utilities."""

from __future__ import annotations

import json
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
