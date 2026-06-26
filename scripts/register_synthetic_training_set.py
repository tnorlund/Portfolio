#!/usr/bin/env python3.12
"""Register a verify_synthetic_replay bundle as a SyntheticTrainingSet record.

Computes the bundle content hash, extracts merchants/counts/mix-balance/
provenance out of the bundle, (optionally) uploads the bundle to S3, dedups by
hash, and creates the SyntheticTrainingSet record so the set becomes a
versioned, approvable, queryable artifact linked to the Jobs that consume it.

Dry-run by default — prints the record it would create without writing anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone

# Resolve receipt_dynamo from this checkout.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "receipt_dynamo"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from receipt_dynamo.entities.synthetic_training_set import (  # noqa: E402
    SyntheticTrainingSet,
)


def _examples(bundle: dict) -> list:
    return bundle.get("synthetic_training_examples", []) or []


def _meta(ex: dict) -> dict:
    m = ex.get("metadata")
    return m if isinstance(m, dict) else ex


def extract_fields(bundle: dict) -> dict:
    sel = bundle.get("selection", {}) or {}
    cm = bundle.get("candidate_mix", {}) or {}
    pol = bundle.get("synthetic_training_batch_policy", {}) or {}
    examples = _examples(bundle)
    merchants = sorted(
        {
            (ex.get("merchant_name") or _meta(ex).get("merchant_name"))
            for ex in examples
            if (ex.get("merchant_name") or _meta(ex).get("merchant_name"))
        }
    )
    source_keys = sorted(
        {
            _meta(ex).get("base_receipt_key")
            for ex in examples
            if _meta(ex).get("base_receipt_key")
        }
    )
    mix = cm.get("accepted_mix_balance", {}) or {}
    return {
        "accepted_count": int(
            pol.get("accepted_candidate_count")
            or cm.get("accepted_count")
            or len(examples)
        ),
        "candidates_seen": int(sel.get("candidates_seen", 0) or 0),
        "candidates_rejected": int(sel.get("candidates_rejected", 0) or 0),
        "operation_counts": dict(cm.get("accepted_operation_counts", {}) or {}),
        "mix_balance": dict(mix),
        "merchants": merchants,
        "source_receipt_keys": source_keys,
        "gate_versions": {
            "schema_version": str(
                pol.get("schema_version") or bundle.get("schema_version") or ""
            ),
            "min_structure_similarity": str(
                sel.get("min_structure_similarity") or ""
            ),
            "quality_gate": os.environ.get(
                "LAYOUTLM_SYNTHETIC_QUALITY_GATE", "1"
            ),
        },
        "generation_config": {
            "max_per_merchant": str(sel.get("max_per_merchant") or ""),
            "max_per_merchant_operation": str(
                sel.get("max_per_merchant_operation") or ""
            ),
        },
    }


def status_from_mix(mix: dict) -> str:
    """pending_review when the bundle carries medium/high overtraining risk."""
    risk = str(mix.get("risk_level", "")).lower()
    return "pending_review" if risk in ("medium", "high") else "draft"


def build_record(
    bundle: dict,
    *,
    content_hash: str,
    name: str,
    created_by: str,
    bundle_s3_uri: str,
) -> SyntheticTrainingSet:
    f = extract_fields(bundle)
    return SyntheticTrainingSet(
        set_id=str(uuid.uuid4()),
        name=name,
        created_at=datetime.now(timezone.utc).isoformat(),
        created_by=created_by,
        status=status_from_mix(f["mix_balance"]),
        bundle_s3_uri=bundle_s3_uri,
        content_hash=content_hash,
        merchants=f["merchants"],
        accepted_count=f["accepted_count"],
        candidates_seen=f["candidates_seen"],
        candidates_rejected=f["candidates_rejected"],
        operation_counts=f["operation_counts"],
        mix_balance=f["mix_balance"],
        source_receipt_keys=f["source_receipt_keys"],
        gate_versions=f["gate_versions"],
        generation_config=f["generation_config"],
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", required=True, help="path to the bundle JSON")
    ap.add_argument(
        "--s3-uri",
        help="s3://bucket/key to upload the bundle to (required for --live)",
    )
    ap.add_argument("--name", default=None)
    ap.add_argument("--created-by", default=os.environ.get("USER", "synthesis"))
    ap.add_argument("--table", default=os.environ.get("DYNAMO_TABLE_NAME"))
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    ap.add_argument(
        "--live",
        action="store_true",
        help="actually upload to S3 and write the record (default: dry-run)",
    )
    args = ap.parse_args()

    raw = open(args.bundle, "rb").read()
    content_hash = hashlib.sha256(raw).hexdigest()
    bundle = json.loads(raw)
    name = args.name or os.path.splitext(os.path.basename(args.bundle))[0]
    s3_uri = args.s3_uri or f"(dry-run: would upload {args.bundle})"

    record = build_record(
        bundle,
        content_hash=content_hash,
        name=name,
        created_by=args.created_by,
        bundle_s3_uri=s3_uri,
    )

    print("=== SyntheticTrainingSet to register ===")
    print(f"set_id:        {record.set_id}")
    print(f"name:          {record.name}")
    print(f"status:        {record.status}  (from mix risk_level)")
    print(f"content_hash:  {content_hash[:16]}…")
    print(f"merchants:     {record.merchants}")
    print(
        f"accepted:      {record.accepted_count} "
        f"(seen {record.candidates_seen}, rejected {record.candidates_rejected})"
    )
    print(f"operations:    {record.operation_counts}")
    print(f"mix_balance:   {json.dumps(record.mix_balance)[:200]}")
    print(f"source keys:   {len(record.source_receipt_keys)} real receipts")
    print(f"bundle_s3_uri: {record.bundle_s3_uri}")

    if not args.live:
        print("\n[dry-run] nothing written. Re-run with --live --s3-uri s3://… "
              "and --table to register.")
        return 0

    if not args.s3_uri:
        print("ERROR: --s3-uri is required with --live", file=sys.stderr)
        return 2
    if not args.table:
        print("ERROR: --table (or DYNAMO_TABLE_NAME) required", file=sys.stderr)
        return 2

    import boto3  # noqa: E402
    from receipt_dynamo.data.dynamo_client import DynamoClient  # noqa: E402

    client = DynamoClient(args.table)
    existing = client.get_synthetic_training_set_by_hash(content_hash)
    if existing is not None:
        print(f"\n[dedup] bundle already registered as {existing.set_id} "
              f"(status {existing.status}); not creating a duplicate.")
        return 0

    bucket, _, key = args.s3_uri[len("s3://"):].partition("/")
    boto3.client("s3", region_name=args.region).put_object(
        Bucket=bucket, Key=key, Body=raw
    )
    print(f"\n[s3] uploaded bundle to {args.s3_uri}")
    client.add_synthetic_training_set(record)
    print(f"[dynamo] registered SyntheticTrainingSet {record.set_id} "
          f"(status {record.status}) in {args.table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
