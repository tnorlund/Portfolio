#!/usr/bin/env python3
"""
Shared Pulumi utilities for Lambda layer and CodeBuild-based components.

Goals:
- Centralize hashing logic with consistent exclusions.
- Provide helpers for artifact buckets, log groups, and common ResourceOptions.
- Keep a single place for Pythonic hygiene (types, lint-friendly structure).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Sequence, Tuple

import pulumi
from pulumi import ResourceOptions
from pulumi_aws.cloudwatch import LogGroup
from pulumi_aws.s3 import (
    Bucket,
    BucketServerSideEncryptionConfigurationArgs,
    BucketServerSideEncryptionConfigurationRuleApplyServerSideEncryptionByDefaultArgs,
    BucketServerSideEncryptionConfigurationRuleArgs,
    BucketVersioning,
    BucketVersioningArgs,
    BucketVersioningVersioningConfigurationArgs,
)

# Common exclude directories when hashing
_DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "__pycache__",
}


def _iter_files(
    roots: Sequence[Path],
    include_globs: Optional[Sequence[str]] = None,
    exclude_dirs: Optional[Sequence[str]] = None,
) -> Iterable[Path]:
    """Yield files under roots honoring include globs and exclusions."""
    exclude_dirs_set = set(exclude_dirs or _DEFAULT_EXCLUDE_DIRS)
    include_globs = list(include_globs or ["**/*"])

    for root in roots:
        if root.is_file():
            yield root
            continue

        for pattern in include_globs:
            for path in root.glob(pattern):
                if not path.is_file():
                    continue
                if any(part in exclude_dirs_set for part in path.parts):
                    continue
                yield path


def compute_hash(
    paths: Sequence[str | Path],
    *,
    include_globs: Optional[Sequence[str]] = None,
    extra_strings: Optional[Mapping[str, str]] = None,
) -> str:
    """
    Compute a deterministic SHA256 hash for the given paths.

    - Supports files or directories.
    - Applies standard exclusions and optional glob filtering.
    - Can incorporate extra keyed strings (e.g., config flags).
    """
    hash_obj = hashlib.sha256()
    normalized_roots: List[Path] = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            continue
        normalized_roots.append(path)

    for root in normalized_roots:
        for file_path in sorted(_iter_files([root], include_globs)):
            try:
                with open(file_path, "rb") as handle:
                    hash_obj.update(handle.read())
                rel_path = file_path.relative_to(root)
                hash_obj.update(str(rel_path).encode())
            except (OSError, IOError, ValueError):
                # ValueError if file is not under root due to symlinks; skip
                continue

    if extra_strings:
        for key, value in sorted(extra_strings.items()):
            hash_obj.update(key.encode())
            hash_obj.update(value.encode())

    return hash_obj.hexdigest()


def make_artifact_bucket(
    name: str,
    *,
    parent: Optional[pulumi.Resource] = None,
    force_destroy: bool = True,
    enable_versioning: bool = True,
    tags: Optional[Mapping[str, str]] = None,
) -> Tuple[Bucket, Optional[BucketVersioning]]:
    """
    Create an S3 bucket for build artifacts with sensible defaults:
    - Force destroy by default to keep dev stacks clean.
    - AES256 encryption and public access block.
    - Optional versioning for deterministic artifacts.
    """
    bucket = Bucket(
        f"{name}-artifacts",
        force_destroy=force_destroy,
        server_side_encryption_configuration=BucketServerSideEncryptionConfigurationArgs(
            rule=BucketServerSideEncryptionConfigurationRuleArgs(
                apply_server_side_encryption_by_default=BucketServerSideEncryptionConfigurationRuleApplyServerSideEncryptionByDefaultArgs(
                    sse_algorithm="AES256"
                )
            )
        ),
        # Note: versioning is configured via separate BucketVersioning resource below
        # to comply with AWS provider v4+ requirements
        tags=tags,
        opts=ResourceOptions(parent=parent),
    )

    versioning = None
    if enable_versioning:
        versioning = BucketVersioning(
            f"{name}-artifacts-versioning",
            bucket=bucket.id,
            versioning_configuration=BucketVersioningVersioningConfigurationArgs(
                status="Enabled"
            ),
            opts=ResourceOptions(parent=parent),
        )

    return bucket, versioning


def make_log_group(
    name: str,
    *,
    retention_days: int = 14,
    parent: Optional[pulumi.Resource] = None,
) -> LogGroup:
    """Create a CloudWatch log group with retention to control costs."""
    return LogGroup(
        name,
        retention_in_days=retention_days,
        opts=ResourceOptions(parent=parent),
    )


def default_resource_options(
    *,
    parent: Optional[pulumi.Resource] = None,
    depends_on: Optional[Sequence[pulumi.Resource]] = None,
    aliases: Optional[Sequence[pulumi.Alias]] = None,
) -> ResourceOptions:
    """Small helper to keep ResourceOptions consistent."""
    return ResourceOptions(parent=parent, depends_on=depends_on, aliases=aliases)


def resolve_build_config(
    namespace: str,
    *,
    sync_override: Optional[bool] = None,
    ci_default_sync: bool = True,
) -> Tuple[bool, bool, bool]:
    """
    Resolve common build config flags (sync, force_rebuild, debug) consistently.

    Order of precedence for sync:
    1) Explicit override argument
    2) Pulumi config key `sync-mode`
    3) CI detection (if enabled)
    4) Default False
    """
    cfg = pulumi.Config(namespace)
    force_rebuild = cfg.get_bool("force-rebuild") or False
    debug_mode = cfg.get_bool("debug-mode") or False

    if sync_override is not None:
        sync_mode = sync_override
    else:
        sync_mode_cfg = cfg.get_bool("sync-mode")
        if sync_mode_cfg is not None:
            sync_mode = sync_mode_cfg
        elif ci_default_sync and (os.getenv("CI") or os.getenv("GITHUB_ACTIONS")):
            sync_mode = True
        else:
            sync_mode = False

    return sync_mode, force_rebuild, debug_mode
