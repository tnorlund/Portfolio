"""Sync a previous training run's S3 outputs into the local checkpoint
directory so the trainer's auto-resume picks up where it left off.

The trainer writes checkpoints to ``/tmp/receipt_layoutlm/{job_name}/`` and
auto-resumes from the latest ``checkpoint-N/`` it finds there. To continue a
prior run, we sync the prior run's S3 output prefix into that directory
before calling ``trainer.train()``.
"""

from __future__ import annotations

import logging
import os
import posixpath
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

LOCAL_OUTPUT_ROOT = Path("/tmp/receipt_layoutlm")


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """Parse ``s3://bucket/key/prefix`` into ``(bucket, key_prefix)``.

    The returned prefix always has a trailing ``/`` so it matches a
    directory-style key.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Not a valid s3:// URI: {uri!r}")
    prefix = parsed.path.lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return parsed.netloc, prefix


def sync_resume_checkpoint(
    resume_from_s3: str,
    job_name: str,
    *,
    s3_client=None,
    local_root: Path = LOCAL_OUTPUT_ROOT,
) -> Path:
    """Download all objects under ``resume_from_s3`` into the job's local
    output directory.

    Args:
        resume_from_s3: ``s3://bucket/runs/<prior-job>/`` URI of a previous
            training run's output directory (the one containing
            ``checkpoint-N/`` subdirs).
        job_name: The current training job's name; files land in
            ``{local_root}/{job_name}/`` so HuggingFace Trainer's
            auto-resume in ``trainer.py`` finds them.
        s3_client: Optional pre-built boto3 S3 client (used by tests).
        local_root: Override the local output root (used by tests).

    Returns:
        The local directory the files were synced into.
    """
    if s3_client is None:
        import boto3

        s3_client = boto3.client("s3")

    bucket, prefix = _parse_s3_uri(resume_from_s3)

    # Resolve local_root once so we can verify every path we write lands
    # inside it. job_name comes from a hyperparameter and S3 keys come from
    # a (potentially attacker-influenced) bucket prefix — both need
    # containment checks to prevent path traversal via ".." segments.
    root = local_root.resolve()
    dest = (root / job_name).resolve()
    if dest == root or root not in dest.parents:
        raise ValueError(
            f"Invalid job_name {job_name!r}: would escape {root}"
        )
    dest.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Resume: syncing s3://%s/%s -> %s", bucket, prefix, dest
    )
    paginator = s3_client.get_paginator("list_objects_v2")
    count = 0
    skipped = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            key = obj["Key"]
            # Skip the "directory placeholder" objects S3 sometimes returns.
            if key.endswith("/"):
                continue
            rel = key[len(prefix):] if prefix else key
            # Normalize using POSIX semantics since S3 keys use forward
            # slashes regardless of host platform.
            rel = posixpath.normpath(rel.lstrip("/"))
            if rel.startswith("..") or rel.startswith("/") or rel == ".":
                logger.warning(
                    "Resume: skipping suspicious key %r", key
                )
                skipped += 1
                continue
            local_path = (dest / rel).resolve()
            if dest not in local_path.parents and local_path != dest:
                logger.warning(
                    "Resume: skipping key %r — resolved path escapes %s",
                    key,
                    dest,
                )
                skipped += 1
                continue
            local_path.parent.mkdir(parents=True, exist_ok=True)
            s3_client.download_file(bucket, key, str(local_path))
            count += 1

    logger.info("Resume: downloaded %d files into %s", count, dest)
    if count == 0:
        logger.warning(
            "Resume: no objects found at s3://%s/%s — trainer will start "
            "from scratch.",
            bucket,
            prefix,
        )
    return dest
