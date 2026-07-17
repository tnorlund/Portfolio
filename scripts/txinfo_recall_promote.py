#!/usr/bin/env python3
"""Build a read-only PENDING-to-VALID TRANSACTION_INFO promotion plan."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import Counter
from pathlib import Path
from typing import Any

import boto3

from txinfo_recall_execute import DEV_TABLE, PROD_TABLE, canonical_hash, jsonable
from txinfo_recall_postcheck import parse_section_key, scan_txinfo


TXINFO = "TRANSACTION_INFO"


def validate_evidence(summary: dict[str, Any], adjudication: dict[str, Any]) -> None:
    if summary.get("structural_anomalies"):
        raise ValueError("postcheck has structural anomalies")
    if summary.get("plan_target_mismatches"):
        raise ValueError("postcheck has plan-target mismatches")
    if not summary.get("promotion_ready_structurally"):
        raise ValueError("postcheck is not structurally promotion-ready")
    assessment = adjudication.get("promotion_assessment", {})
    if not assessment.get("ready"):
        raise ValueError("semantic adjudication is not promotion-ready")
    if assessment.get("structural_anomalies") or assessment.get(
        "plan_target_mismatches"
    ):
        raise ValueError("semantic adjudication records hard failures")


def build_plan(
    sections: list[dict[str, Any]],
    *,
    table: str,
    region: str,
    evidence_sha256: str,
) -> dict[str, Any]:
    anomalies = []
    per_receipt = {}
    for section in sorted(sections, key=parse_section_key):
        current = jsonable(section)
        image_id, receipt_id = parse_section_key(section)
        key = f"{image_id}::{receipt_id}"
        if key in per_receipt:
            anomalies.append(
                {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "issue": "duplicate TRANSACTION_INFO section",
                }
            )
            continue
        if section.get("validation_status") != "PENDING":
            anomalies.append(
                {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "issue": "section is not PENDING",
                    "status": section.get("validation_status"),
                }
            )
            continue
        target = json.loads(json.dumps(current))
        target["validation_status"] = "VALID"
        per_receipt[key] = {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant": "",
            "operations": [
                {
                    "op": "modify",
                    "section_type": TXINFO,
                    "current_fingerprint": canonical_hash(current),
                    "current_item": current,
                    "target_item": target,
                }
            ],
        }

    summary = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "table": table,
        "region": region,
        "mode": "read-only-promotion-plan",
        "target_status": "VALID",
        "candidate_receipts": len(sections),
        "planned_operations": len(per_receipt),
        "blocked_receipts": len(anomalies),
        "anomalies": len(anomalies),
        "holdout_overlap": 0,
        "evidence_sha256": evidence_sha256,
        "model_source_counts": dict(
            sorted(Counter(str(item.get("model_source")) for item in sections).items())
        ),
        "plan_sha256": None,
    }
    payload = {
        "summary": summary,
        "anomalies": anomalies,
        "per_receipt": per_receipt,
    }
    summary["plan_sha256"] = canonical_hash(
        {"anomalies": anomalies, "per_receipt": per_receipt}
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--postcheck-summary", type=Path, required=True)
    parser.add_argument("--adjudication", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--table", default=DEV_TABLE)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    if args.table == PROD_TABLE or args.table != DEV_TABLE:
        raise ValueError(f"promotion planner is dev-only; refusing {args.table!r}")
    summary = json.loads(args.postcheck_summary.read_text())
    adjudication = json.loads(args.adjudication.read_text())
    validate_evidence(summary, adjudication)
    evidence_sha = canonical_hash(
        {"postcheck": summary, "adjudication": adjudication}
    )

    client = boto3.client("dynamodb", region_name=args.region)
    sections = scan_txinfo(client, args.table)
    plan = build_plan(
        sections,
        table=args.table,
        region=args.region,
        evidence_sha256=evidence_sha,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    print(json.dumps(plan["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
