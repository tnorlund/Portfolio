#!/usr/bin/env python3
"""Emit MerchantTruth v1 DynamoDB payloads; optionally mint+seal with --live.

Default behavior is unchanged and DRY-RUN ONLY: build payloads through the
structurally read-only source adapter and write inspection files. With an
explicit ``--live`` flag the same payloads are minted and sealed through the
mutation-tested ``DynamoClient`` accessor (never through the read adapter),
excluding asset-blocked merchants. ``--verify`` reads every minted bundle
back through ``MerchantTruthLoader`` and byte-compares canonical component
payloads against the dry-run files, exiting nonzero on any mismatch.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import boto3

from receipt_dynamo import DynamoClient
from receipt_dynamo.migrations.merchant_truth_v1 import (
    DEV_TABLE_NAME,
    ReadOnlyMerchantTruthSource,
    build_v1_payloads,
    write_dry_run_payloads,
)
from receipt_dynamo.migrations.merchant_truth_v1_live import (
    bootstrap_gate_results,
    format_live_banner,
    run_live_mint,
    validate_live_table,
    verify_live_against_dry_run,
)


def _git_sha(repo_root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=Path, required=True)
    parser.add_argument("--stylemap-root", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.home() / "section_label_kickoff" / "truth_v1_payloads",
    )
    parser.add_argument(
        "--table",
        "--table-name",
        dest="table_name",
        default=None,
        help=(
            f"DynamoDB table (default: exact dev table {DEV_TABLE_NAME}). "
            "Live mode refuses any other table unless passed explicitly; "
            "the prod table is refused unconditionally."
        ),
    )
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--git-sha")
    parser.add_argument("--generated-at")
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Mint + seal v1 for every unblocked merchant through the "
            "DynamoClient accessor. Without this flag no DynamoDB write "
            "ever happens."
        ),
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help=(
            "Read minted bundles back through MerchantTruthLoader and "
            "byte-compare canonical component payloads against the dry-run "
            "payload files; exit nonzero on any mismatch."
        ),
    )
    args = parser.parse_args(argv)

    explicit_table = args.table_name is not None
    table_name = args.table_name or DEV_TABLE_NAME
    # Unconditional on every path (including the plain dry run): prod
    # refusal must precede profile reads and boto3 client construction.
    validate_live_table(table_name, explicit=explicit_table)

    repo_root = Path(__file__).resolve().parents[1]
    generated_at = args.generated_at or datetime.now(timezone.utc).isoformat()
    git_sha = args.git_sha or _git_sha(repo_root)
    with args.profiles.open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    source = ReadOnlyMerchantTruthSource(
        boto3.client("dynamodb", region_name=args.region),
        boto3.client("s3", region_name=args.region),
        table_name,
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
    minted_slugs = sorted(
        payload.slug for payload in payloads if not payload.blockers
    )

    client: DynamoClient | None = None
    if args.live:
        print(
            format_live_banner(table_name, len(minted_slugs), git_sha),
            flush=True,
        )
        client = DynamoClient(table_name, region=args.region)
        results = run_live_mint(
            client,
            payloads,
            table_name=table_name,
            gate_results=bootstrap_gate_results(
                git_sha=git_sha,
                generated_at=generated_at,
                payload_dir=args.output_dir,
            ),
            generated_at=generated_at,
            explicit_table=explicit_table,
        )
        for result in results:
            print(result.report_line)
        minted = [r for r in results if r.action == "MINTED_SEALED"]
        excluded = [r for r in results if r.action == "EXCLUDED"]
        print(
            f"LIVE: minted+sealed {len(minted)} merchants on {table_name}; "
            f"excluded {len(excluded)} asset-blocked merchants; dry-run "
            f"payloads written to {args.output_dir}"
        )
    else:
        print(
            f"DRY RUN: wrote {len(payloads)} merchant payloads to "
            f"{args.output_dir}"
        )
        print("No DynamoDB or S3 writes were performed.")

    if args.verify:
        if client is None:
            client = DynamoClient(table_name, region=args.region)
        verify_results = verify_live_against_dry_run(
            client,
            args.output_dir,
            minted_slugs,
            work_dir=args.output_dir / "_live_verify",
        )
        for verify_result in verify_results:
            print(verify_result.report_line)
        failures = [entry for entry in verify_results if not entry.ok]
        if failures:
            print(
                f"VERIFY FAILED: {len(failures)}/{len(verify_results)} live "
                "bundles do not match the dry-run payloads"
            )
            return 1
        print(
            f"VERIFY OK: {len(verify_results)} live bundles byte-match the "
            "dry-run payloads"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
