#!/usr/bin/env python3
"""Independently verify the post-write TRANSACTION_INFO corpus in dev."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import boto3

from txinfo_recall_execute import (
    DEV_TABLE,
    PROD_TABLE,
    batch_get_items,
    canonical_hash,
    deserialize_item,
)
from txinfo_recall_plan import batch_get_lines, query_prefix
from txinfo_recall_row_verify import (
    NON_METADATA_TRANSACTION_RE,
    SPATIAL_TXINFO_OWNER_RE,
    STRONG_PAYMENT_PAYLOAD_RE,
)


TXINFO = "TRANSACTION_INFO"
FOOTER_CONTENT_RE = re.compile(
    r"\b(?:thank\s+you|questions?/comments?|www\.|https?://|coupon|survey|"
    r"returns?|refunds?|exchanges?|not\s+valid|admission|policy|careers?|"
    r"visit\s+us|visit\s+www|come\s+again)\b",
    re.IGNORECASE,
)


def scan_txinfo(client: Any, table: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    start_key = None
    while True:
        request: dict[str, Any] = {
            "TableName": table,
            "FilterExpression": "#type = :receipt_section AND #section = :txinfo",
            "ExpressionAttributeNames": {
                "#type": "TYPE",
                "#section": "section_type",
            },
            "ExpressionAttributeValues": {
                ":receipt_section": {"S": "RECEIPT_SECTION"},
                ":txinfo": {"S": TXINFO},
            },
            "ConsistentRead": True,
        }
        if start_key:
            request["ExclusiveStartKey"] = start_key
        response = client.scan(**request)
        items.extend(deserialize_item(item) for item in response.get("Items", []))
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            return items


def parse_section_key(section: dict[str, Any]) -> tuple[str, int]:
    image_id = str(section["PK"]).removeprefix("IMAGE#")
    receipt_part = str(section["SK"]).split("#SECTION#", 1)[0]
    return image_id, int(receipt_part.removeprefix("RECEIPT#"))


def read_receipt_state(
    client: Any, table: str, key: tuple[str, int]
) -> tuple[tuple[str, int], dict[str, Any]]:
    image_id, receipt_id = key
    base = f"RECEIPT#{receipt_id:05d}#"
    return key, {
        "sections": query_prefix(client, table, image_id, base + "SECTION#"),
        "rows": query_prefix(client, table, image_id, base + "ROW#"),
    }


def semantic_flags(row_text: str, line_count: int) -> list[str]:
    flags = []
    has_owner = SPATIAL_TXINFO_OWNER_RE.search(row_text) is not None
    has_payment = STRONG_PAYMENT_PAYLOAD_RE.search(row_text) is not None
    if NON_METADATA_TRANSACTION_RE.search(row_text):
        flags.append("non-metadata-transaction-phrase")
    if has_payment and not has_owner:
        flags.append("payment-without-txinfo-owner")
    if has_payment and has_owner and line_count >= 5:
        flags.append("large-mixed-payment-row")
    if FOOTER_CONTENT_RE.search(row_text):
        flags.append("footer-content")
    if not has_owner:
        flags.append("no-explicit-spatial-owner")
    return flags


def deterministic_sample(
    rows: list[dict[str, Any]], per_source: int
) -> list[dict[str, Any]]:
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_source[row["model_source"]].append(row)
    sample = []
    for source, source_rows in sorted(by_source.items()):
        ordered = sorted(
            source_rows,
            key=lambda row: hashlib.sha256(row["row_key"].encode()).hexdigest(),
        )
        sample.extend(ordered[:per_source])
    return sorted(sample, key=lambda row: row["row_key"])


def write_report(summary: dict[str, Any], path: Path) -> None:
    text = f"""# TRANSACTION_INFO post-write check

| Check | Result |
|---|---:|
| Sections | {summary['txinfo_sections']} |
| Lines | {summary['txinfo_lines']} |
| Visual rows | {summary['txinfo_rows']} |
| Structural anomalies | {summary['structural_anomalies']} |
| Exact plan-target mismatches | {summary['plan_target_mismatches']} |
| PENDING sections | {summary['status_counts'].get('PENDING', 0)} |
| Semantically flagged rows | {summary['flagged_rows']} |
| Deterministic audit sample | {summary['audit_sample_rows']} |

Semantic flags are review prompts, not automatic failures. Structural anomalies or
plan-target mismatches are hard failures and block promotion.
"""
    path.write_text(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--table", default=DEV_TABLE)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--sample-per-source", type=int, default=120)
    args = parser.parse_args()

    if args.table == PROD_TABLE or args.table != DEV_TABLE:
        raise ValueError(f"postcheck is dev-only; refusing table {args.table!r}")

    plan = json.loads(args.plan.read_text())
    client = boto3.client("dynamodb", region_name=args.region)
    tx_sections = scan_txinfo(client, args.table)
    tx_by_receipt = {parse_section_key(section): section for section in tx_sections}
    if len(tx_by_receipt) != len(tx_sections):
        raise RuntimeError("duplicate TRANSACTION_INFO sections for a receipt")

    states = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(read_receipt_state, client, args.table, key): key
            for key in sorted(tx_by_receipt)
        }
        for index, future in enumerate(
            concurrent.futures.as_completed(futures), start=1
        ):
            key, state = future.result()
            states[key] = state
            if index % 100 == 0 or index == len(futures):
                print(f"read {index}/{len(futures)} receipt states", flush=True)

    all_line_keys = []
    for (image_id, receipt_id), state in states.items():
        tx_lines = {
            int(value) for value in tx_by_receipt[(image_id, receipt_id)]["line_ids"]
        }
        needed = set(tx_lines)
        for row in state["rows"]:
            row_lines = {int(value) for value in row.get("line_ids", [])}
            if tx_lines & row_lines:
                needed.update(row_lines)
        all_line_keys.extend((image_id, receipt_id, line_id) for line_id in needed)
    lines = batch_get_lines(client, args.table, all_line_keys)

    anomalies = []
    row_records = []
    for key, tx_section in sorted(tx_by_receipt.items()):
        image_id, receipt_id = key
        state = states[key]
        sections = {
            str(item["section_type"]): item for item in state["sections"]
        }
        line_owners: dict[int, list[str]] = defaultdict(list)
        for section_type, section in sections.items():
            for line_id in section.get("line_ids", []):
                line_owners[int(line_id)].append(section_type)

        rows = {}
        line_to_rows: dict[int, list[int]] = defaultdict(list)
        for row in state["rows"]:
            row_id = int(str(row["SK"]).rsplit("#", 1)[1])
            row_lines = {int(value) for value in row.get("line_ids", [])}
            rows[row_id] = row_lines
            for line_id in row_lines:
                line_to_rows[line_id].append(row_id)

        tx_lines = {int(value) for value in tx_section.get("line_ids", [])}
        for line_id in sorted(tx_lines):
            owners = sorted(line_owners.get(line_id, []))
            if owners != [TXINFO]:
                anomalies.append(
                    {"receipt": key, "line_id": line_id, "issue": "ownership", "owners": owners}
                )
            if (image_id, receipt_id, line_id) not in lines:
                anomalies.append(
                    {"receipt": key, "line_id": line_id, "issue": "missing line item"}
                )
            if len(line_to_rows.get(line_id, [])) != 1:
                anomalies.append(
                    {
                        "receipt": key,
                        "line_id": line_id,
                        "issue": "line does not resolve to one row",
                        "rows": line_to_rows.get(line_id, []),
                    }
                )

        tx_row_ids = {
            row_id for line_id in tx_lines for row_id in line_to_rows.get(line_id, [])
        }
        for row_id in sorted(tx_row_ids):
            row_lines = rows[row_id]
            if not row_lines <= tx_lines:
                anomalies.append(
                    {
                        "receipt": key,
                        "row_id": row_id,
                        "issue": "TRANSACTION_INFO only partially covers visual row",
                        "tx_lines": sorted(tx_lines & row_lines),
                        "other_lines": sorted(row_lines - tx_lines),
                    }
                )
            rendered = [
                {
                    "line_id": line_id,
                    "text": str(lines.get((image_id, receipt_id, line_id), {}).get("text", "")),
                }
                for line_id in sorted(row_lines)
            ]
            row_text = " | ".join(line["text"] for line in rendered)
            row_records.append(
                {
                    "row_key": f"{image_id}:{receipt_id}:{row_id}",
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "row_id": row_id,
                    "model_source": tx_section.get("model_source", ""),
                    "validation_status": tx_section.get("validation_status", ""),
                    "row_lines": rendered,
                    "row_text": row_text,
                    "flags": semantic_flags(row_text, len(row_lines)),
                }
            )

        declared_rows = tx_section.get("row_ids")
        if declared_rows is not None and {int(value) for value in declared_rows} != tx_row_ids:
            anomalies.append(
                {
                    "receipt": key,
                    "issue": "declared row_ids do not match line-derived rows",
                    "declared": sorted(int(value) for value in declared_rows),
                    "derived": sorted(tx_row_ids),
                }
            )

    target_mismatches = []
    target_keys = []
    operations = []
    for receipt in plan["per_receipt"].values():
        for operation in receipt["operations"]:
            operations.append(operation)
            item = operation.get("target_item") or operation["current_item"]
            target_keys.append((str(item["PK"]), str(item["SK"])))
    live_targets = batch_get_items(client, args.table, target_keys)
    for operation in operations:
        target = operation.get("target_item")
        current = operation.get("current_item")
        key = (str((target or current)["PK"]), str((target or current)["SK"]))
        actual = live_targets.get(key)
        if target is None and actual is not None:
            target_mismatches.append({"key": key, "issue": "deleted item exists"})
        elif target is not None and actual is None:
            target_mismatches.append({"key": key, "issue": "target missing"})
        elif target is not None and canonical_hash(actual) != canonical_hash(target):
            target_mismatches.append({"key": key, "issue": "target fingerprint mismatch"})

    flagged = [row for row in row_records if row["flags"]]
    sample = deterministic_sample(row_records, args.sample_per_source)
    summary = {
        "mode": "read-only-post-write-check",
        "table": args.table,
        "plan_sha256": plan["summary"]["plan_sha256"],
        "txinfo_sections": len(tx_sections),
        "txinfo_lines": sum(len(section.get("line_ids", [])) for section in tx_sections),
        "txinfo_rows": len(row_records),
        "status_counts": dict(sorted(Counter(str(item.get("validation_status")) for item in tx_sections).items())),
        "model_source_counts": dict(sorted(Counter(str(item.get("model_source")) for item in tx_sections).items())),
        "structural_anomalies": len(anomalies),
        "plan_target_mismatches": len(target_mismatches),
        "flagged_rows": len(flagged),
        "flag_counts": dict(sorted(Counter(flag for row in flagged for flag in row["flags"]).items())),
        "audit_sample_rows": len(sample),
        "promotion_ready_structurally": not anomalies and not target_mismatches,
    }

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "POSTCHECK_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "STRUCTURAL_ANOMALIES.json").write_text(
        json.dumps(anomalies, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "PLAN_TARGET_MISMATCHES.json").write_text(
        json.dumps(target_mismatches, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "TXINFO_ROWS.json").write_text(
        json.dumps(row_records, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "SEMANTIC_REVIEW_QUEUE.json").write_text(
        json.dumps(flagged, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "AUDIT_SAMPLE.json").write_text(
        json.dumps(sample, indent=2, sort_keys=True) + "\n"
    )
    write_report(summary, args.output / "POSTCHECK.md")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
