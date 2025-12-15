"""Shared helpers for polling handlers to reduce duplication."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, Optional, Tuple


def parse_line_custom_id(custom_id: str) -> Dict[str, Any]:
    """Parse metadata from a line ID in format IMAGE#<id>#RECEIPT#<id>#LINE#<id>."""
    parts = custom_id.split("#")
    if len(parts) != 6:
        raise ValueError(
            f"Invalid custom_id format for line embedding: {custom_id}. "
            f"Expected format: IMAGE#<id>#RECEIPT#<id>#LINE#<id> (6 parts), "
            f"but got {len(parts)} parts"
        )
    return {
        "image_id": parts[1],
        "receipt_id": int(parts[3]),
        "line_id": int(parts[5]),
        "source": "openai_line_embedding_batch",
    }


def parse_word_custom_id(custom_id: str) -> Dict[str, Any]:
    """Parse metadata from a word ID in format IMAGE#<id>#RECEIPT#<id>#LINE#<id>#WORD#<id>."""
    parts = custom_id.split("#")
    if len(parts) != 8 or parts[4] != "LINE" or parts[6] != "WORD":
        raise ValueError(
            f"Invalid custom_id format for word embedding: {custom_id}. "
            f"Expected IMAGE#<id>#RECEIPT#<id>#LINE#<id>#WORD#<id>"
        )
    return {
        "image_id": parts[1],
        "receipt_id": int(parts[3]),
        "line_id": int(parts[5]),
        "word_id": int(parts[7]),
        "source": "openai_word_embedding_batch",
    }


def resolve_batch_info(
    event: Dict[str, Any],
    logger: Any,
    s3_client: Any,
    *,
    handler_label: str,
) -> Tuple[str, str, Optional[int]]:
    """Resolve batch_id/openai_batch_id from manifest, pending_batches, or inline event."""
    manifest_s3_key = event.get("manifest_s3_key")
    manifest_s3_bucket = event.get("manifest_s3_bucket")
    batch_index = event.get("batch_index")
    pending_batches = event.get("pending_batches")

    if (
        manifest_s3_key
        and manifest_s3_bucket is not None
        and batch_index is not None
    ):
        logger.info(
            "Loading batch info from S3 manifest",
            manifest_s3_key=manifest_s3_key,
            manifest_s3_bucket=manifest_s3_bucket,
            batch_index=batch_index,
            handler=handler_label,
        )

        fd, tmp_file_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)

        try:
            s3_client.download_file(
                manifest_s3_bucket, manifest_s3_key, tmp_file_path
            )
            with open(tmp_file_path, "r", encoding="utf-8") as manifest_file:
                manifest = json.load(manifest_file)

            if not isinstance(manifest, dict) or "batches" not in manifest:
                raise ValueError(
                    "Invalid manifest format: expected dict with 'batches' key"
                )

            batches = manifest.get("batches", [])
            if not isinstance(batches, list):
                raise TypeError(
                    "Invalid manifest format: 'batches' must be a list"
                )

            max_idx = len(batches) - 1 if batches else "N/A (empty list)"
            if batch_index < 0 or batch_index >= len(batches):
                raise ValueError(
                    f"batch_index {batch_index} out of range (0-{max_idx})"
                )

            batch_info = batches[batch_index]
            batch_id = batch_info["batch_id"]
            openai_batch_id = batch_info["openai_batch_id"]

            logger.info(
                "Loaded batch info from manifest",
                batch_id=batch_id,
                openai_batch_id=openai_batch_id,
                batch_index=batch_index,
                handler=handler_label,
            )
        finally:
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass
    elif pending_batches is not None and batch_index is not None:
        if not isinstance(pending_batches, list):
            raise ValueError(
                f"pending_batches must be a list, got {type(pending_batches).__name__}"
            )

        if batch_index < 0 or batch_index >= len(pending_batches):
            raise ValueError(
                f"batch_index {batch_index} out of range (0-{len(pending_batches)-1})"
            )

        batch_info = pending_batches[batch_index]
        batch_id = batch_info["batch_id"]
        openai_batch_id = batch_info["openai_batch_id"]

        logger.info(
            "Using batch info from inline pending_batches",
            batch_id=batch_id,
            openai_batch_id=openai_batch_id,
            batch_index=batch_index,
            handler=handler_label,
        )
    else:
        batch_id = event["batch_id"]
        openai_batch_id = event["openai_batch_id"]
        logger.info(
            "Using batch info directly from event (backward compatible)",
            batch_id=batch_id,
            openai_batch_id=openai_batch_id,
            handler=handler_label,
        )

    return batch_id, openai_batch_id, batch_index
