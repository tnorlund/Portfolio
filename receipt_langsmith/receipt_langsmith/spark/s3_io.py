"""Shared S3 JSON IO helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from receipt_langsmith.spark.utils import parse_s3_path


def load_json_from_s3(s3_client: Any, s3_path: str) -> Any:
    """Load a JSON payload from S3."""
    bucket, key = parse_s3_path(s3_path)
    response = s3_client.get_object(Bucket=bucket, Key=key)
    payload = response["Body"].read().decode("utf-8")
    return json.loads(payload)


def write_json_to_s3(
    s3_client: Any,
    bucket: str,
    key: str,
    payload: Any,
    *,
    dump_kwargs: dict[str, Any] | None = None,
) -> None:
    """Write a JSON payload to S3."""
    if dump_kwargs is None:
        dump_kwargs = {}

    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, **dump_kwargs),
        ContentType="application/json",
    )


def write_json_with_default(
    s3_client: Any,
    bucket: str,
    key: str,
    payload: Any,
    *,
    default: Any = str,
) -> None:
    """Write JSON to S3 with a default serializer."""
    write_json_to_s3(
        s3_client,
        bucket,
        key,
        payload,
        dump_kwargs={"default": default},
    )


def write_receipt_json(
    s3_client: Any,
    bucket: str,
    key: str,
    receipt: Any,
) -> None:
    """Write a receipt payload using the default serializer."""
    write_json_with_default(s3_client, bucket, key, receipt)


def write_metadata_json(
    s3_client: Any,
    bucket: str,
    payload: Any,
) -> None:
    """Write metadata.json with pretty JSON formatting."""
    write_json_to_s3(
        s3_client,
        bucket,
        "metadata.json",
        payload,
        dump_kwargs={"indent": 2},
    )


def write_latest_json(
    s3_client: Any,
    bucket: str,
    payload: Any,
    *,
    indent: int | None = 2,
) -> None:
    """Write latest.json with optional pretty formatting."""
    dump_kwargs: dict[str, Any] = {}
    if indent is not None:
        dump_kwargs["indent"] = indent
    write_json_to_s3(
        s3_client,
        bucket,
        "latest.json",
        payload,
        dump_kwargs=dump_kwargs,
    )


def build_latest_receipts_pointer(
    version: str,
    receipts_prefix: str,
    updated_at: str,
) -> dict[str, str]:
    """Build latest.json pointer payload for receipt caches."""
    return {
        "version": version,
        "receipts_prefix": receipts_prefix,
        "metadata_key": "metadata.json",
        "updated_at": updated_at,
    }


@dataclass(frozen=True)
class ReceiptsCachePointer:
    """Metadata needed to write a receipts cache pointer."""

    version: str
    receipts_prefix: str
    updated_at: str


def write_receipt_cache_index(
    s3_client: Any,
    bucket: str,
    metadata: dict[str, Any],
    pointer: ReceiptsCachePointer,
) -> None:
    """Write metadata.json and latest.json for receipt caches."""
    write_metadata_json(s3_client, bucket, metadata)
    latest = build_latest_receipts_pointer(
        pointer.version,
        pointer.receipts_prefix,
        pointer.updated_at,
    )
    write_latest_json(s3_client, bucket, latest)
