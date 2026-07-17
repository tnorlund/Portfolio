#!/usr/bin/env python3
"""Build a read-only plan to backfill missing TRANSACTION_INFO row_ids."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
from pathlib import Path
from typing import Any

import boto3

from txinfo_recall_execute import DEV_TABLE, PROD_TABLE, canonical_hash, jsonable
from txinfo_recall_plan import query_prefix
from txinfo_recall_postcheck import parse_section_key, scan_txinfo


TXINFO = "TRANSACTION_INFO"


def read_rows(
    client: Any, table: str, key: tuple[str, int]
) -> tuple[tuple[str, int], list[dict[str, Any]]]:
    image_id, receipt_id = key
    prefix = f"RECEIPT#{receipt_id:05d}#ROW#"
    return key, query_prefix(client, table, image_id, prefix)


def derive_row_ids(
    section: dict[str, Any], rows: list[dict[str, Any]]
) -> tuple[list[int], list[dict[str, Any]]]:
    tx_lines = {int(value) for value in section.get("line_ids", [])}
    selected = []
    anomalies = []
    covered: set[int] = set()
    line_memberships: dict[int, int] = {}
    for row in rows:
        row_id = int(str(row["SK"]).rsplit("#", 1)[1])
        row_lines = {int(value) for value in row.get("line_ids", [])}
        if not tx_lines & row_lines:
            continue
        if not row_lines <= tx_lines:
            anomalies.append(
                {
                    "issue": "section only partially covers visual row",
                    "row_id": row_id,
                    "section_lines": sorted(tx_lines & row_lines),
                    "other_lines": sorted(row_lines - tx_lines),
                }
            )
            continue
        selected.append(row_id)
        covered.update(row_lines)
        for line_id in row_lines:
            line_memberships[line_id] = line_memberships.get(line_id, 0) + 1
    if covered != tx_lines:
        anomalies.append(
            {
                "issue": "visual-row union does not equal section lines",
                "missing": sorted(tx_lines - covered),
                "extra": sorted(covered - tx_lines),
            }
        )
    duplicate_lines = sorted(
        line_id for line_id, count in line_memberships.items() if count != 1
    )
    if duplicate_lines:
        anomalies.append(
            {
                "issue": "section lines do not map one-to-one to rows",
                "line_ids": duplicate_lines,
            }
        )
    return sorted(selected), anomalies


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--table", default=DEV_TABLE)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()

    if args.table == PROD_TABLE or args.table != DEV_TABLE:
        raise ValueError(f"row backfill is dev-only; refusing {args.table!r}")
    client = boto3.client("dynamodb", region_name=args.region)
    sections = scan_txinfo(client, args.table)
    missing = [section for section in sections if not section.get("row_ids")]

    rows_by_receipt = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(read_rows, client, args.table, parse_section_key(section)): section
            for section in missing
        }
        for future in concurrent.futures.as_completed(futures):
            key, rows = future.result()
            rows_by_receipt[key] = rows

    anomalies = []
    per_receipt = {}
    for section in sorted(missing, key=parse_section_key):
        image_id, receipt_id = parse_section_key(section)
        row_ids, row_anomalies = derive_row_ids(
            section, rows_by_receipt[(image_id, receipt_id)]
        )
        for anomaly in row_anomalies:
            anomalies.append(
                {**anomaly, "image_id": image_id, "receipt_id": receipt_id}
            )
        if row_anomalies:
            continue
        current = jsonable(section)
        target = json.loads(json.dumps(current))
        target["row_ids"] = row_ids
        per_receipt[f"{image_id}::{receipt_id}"] = {
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
        "table": args.table,
        "region": args.region,
        "mode": "read-only-row-id-backfill-plan",
        "txinfo_sections": len(sections),
        "missing_row_ids": len(missing),
        "planned_operations": len(per_receipt),
        "backfilled_row_ids": sum(
            len(receipt["operations"][0]["target_item"]["row_ids"])
            for receipt in per_receipt.values()
        ),
        "candidate_receipts": len(missing),
        "blocked_receipts": len(missing) - len(per_receipt),
        "anomalies": len(anomalies),
        "holdout_overlap": 0,
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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
