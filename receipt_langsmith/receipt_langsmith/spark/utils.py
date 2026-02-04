"""Shared Spark job utilities."""

from __future__ import annotations


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
