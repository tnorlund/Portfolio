#!/usr/bin/env python3
"""Emit MerchantTruth v1 DynamoDB payloads without making any writes."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import boto3

from receipt_dynamo.migrations.merchant_truth_v1 import (
    DEV_TABLE_NAME,
    ReadOnlyMerchantTruthSource,
    build_v1_payloads,
    write_dry_run_payloads,
)


def _git_sha(repo_root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=Path, required=True)
    parser.add_argument("--stylemap-root", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.home() / "section_label_kickoff" / "truth_v1_payloads",
    )
    parser.add_argument("--table-name", default=DEV_TABLE_NAME)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--git-sha")
    parser.add_argument("--generated-at")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    generated_at = args.generated_at or datetime.now(timezone.utc).isoformat()
    git_sha = args.git_sha or _git_sha(repo_root)
    with args.profiles.open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    source = ReadOnlyMerchantTruthSource(
        boto3.client("dynamodb", region_name=args.region),
        boto3.client("s3", region_name=args.region),
        args.table_name,
    )
    payloads, crosswalk = build_v1_payloads(
        document,
        source,
        profiles_source_path=str(args.profiles),
        git_sha=git_sha,
        generated_at=generated_at,
        stylemap_root=args.stylemap_root,
    )
    write_dry_run_payloads(
        args.output_dir,
        payloads,
        crosswalk,
        generated_at=generated_at,
        git_sha=git_sha,
    )
    print(
        f"DRY RUN: wrote {len(payloads)} merchant payloads to "
        f"{args.output_dir}"
    )
    print("No DynamoDB or S3 writes were performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
