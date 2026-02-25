#!/usr/bin/env python3
"""Pull financial validation results from the latest label evaluator run.

Reads receipt JSONs from the viz-cache S3 bucket, re-computes the financial
math equations locally from each receipt's words, and reports which receipts
pass or fail with the actual equation mismatches.

Usage:
    # Default: use Pulumi dev stack outputs to find bucket + latest execution
    python scripts/financial_validation_report.py

    # Override bucket
    python scripts/financial_validation_report.py \
        --bucket label-evaluator-dev-viz-cache-XXXXX

    # Output as JSON for downstream processing
    python scripts/financial_validation_report.py --json

    # Show only failures
    python scripts/financial_validation_report.py --failures-only
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.exceptions import ClientError

FLOAT_EPSILON = 0.001

FINANCIAL_LABELS = {
    "GRAND_TOTAL",
    "SUBTOTAL",
    "TAX",
    "TIP",
    "LINE_TOTAL",
    "UNIT_PRICE",
    "QUANTITY",
    "DISCOUNT",
}


# ---------------------------------------------------------------------------
# Number extraction (mirrors financial_subagent.extract_number)
# ---------------------------------------------------------------------------

def extract_number(text):
    """Extract numeric value from text, handling currency symbols and formatting."""
    if not text:
        return None
    clean = text.strip()
    clean = re.sub(r"[$€£¥]", "", clean)
    clean = clean.replace(",", "")

    is_negative = False
    if clean.startswith("(") and clean.endswith(")"):
        is_negative = True
        clean = clean[1:-1]
    if clean.endswith("-"):
        is_negative = True
        clean = clean[:-1]
    if clean.startswith("-"):
        is_negative = True
        clean = clean[1:]

    try:
        value = float(clean)
        return -value if is_negative else value
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Local math checks (mirrors financial_subagent check_* functions)
# ---------------------------------------------------------------------------

def extract_financial_values(words):
    """Group receipt words by financial label with parsed numeric values.

    Returns dict mapping label -> list of {value, text, line_id, word_id}.
    """
    values = defaultdict(list)
    for w in words:
        label = w.get("label", "")
        if label not in FINANCIAL_LABELS:
            continue
        num = extract_number(w.get("text", ""))
        if num is None:
            continue
        values[label].append({
            "value": num,
            "text": w.get("text", ""),
            "line_id": w.get("line_id"),
            "word_id": w.get("word_id"),
        })
    return dict(values)


def check_grand_total(values):
    """GRAND_TOTAL = SUBTOTAL + TAX + TIP - |DISCOUNT|"""
    gts = values.get("GRAND_TOTAL", [])
    sts = values.get("SUBTOTAL", [])
    if not gts or not sts:
        return None

    gt = gts[0]["value"]
    subtotal_sum = sum(v["value"] for v in sts)
    tax_sum = sum(v["value"] for v in values.get("TAX", []))
    tip_sum = sum(v["value"] for v in values.get("TIP", []))
    discount_sum = sum(abs(v["value"]) for v in values.get("DISCOUNT", []))

    expected = subtotal_sum + tax_sum + tip_sum - discount_sum
    diff = gt - expected
    if abs(diff) <= FLOAT_EPSILON:
        return None

    parts = "GRAND_TOTAL (%.2f) != SUBTOTAL (%.2f) + TAX (%.2f)" % (
        gt, subtotal_sum, tax_sum,
    )
    if tip_sum:
        parts += " + TIP (%.2f)" % tip_sum
    if discount_sum:
        parts += " - DISCOUNT (%.2f)" % discount_sum
    parts += " = %.2f. Difference: %.2f" % (expected, diff)

    return {
        "issue_type": "GRAND_TOTAL_MISMATCH",
        "expected": expected,
        "actual": gt,
        "difference": diff,
        "description": parts,
    }


def check_grand_total_direct(values):
    """GRAND_TOTAL = sum(LINE_TOTAL) + TAX + TIP - |DISCOUNT| (when no SUBTOTAL)."""
    gts = values.get("GRAND_TOTAL", [])
    sts = values.get("SUBTOTAL", [])
    lts = values.get("LINE_TOTAL", [])
    if not gts or sts or not lts:
        return None

    gt = gts[0]["value"]
    lt_sum = sum(v["value"] for v in lts)
    tax_sum = sum(v["value"] for v in values.get("TAX", []))
    tip_sum = sum(v["value"] for v in values.get("TIP", []))
    discount_sum = sum(abs(v["value"]) for v in values.get("DISCOUNT", []))

    expected = lt_sum + tax_sum + tip_sum - discount_sum
    diff = gt - expected
    if abs(diff) <= FLOAT_EPSILON:
        return None

    parts = "GRAND_TOTAL (%.2f) != sum(LINE_TOTAL) (%.2f) + TAX (%.2f)" % (
        gt, lt_sum, tax_sum,
    )
    if tip_sum:
        parts += " + TIP (%.2f)" % tip_sum
    if discount_sum:
        parts += " - DISCOUNT (%.2f)" % discount_sum
    parts += " = %.2f. Difference: %.2f" % (expected, diff)

    return {
        "issue_type": "GRAND_TOTAL_DIRECT_MISMATCH",
        "expected": expected,
        "actual": gt,
        "difference": diff,
        "description": parts,
    }


def check_subtotal(values):
    """SUBTOTAL = sum(LINE_TOTAL) - |DISCOUNT|"""
    sts = values.get("SUBTOTAL", [])
    lts = values.get("LINE_TOTAL", [])
    if not sts or not lts:
        return None

    st = sts[0]["value"]
    lt_sum = sum(v["value"] for v in lts)
    discount_sum = sum(abs(v["value"]) for v in values.get("DISCOUNT", []))

    expected = lt_sum - discount_sum
    diff = st - expected
    if abs(diff) <= FLOAT_EPSILON:
        return None

    return {
        "issue_type": "SUBTOTAL_MISMATCH",
        "expected": expected,
        "actual": st,
        "difference": diff,
        "description": (
            "SUBTOTAL (%.2f) != sum(LINE_TOTAL) (%.2f) - DISCOUNT (%.2f)"
            " = %.2f. Difference: %.2f" % (st, lt_sum, discount_sum, expected, diff)
        ),
    }


def check_line_items(values):
    """QTY x UNIT_PRICE = LINE_TOTAL per line."""
    issues = []
    by_line = defaultdict(dict)
    for label in ("QUANTITY", "UNIT_PRICE", "LINE_TOTAL"):
        for v in values.get(label, []):
            by_line[v["line_id"]][label] = v

    for line_id, lv in by_line.items():
        qty = lv.get("QUANTITY")
        up = lv.get("UNIT_PRICE")
        lt = lv.get("LINE_TOTAL")
        if not qty or not up or not lt:
            continue

        expected = qty["value"] * up["value"]
        actual = lt["value"]
        diff = actual - expected
        if abs(diff) <= FLOAT_EPSILON:
            continue

        issues.append({
            "issue_type": "LINE_ITEM_MISMATCH",
            "expected": expected,
            "actual": actual,
            "difference": diff,
            "description": (
                "Line %s: LINE_TOTAL (%.2f) != QTY (%s) x UNIT_PRICE (%.2f)"
                " = %.2f. Difference: %.2f"
                % (line_id, actual, qty["text"], up["value"], expected, diff)
            ),
        })
    return issues


def compute_math_issues(receipt):
    """Re-compute equation-level math issues from receipt words."""
    words = receipt.get("words", [])
    values = extract_financial_values(words)

    if not values:
        return [], values

    issues = []

    gt_issue = check_grand_total(values)
    if gt_issue:
        issues.append(gt_issue)

    gt_direct = check_grand_total_direct(values)
    if gt_direct:
        issues.append(gt_direct)

    st_issue = check_subtotal(values)
    if st_issue:
        issues.append(st_issue)

    li_issues = check_line_items(values)
    issues.extend(li_issues)

    return issues, values


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------

def get_pulumi_output(key, stack="dev"):
    """Read a single Pulumi stack output."""
    result = subprocess.run(
        ["pulumi", "stack", "output", key, "--stack", stack],
        capture_output=True,
        text=True,
        cwd=subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        ).stdout.strip() + "/infra",
    )
    return result.stdout.strip()


def list_receipt_keys(s3, bucket, prefix="receipts/"):
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json") and "metadata" not in key:
                keys.append(key)
    return keys


def fetch_receipt(s3, bucket, key):
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(resp["Body"].read().decode("utf-8"))
    except (ClientError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_receipt(receipt):
    """Classify receipt and compute equation-level failure reasons."""
    fin = receipt.get("financial")
    if not fin or not isinstance(fin, dict):
        return "NO_FINANCIAL_DATA", [], {}

    decisions = fin.get("decisions", {})
    invalid = decisions.get("INVALID", 0)
    needs_review = decisions.get("NEEDS_REVIEW", 0)
    valid = decisions.get("VALID", 0)

    if invalid == 0 and needs_review == 0 and valid == 0:
        return "NO_FINANCIAL_LABELS", [], {}

    math_issues, values = compute_math_issues(receipt)

    if invalid > 0:
        return "FAIL", math_issues, values
    if needs_review > 0:
        return "NEEDS_REVIEW", math_issues, values
    return "PASS", math_issues, values


def format_values_summary(values):
    """One-line summary of what financial labels the receipt has."""
    parts = []
    for label in ("GRAND_TOTAL", "SUBTOTAL", "TAX", "TIP", "LINE_TOTAL", "DISCOUNT"):
        vs = values.get(label, [])
        if vs:
            if len(vs) == 1:
                parts.append("%s=%.2f" % (label, vs[0]["value"]))
            else:
                joined = "+".join("%.2f" % v["value"] for v in vs)
                parts.append("%s=[%s]" % (label, joined))
    return ", ".join(parts) if parts else "(no financial values)"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Report on financial validation pass/fail from label evaluator"
    )
    parser.add_argument("--bucket", help="Viz-cache S3 bucket (default: from Pulumi dev stack)")
    parser.add_argument("--execution-id", help="Execution ID filter (default: latest)")
    parser.add_argument("--stack", default="dev", help="Pulumi stack (default: dev)")
    parser.add_argument("--json", action="store_true", dest="output_json", help="Output as JSON")
    parser.add_argument("--failures-only", action="store_true", help="Show only failing receipts")
    parser.add_argument("--workers", type=int, default=20, help="Parallel S3 workers (default: 20)")
    args = parser.parse_args()

    bucket = args.bucket
    if not bucket:
        bucket = get_pulumi_output("label_evaluator_viz_cache_bucket", args.stack)
        if not bucket:
            print("ERROR: Could not resolve viz-cache bucket", file=sys.stderr)
            sys.exit(1)

    s3 = boto3.client("s3", region_name="us-east-1")

    try:
        meta_resp = s3.get_object(Bucket=bucket, Key="receipts/metadata.json")
        metadata = json.loads(meta_resp["Body"].read().decode("utf-8"))
    except ClientError:
        metadata = {}

    execution_id = args.execution_id or metadata.get("execution_id", "unknown")

    print("Fetching receipts from s3://%s/receipts/ ..." % bucket, file=sys.stderr)
    keys = list_receipt_keys(s3, bucket)
    print("Found %d receipt files" % len(keys), file=sys.stderr)

    receipts = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_receipt, s3, bucket, k): k for k in keys}
        for i, future in enumerate(as_completed(futures), 1):
            r = future.result()
            if r:
                receipts.append(r)
            if i % 100 == 0:
                print("  ... fetched %d/%d" % (i, len(keys)), file=sys.stderr)

    print("Loaded %d receipts" % len(receipts), file=sys.stderr)

    # Classify each receipt with local math re-computation
    classified = defaultdict(list)
    for r in receipts:
        status, math_issues, values = classify_receipt(r)
        entry = {
            "image_id": r.get("image_id"),
            "receipt_id": r.get("receipt_id"),
            "merchant_name": r.get("merchant_name", ""),
            "status": status,
            "decisions": r.get("financial", {}).get("decisions", {}),
            "math_issues": math_issues,
            "values_summary": format_values_summary(values),
        }
        classified[status].append(entry)

    summary = {
        "execution_id": execution_id,
        "cached_at": metadata.get("cached_at"),
        "total_receipts": len(receipts),
        "pass": len(classified["PASS"]),
        "fail": len(classified["FAIL"]),
        "needs_review": len(classified["NEEDS_REVIEW"]),
        "no_financial_labels": len(classified["NO_FINANCIAL_LABELS"]),
        "no_financial_data": len(classified["NO_FINANCIAL_DATA"]),
    }

    if args.output_json:
        output = {
            "summary": summary,
            "receipts": (
                classified["FAIL"]
                if args.failures_only
                else dict(classified)
            ),
        }
        print(json.dumps(output, indent=2, default=str))
        return

    # Human-readable output
    print()
    print("=" * 70)
    print("FINANCIAL VALIDATION REPORT")
    print("=" * 70)
    print("Execution:  %s" % execution_id)
    print("Cached at:  %s" % metadata.get("cached_at", "N/A"))
    print("Total:      %d receipts" % len(receipts))
    print()
    print("  PASS:                %4d" % summary["pass"])
    print("  FAIL:                %4d" % summary["fail"])
    print("  NEEDS_REVIEW:        %4d" % summary["needs_review"])
    print("  NO_FINANCIAL_LABELS: %4d" % summary["no_financial_labels"])
    print("  NO_FINANCIAL_DATA:   %4d" % summary["no_financial_data"])
    print("=" * 70)

    # Show failures grouped by merchant
    failures = classified["FAIL"]
    if failures:
        print()
        print("FAILING RECEIPTS (%d):" % len(failures))
        print("-" * 70)

        by_merchant = defaultdict(list)
        for f in failures:
            by_merchant[f["merchant_name"] or "(unknown)"].append(f)

        for merchant in sorted(by_merchant.keys()):
            merchant_failures = by_merchant[merchant]
            print("\n  %s (%d receipts):" % (merchant, len(merchant_failures)))
            for f in merchant_failures:
                inv = f["decisions"].get("INVALID", 0)
                print(
                    "    %s  receipt#%s  (%d INVALID)"
                    % (f["image_id"], f["receipt_id"], inv)
                )
                print("      Values: %s" % f["values_summary"])
                if f["math_issues"]:
                    for issue in f["math_issues"]:
                        print("      >> %s" % issue["description"])
                else:
                    print("      >> (no equation mismatch detected locally)")

    if not args.failures_only:
        passes = classified["PASS"]
        if passes:
            print()
            print("PASSING RECEIPTS (%d):" % len(passes))
            print("-" * 70)
            by_merchant_pass = defaultdict(int)
            for p in passes:
                by_merchant_pass[p["merchant_name"] or "(unknown)"] += 1
            for merchant in sorted(by_merchant_pass.keys()):
                print("  %s: %d" % (merchant, by_merchant_pass[merchant]))


if __name__ == "__main__":
    main()
