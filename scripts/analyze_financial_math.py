#!/usr/bin/env python3
"""
Analyze financial math pass/fail from label evaluator step function results.

Downloads unified results from S3 and reports:
  - Overall pass/fail rate (balanced vs issues)
  - Merchant breakdown
  - Comparison between two executions
  - Per-receipt detail for failing receipts

Usage:
    # Analyze the latest successful execution
    python scripts/analyze_financial_math.py

    # Analyze a specific execution
    python scripts/analyze_financial_math.py --execution-id 6ae90b47-...

    # Compare two executions
    python scripts/analyze_financial_math.py --compare 764ae388-... 6ae90b47-...

    # Show per-receipt details for a merchant
    python scripts/analyze_financial_math.py --merchant "Sprouts Farmers Market"

Environment:
    Reads Pulumi outputs from the dev stack for bucket names and SF ARN.
    Requires AWS credentials with S3 read + Step Functions list access.
"""

import argparse
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import boto3

# ---------------------------------------------------------------------------
# Pulumi config helpers
# ---------------------------------------------------------------------------

script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))


def _load_pulumi_config():
    """Load batch bucket and SF ARN from Pulumi dev stack."""
    try:
        from receipt_dynamo.data._pulumi import load_env

        env = os.environ.get("PORTFOLIO_ENV", "dev")
        config = load_env(env=env)
        return {
            "batch_bucket": config.get(
                "label_evaluator_batch_bucket_name",
                "label-evaluator-dev-batch-bucket-a82b944",
            ),
            "sf_arn": config.get(
                "label_evaluator_sf_arn",
                "arn:aws:states:us-east-1:681647709217:stateMachine:label-evaluator-dev-sf",
            ),
        }
    except Exception:
        # Fallback to hardcoded dev values
        return {
            "batch_bucket": "label-evaluator-dev-batch-bucket-a82b944",
            "sf_arn": "arn:aws:states:us-east-1:681647709217:stateMachine:label-evaluator-dev-sf",
        }


# ---------------------------------------------------------------------------
# AWS helpers
# ---------------------------------------------------------------------------


def list_executions(sf_arn: str, limit: int = 10) -> list[dict]:
    """List recent step function executions."""
    client = boto3.client("stepfunctions", region_name="us-east-1")
    resp = client.list_executions(
        stateMachineArn=sf_arn, maxResults=limit
    )
    results = []
    for ex in resp["executions"]:
        results.append(
            {
                "execution_id": ex["name"],
                "status": ex["status"],
                "start": ex["startDate"].isoformat(),
                "stop": ex.get("stopDate", "").isoformat()
                if ex.get("stopDate")
                else None,
            }
        )
    return results


def get_latest_successful_execution(sf_arn: str) -> str | None:
    """Return the execution_id of the most recent SUCCEEDED run."""
    for ex in list_executions(sf_arn, limit=20):
        if ex["status"] == "SUCCEEDED":
            return ex["execution_id"]
    return None


def download_unified_results(
    bucket: str, execution_id: str, local_dir: str
) -> list[Path]:
    """Download all unified/*.json for an execution."""
    s3 = boto3.client("s3", region_name="us-east-1")
    prefix = f"unified/{execution_id}/"
    paginator = s3.get_paginator("list_objects_v2")

    files = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".json"):
                continue
            local_path = Path(local_dir) / Path(key).name
            s3.download_file(bucket, key, str(local_path))
            files.append(local_path)
    return files


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def analyze_execution(files: list[Path]) -> dict:
    """Analyze unified results and return structured stats."""
    total = 0
    no_financial = 0
    balanced = 0
    issues = 0

    merchant_balanced = Counter()
    merchant_issues = Counter()
    issue_receipts = []

    for f in files:
        with open(f) as fh:
            d = json.load(fh)

        total += 1
        decisions = d.get("financial_all_decisions", [])
        evaluated = d.get("financial_values_evaluated", 0)

        if evaluated == 0 and not decisions:
            no_financial += 1
            continue

        merchant = d.get("merchant_name", "Unknown")
        image_id = d.get("image_id", "")
        receipt_id = d.get("receipt_id", 0)

        has_issues = any(
            "fast_path_issues"
            in dec.get("llm_review", {}).get("reasoning", "")
            for dec in decisions
        )

        if has_issues:
            issues += 1
            merchant_issues[merchant] += 1
            issue_receipts.append(
                {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "merchant": merchant,
                    "financial_values_evaluated": evaluated,
                    "decision_count": len(decisions),
                }
            )
        else:
            balanced += 1
            merchant_balanced[merchant] += 1

    # Build merchant summary sorted by issue count
    all_merchants = set(merchant_issues.keys()) | set(merchant_balanced.keys())
    merchant_summary = []
    for m in all_merchants:
        i = merchant_issues.get(m, 0)
        b = merchant_balanced.get(m, 0)
        t = i + b
        merchant_summary.append(
            {
                "merchant": m,
                "issues": i,
                "balanced": b,
                "total": t,
                "fail_rate": round(i / t * 100, 1) if t > 0 else 0,
            }
        )
    merchant_summary.sort(key=lambda x: x["issues"], reverse=True)

    return {
        "total_receipts": total,
        "no_financial_data": no_financial,
        "with_financial_eval": balanced + issues,
        "balanced": balanced,
        "issues": issues,
        "pass_rate": round(balanced / (balanced + issues) * 100, 1)
        if (balanced + issues) > 0
        else 0,
        "merchant_summary": merchant_summary,
        "issue_receipts": issue_receipts,
    }


def print_report(stats: dict, execution_id: str):
    """Print a formatted report."""
    print(f"\n{'='*70}")
    print(f"Financial Math Analysis — Execution {execution_id[:12]}...")
    print(f"{'='*70}\n")

    print(f"Total receipts:          {stats['total_receipts']}")
    print(f"No financial data:       {stats['no_financial_data']}")
    print(f"With financial eval:     {stats['with_financial_eval']}")
    print(f"  Balanced (pass):       {stats['balanced']}")
    print(f"  Issues (fail):         {stats['issues']}")
    print(f"  Pass rate:             {stats['pass_rate']}%")

    print(f"\n{'─'*70}")
    print("Merchant Breakdown (sorted by issue count)")
    print(f"{'─'*70}")
    print(f"{'Merchant':<40} {'Issues':>6} {'Total':>6} {'Fail%':>6}")
    print(f"{'─'*40} {'─'*6} {'─'*6} {'─'*6}")
    for m in stats["merchant_summary"][:25]:
        if m["issues"] > 0:
            print(
                f"{m['merchant']:<40} {m['issues']:>6} {m['total']:>6} "
                f"{m['fail_rate']:>5.1f}%"
            )


def print_comparison(stats_a: dict, stats_b: dict, id_a: str, id_b: str):
    """Print a side-by-side comparison of two executions."""
    print(f"\n{'='*70}")
    print("Financial Math Comparison")
    print(f"{'='*70}\n")

    print(f"{'Metric':<30} {'Before':>12} {'After':>12} {'Delta':>10}")
    print(f"{'─'*30} {'─'*12} {'─'*12} {'─'*10}")

    rows = [
        ("Total receipts", "total_receipts"),
        ("With financial eval", "with_financial_eval"),
        ("Balanced (pass)", "balanced"),
        ("Issues (fail)", "issues"),
        ("Pass rate (%)", "pass_rate"),
    ]
    for label, key in rows:
        a = stats_a[key]
        b = stats_b[key]
        if isinstance(a, float):
            delta = f"{b - a:+.1f}"
        else:
            delta = f"{b - a:+d}"
        print(f"{label:<30} {a:>12} {b:>12} {delta:>10}")

    # Merchant diff
    merchants_a = {m["merchant"]: m for m in stats_a["merchant_summary"]}
    merchants_b = {m["merchant"]: m for m in stats_b["merchant_summary"]}
    all_merchants = set(merchants_a.keys()) | set(merchants_b.keys())

    diffs = []
    for m in all_merchants:
        ia = merchants_a.get(m, {}).get("issues", 0)
        ib = merchants_b.get(m, {}).get("issues", 0)
        if ia != ib:
            diffs.append((m, ia, ib, ib - ia))
    diffs.sort(key=lambda x: x[3])

    if diffs:
        print(f"\n{'─'*70}")
        print("Merchants with changed issue counts")
        print(f"{'─'*70}")
        print(f"{'Merchant':<40} {'Before':>7} {'After':>7} {'Delta':>7}")
        print(f"{'─'*40} {'─'*7} {'─'*7} {'─'*7}")
        for m, ia, ib, d in diffs:
            sign = "+" if d > 0 else ""
            print(f"{m:<40} {ia:>7} {ib:>7} {sign}{d:>6}")


def print_merchant_detail(stats: dict, merchant: str):
    """Print per-receipt details for a specific merchant."""
    receipts = [
        r for r in stats["issue_receipts"] if r["merchant"] == merchant
    ]
    if not receipts:
        print(f"\nNo failing receipts for '{merchant}'")
        return

    print(f"\n{'='*70}")
    print(f"Failing receipts for {merchant} ({len(receipts)} receipts)")
    print(f"{'='*70}\n")

    for r in sorted(receipts, key=lambda x: x["image_id"]):
        print(
            f"  {r['image_id']}/{r['receipt_id']}  "
            f"({r['financial_values_evaluated']} values, "
            f"{r['decision_count']} decisions)"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Analyze financial math results from label evaluator"
    )
    parser.add_argument(
        "--execution-id",
        help="Specific execution ID to analyze (default: latest successful)",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("BEFORE", "AFTER"),
        help="Compare two execution IDs",
    )
    parser.add_argument(
        "--merchant",
        help="Show per-receipt details for a specific merchant",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of formatted report",
    )
    parser.add_argument(
        "--list-executions",
        action="store_true",
        help="List recent executions and exit",
    )
    args = parser.parse_args()

    config = _load_pulumi_config()
    bucket = config["batch_bucket"]
    sf_arn = config["sf_arn"]

    def _log(msg: str, end: str = "\n"):
        """Print progress to stderr so stdout stays clean for --json."""
        print(msg, end=end, flush=True, file=sys.stderr)

    if args.list_executions:
        executions = list_executions(sf_arn)
        print(f"\n{'Status':<12} {'Execution ID':<40} {'Started'}")
        print(f"{'─'*12} {'─'*40} {'─'*25}")
        for ex in executions:
            print(
                f"{ex['status']:<12} {ex['execution_id']:<40} {ex['start'][:19]}"
            )
        return

    if args.compare:
        id_a, id_b = args.compare
        with tempfile.TemporaryDirectory() as tmpdir:
            dir_a = os.path.join(tmpdir, "a")
            dir_b = os.path.join(tmpdir, "b")
            os.makedirs(dir_a)
            os.makedirs(dir_b)

            _log(f"Downloading {id_a[:12]}...", end="")
            files_a = download_unified_results(bucket, id_a, dir_a)
            _log(f" {len(files_a)} files")

            _log(f"Downloading {id_b[:12]}...", end="")
            files_b = download_unified_results(bucket, id_b, dir_b)
            _log(f" {len(files_b)} files")

            stats_a = analyze_execution(files_a)
            stats_b = analyze_execution(files_b)

            if args.json:
                print(
                    json.dumps(
                        {"before": stats_a, "after": stats_b}, indent=2
                    )
                )
            else:
                print_comparison(stats_a, stats_b, id_a, id_b)
        return

    # Single execution analysis
    execution_id = args.execution_id
    if not execution_id:
        _log("Finding latest successful execution...", end="")
        execution_id = get_latest_successful_execution(sf_arn)
        if not execution_id:
            _log("\nNo successful executions found.")
            sys.exit(1)
        _log(f" {execution_id}")

    with tempfile.TemporaryDirectory() as tmpdir:
        _log("Downloading results...", end="")
        files = download_unified_results(bucket, execution_id, tmpdir)
        _log(f" {len(files)} files")

        stats = analyze_execution(files)

        if args.json:
            print(json.dumps(stats, indent=2))
        elif args.merchant:
            print_merchant_detail(stats, args.merchant)
        else:
            print_report(stats, execution_id)


if __name__ == "__main__":
    main()
