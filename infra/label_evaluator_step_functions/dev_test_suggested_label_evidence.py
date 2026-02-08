#!/usr/bin/env python3
"""
Dev script: Prove suggested_label fallback fixes empty evidence.

The unified_receipt_evaluator only queries ChromaDB evidence when
current_label is set. But missing_label_cluster and
missing_constellation_member issues have current_label=None by design
(unlabeled words). They DO have suggested_label set.

This script:
  1. Fetches the actual geometric issues from the last test run (S3)
  2. Shows that current_label=None for all issues
  3. Queries evidence using suggested_label instead
  4. Proves that evidence IS returned with the suggested_label fallback

Run with:
    ~/.coreml-venv/bin/python3 \
        infra/label_evaluator_step_functions/dev_test_suggested_label_evidence.py
"""

import json
import os
import subprocess
import sys
import types

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lambdas", "utils"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lambdas"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

for mod_name, mod_path in [
    ("receipt_agent", "receipt_agent/receipt_agent"),
    ("receipt_agent.utils", "receipt_agent/receipt_agent/utils"),
]:
    stub = types.ModuleType(mod_name)
    stub.__path__ = [mod_path]
    sys.modules[mod_name] = stub

# ---------------------------------------------------------------------------
# Pulumi config
# ---------------------------------------------------------------------------
INFRA_DIR = os.path.join(os.path.dirname(__file__), "..")


def _pulumi_get(key, kind="config"):
    try:
        cmd = ["pulumi", kind, "get", key, "--stack", "dev"] if kind == "config" else \
              ["pulumi", "stack", "output", key, "--stack", "dev"]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=INFRA_DIR, check=False)
        return r.stdout.strip()
    except Exception:
        return ""


CHROMA_CLOUD_API_KEY = _pulumi_get("CHROMA_CLOUD_API_KEY")
CHROMA_CLOUD_TENANT = _pulumi_get("CHROMA_CLOUD_TENANT")
CHROMA_CLOUD_DATABASE = _pulumi_get("CHROMA_CLOUD_DATABASE")

if not CHROMA_CLOUD_API_KEY:
    print("ERROR: CHROMA_CLOUD_API_KEY not found in Pulumi config")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from receipt_chroma import ChromaClient
from receipt_agent.utils.chroma_helpers import (
    compute_label_consensus,
    format_label_evidence_for_prompt,
    query_label_evidence,
)

# ---------------------------------------------------------------------------
# Load geometric issues from the last test run S3 output
# ---------------------------------------------------------------------------
EXECUTION_ID = "c74f44ea-326b-45cc-9594-b045fb1e46ea"
BUCKET = "label-evaluator-dev-batch-bucket-a82b944"

print("=" * 70)
print("DEV TEST: Prove suggested_label fallback fixes empty evidence")
print("=" * 70)
print()

# Fetch all receipt outputs from S3
import boto3
s3 = boto3.client("s3", region_name="us-east-1")

prefix = f"unified/{EXECUTION_ID}/"
response = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
files = [obj["Key"] for obj in response.get("Contents", [])]
print(f"Found {len(files)} receipt outputs in S3")
print()

# Collect all geometric issues across receipts
all_issues = []
for key in files:
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    data = json.loads(obj["Body"].read())
    merchant = data.get("merchant_name", "Unknown")
    image_id = data.get("image_id")
    receipt_id = data.get("receipt_id")
    for issue in data.get("geometric_issues", []):
        issue["_image_id"] = image_id
        issue["_receipt_id"] = receipt_id
        issue["_merchant"] = merchant
        all_issues.append(issue)

print(f"Total geometric issues across all receipts: {len(all_issues)}")
print()

if not all_issues:
    print("No geometric issues found — nothing to test.")
    sys.exit(0)

# ---------------------------------------------------------------------------
# Phase 1: Show the problem — current_label is None for all issues
# ---------------------------------------------------------------------------
print("-" * 70)
print("PHASE 1: Current behavior (current_label is always None)")
print("-" * 70)
print()

for i, issue in enumerate(all_issues):
    current = issue.get("current_label")
    suggested = issue.get("suggested_label")
    word_text = issue.get("word_text", "?")
    issue_type = issue.get("type", "?")
    print(f"  [{i}] type={issue_type}")
    print(f"      word='{word_text}' current_label={current} suggested_label={suggested}")

    # Simulate the existing guard
    if current and current != "O":
        print(f"      -> WOULD query evidence for '{current}'")
    else:
        print(f"      -> SKIPPED (current_label is falsy)")
    print()

# ---------------------------------------------------------------------------
# Phase 2: Query evidence using suggested_label (the fix)
# ---------------------------------------------------------------------------
print("-" * 70)
print("PHASE 2: Query evidence using suggested_label fallback")
print("-" * 70)
print()

chroma_client = ChromaClient(
    cloud_api_key=CHROMA_CLOUD_API_KEY,
    cloud_tenant=CHROMA_CLOUD_TENANT or None,
    cloud_database=CHROMA_CLOUD_DATABASE or None,
    mode="read",
    metadata_only=True,
)

results_summary = []

for i, issue in enumerate(all_issues):
    current = issue.get("current_label")
    suggested = issue.get("suggested_label")
    word_text = issue.get("word_text", "?")
    image_id = issue["_image_id"]
    receipt_id = issue["_receipt_id"]
    merchant = issue["_merchant"]
    line_id = issue.get("line_id", 0)
    word_id = issue.get("word_id", 0)

    # The fix: use suggested_label when current_label is None
    target_label = current if (current and current != "O") else suggested

    if not target_label:
        print(f"  [{i}] word='{word_text}' — no label to query (both None)")
        results_summary.append({"word": word_text, "target": None, "evidence": 0})
        continue

    print(f"  [{i}] word='{word_text}' target_label={target_label} (from {'current' if current else 'suggested'})")

    try:
        evidence = query_label_evidence(
            chroma_client=chroma_client,
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            target_label=target_label,
            target_merchant=merchant,
            n_results_per_query=15,
            min_similarity=0.70,
            include_collections=("words", "lines"),
        )

        consensus, pos, neg = compute_label_consensus(evidence)
        evidence_text = format_label_evidence_for_prompt(
            evidence, target_label=target_label, max_positive=5, max_negative=3,
        )

        print(f"      evidence={len(evidence)} items (positive={pos}, negative={neg})")
        print(f"      consensus={consensus:.3f}")
        if evidence:
            for e in evidence[:3]:
                print(
                    f"        sim={e.similarity_score:.3f} valid={e.label_valid} "
                    f"text='{e.word_text}' merchant='{e.merchant_name}' "
                    f"source={e.evidence_source}"
                )
        print()

        results_summary.append({
            "word": word_text,
            "target": target_label,
            "evidence": len(evidence),
            "consensus": consensus,
            "positive": pos,
            "negative": neg,
        })
    except Exception as exc:
        print(f"      ERROR: {exc}")
        results_summary.append({"word": word_text, "target": target_label, "evidence": -1, "error": str(exc)})
        print()

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("=" * 70)
print("SUMMARY")
print("=" * 70)
print()

total_issues = len(results_summary)
issues_with_evidence = sum(1 for r in results_summary if r.get("evidence", 0) > 0)
total_evidence = sum(r.get("evidence", 0) for r in results_summary if r.get("evidence", 0) > 0)

print(f"  Total geometric issues:    {total_issues}")
print(f"  Issues with evidence:      {issues_with_evidence} / {total_issues}")
print(f"  Total evidence items:      {total_evidence}")
print()

print("  Before fix (current_label guard):")
print(f"    Issues with evidence:    0 / {total_issues}  (all skipped)")
print(f"    Total evidence items:    0")
print()
print("  After fix (suggested_label fallback):")
print(f"    Issues with evidence:    {issues_with_evidence} / {total_issues}")
print(f"    Total evidence items:    {total_evidence}")
print()

if issues_with_evidence > 0:
    print("  CONCLUSION: suggested_label fallback provides evidence.")
    print("  The fix should use suggested_label when current_label is None.")
else:
    print("  CONCLUSION: No evidence found even with suggested_label.")
    print("  The issue may be deeper (embeddings missing for these words).")
