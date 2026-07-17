#!/usr/bin/env python3
"""Build a read-only, row-aware DynamoDB correction plan for recall hits."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import boto3
from boto3.dynamodb.types import TypeDeserializer


DEV_TABLE = "ReceiptsTable-dc5be22"
PROD_TABLE = "ReceiptsTable-d7ff76a"
TXINFO = "TRANSACTION_INFO"
DEFAULT_MODEL_SOURCE = "txinfo-recall-verified-2026-07-17"
_DESERIALIZER = TypeDeserializer()


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    return value


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        jsonable(value), separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def deserialize_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: _DESERIALIZER.deserialize(value) for key, value in item.items()}


def query_prefix(
    client: Any, table: str, image_id: str, prefix: str
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    start_key = None
    while True:
        request: dict[str, Any] = {
            "TableName": table,
            "KeyConditionExpression": "#pk = :pk AND begins_with(#sk, :prefix)",
            "ExpressionAttributeNames": {"#pk": "PK", "#sk": "SK"},
            "ExpressionAttributeValues": {
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":prefix": {"S": prefix},
            },
            "ConsistentRead": True,
        }
        if start_key:
            request["ExclusiveStartKey"] = start_key
        response = client.query(**request)
        items.extend(deserialize_item(item) for item in response.get("Items", []))
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            return items


def batch_get_lines(
    client: Any,
    table: str,
    keys: Iterable[tuple[str, int, int]],
) -> dict[tuple[str, int, int], dict[str, Any]]:
    raw_keys = [
        {
            "PK": {"S": f"IMAGE#{image_id}"},
            "SK": {"S": f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"},
        }
        for image_id, receipt_id, line_id in sorted(set(keys))
    ]
    items: list[dict[str, Any]] = []
    for offset in range(0, len(raw_keys), 100):
        pending = raw_keys[offset : offset + 100]
        for _attempt in range(10):
            response = client.batch_get_item(
                RequestItems={
                    table: {"Keys": pending, "ConsistentRead": True}
                }
            )
            items.extend(
                deserialize_item(item)
                for item in response.get("Responses", {}).get(table, [])
            )
            pending = response.get("UnprocessedKeys", {}).get(table, {}).get(
                "Keys", []
            )
            if not pending:
                break
        if pending:
            raise RuntimeError(f"batch-get left {len(pending)} unprocessed line keys")

    result = {}
    for item in items:
        image_id = str(item["PK"]).removeprefix("IMAGE#")
        receipt_part, line_part = str(item["SK"]).split("#LINE#", 1)
        receipt_id = int(receipt_part.removeprefix("RECEIPT#"))
        result[(image_id, receipt_id, int(line_part))] = item
    return result


def holdout_keys(path: Path) -> set[tuple[str, int]]:
    payload = json.loads(path.read_text())
    keys = {
        (str(item["image_id"]), int(item["receipt_id"]))
        for item in payload.get("mapping_opaque_to_source", {}).values()
    }
    keys.update(
        (str(item["image_id"]), int(item["receipt_id"]))
        for item in payload.get("excluded_pinned_goldens", [])
    )
    return keys


def section_target(
    current: dict[str, Any],
    *,
    line_ids: set[int],
    row_ids: set[int] | None,
) -> dict[str, Any]:
    target = jsonable(current)
    target["line_ids"] = sorted(line_ids)
    if row_ids is None:
        target.pop("row_ids", None)
    else:
        target["row_ids"] = sorted(row_ids)
    return target


def read_receipt_state(
    client: Any, table: str, key: tuple[str, int]
) -> tuple[tuple[str, int], dict[str, Any]]:
    image_id, receipt_id = key
    base = f"RECEIPT#{receipt_id:05d}#"
    sections = query_prefix(client, table, image_id, base + "SECTION#")
    rows = query_prefix(client, table, image_id, base + "ROW#")
    return key, {"sections": sections, "rows": rows}


def plan_receipt(
    key: tuple[str, int],
    hits: list[dict[str, Any]],
    state: dict[str, Any],
    lines: dict[tuple[str, int, int], dict[str, Any]],
    *,
    generated_at: str,
    model_source: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    image_id, receipt_id = key
    anomalies: list[dict[str, Any]] = []
    sections = {
        str(item["section_type"]): jsonable(item) for item in state["sections"]
    }
    if len(sections) != len(state["sections"]):
        anomalies.append({"issue": "duplicate section type records"})

    rows = {}
    line_to_rows: dict[int, list[int]] = defaultdict(list)
    for item in state["rows"]:
        row_id = int(str(item["SK"]).rsplit("#", 1)[1])
        row_lines = {int(value) for value in item.get("line_ids", [])}
        rows[row_id] = row_lines
        for line_id in row_lines:
            line_to_rows[line_id].append(row_id)

    line_to_sections: dict[int, list[str]] = defaultdict(list)
    for section_type, item in sections.items():
        for line_id in item.get("line_ids", []):
            line_to_sections[int(line_id)].append(section_type)

    hit_by_line = {int(hit["line_id"]): hit for hit in hits}
    if len(hit_by_line) != len(hits):
        anomalies.append({"issue": "duplicate final hits for the same line"})

    move_rows_by_source: dict[str, set[int]] = defaultdict(set)
    move_lines_by_source: dict[str, set[int]] = defaultdict(set)
    row_expansions = []
    for line_id, hit in sorted(hit_by_line.items()):
        line = lines.get((image_id, receipt_id, line_id))
        if line is None:
            anomalies.append({"line_id": line_id, "issue": "line item missing"})
        elif line.get("TYPE") != "RECEIPT_LINE":
            anomalies.append(
                {"line_id": line_id, "issue": "key is not a RECEIPT_LINE"}
            )
        elif str(line.get("text", "")) != str(hit["line_text"]):
            anomalies.append(
                {
                    "line_id": line_id,
                    "issue": "line text drift",
                    "frozen": hit["line_text"],
                    "live": line.get("text", ""),
                }
            )

        owners = sorted(line_to_sections.get(line_id, []))
        expected_source = str(hit["current_label"])
        if owners != [expected_source]:
            anomalies.append(
                {
                    "line_id": line_id,
                    "issue": "source section drift",
                    "frozen": expected_source,
                    "live": owners,
                }
            )

        containing_rows = sorted(line_to_rows.get(line_id, []))
        if len(containing_rows) != 1:
            anomalies.append(
                {
                    "line_id": line_id,
                    "issue": "line must resolve to exactly one visual row",
                    "rows": containing_rows,
                }
            )
            continue
        row_id = containing_rows[0]
        row_lines = rows[row_id]
        incompatible = {}
        for row_line in sorted(row_lines):
            row_owners = sorted(line_to_sections.get(row_line, []))
            if not row_owners or set(row_owners) - {expected_source, TXINFO}:
                incompatible[row_line] = row_owners
            if len(row_owners) > 1:
                incompatible[row_line] = row_owners
        if incompatible:
            anomalies.append(
                {
                    "line_id": line_id,
                    "row_id": row_id,
                    "issue": "visual row crosses incompatible section ownership",
                    "row_line_owners": incompatible,
                }
            )
            continue
        source = sections.get(expected_source)
        if source is None:
            continue
        if "row_ids" in source and row_id not in {
            int(value) for value in source["row_ids"]
        }:
            anomalies.append(
                {
                    "line_id": line_id,
                    "row_id": row_id,
                    "issue": "source row_ids omit the target visual row",
                    "source": expected_source,
                }
            )
            continue
        source_row_lines = {
            row_line
            for row_line in row_lines
            if expected_source in line_to_sections.get(row_line, [])
        }
        move_rows_by_source[expected_source].add(row_id)
        move_lines_by_source[expected_source].update(source_row_lines)
        row_expansions.append(
            {
                "hit_line_id": line_id,
                "row_id": row_id,
                "row_line_ids": sorted(row_lines),
                "row_lines": [
                    {
                        "line_id": row_line,
                        "text": str(
                            lines.get(
                                (image_id, receipt_id, row_line), {}
                            ).get("text", "")
                        ),
                        "is_final_candidate": row_line in hit_by_line,
                    }
                    for row_line in sorted(row_lines)
                ],
                "added_sibling_line_ids": sorted(row_lines - {line_id}),
                "source": expected_source,
            }
        )

    current_tx = sections.get(TXINFO)
    if current_tx and current_tx.get("validation_status") != "PENDING":
        anomalies.append(
            {
                "issue": "existing TRANSACTION_INFO is not PENDING",
                "status": current_tx.get("validation_status"),
            }
        )

    operations = []
    if not anomalies:
        for source_type in sorted(move_lines_by_source):
            current = sections[source_type]
            current_lines = {int(value) for value in current.get("line_ids", [])}
            target_lines = current_lines - move_lines_by_source[source_type]
            target_rows: set[int] | None = None
            if "row_ids" in current:
                current_rows = {int(value) for value in current["row_ids"]}
                target_rows = current_rows - move_rows_by_source[source_type]
                expected_lines = set().union(
                    *(rows[row_id] for row_id in target_rows)
                ) if target_rows else set()
                if expected_lines != target_lines:
                    anomalies.append(
                        {
                            "issue": "source row coverage would break",
                            "source": source_type,
                            "target_line_ids": sorted(target_lines),
                            "row_union": sorted(expected_lines),
                        }
                    )
                    break
            operations.append(
                {
                    "op": "delete" if not target_lines else "modify",
                    "section_type": source_type,
                    "current_fingerprint": canonical_hash(current),
                    "current_item": current,
                    "target_item": (
                        section_target(
                            current, line_ids=target_lines, row_ids=target_rows
                        )
                        if target_lines
                        else None
                    ),
                    "removed_line_ids": sorted(
                        current_lines - target_lines
                    ),
                    "removed_row_ids": sorted(move_rows_by_source[source_type]),
                }
            )

    if not anomalies:
        current_tx_lines = (
            {int(value) for value in current_tx.get("line_ids", [])}
            if current_tx
            else set()
        )
        added_lines = set().union(*move_lines_by_source.values())
        target_tx_lines = current_tx_lines | added_lines

        target_tx_rows: set[int] | None = set()
        for line_id in target_tx_lines:
            containing_rows = line_to_rows.get(line_id, [])
            if len(containing_rows) != 1:
                target_tx_rows = None
                break
            target_tx_rows.add(containing_rows[0])
        if target_tx_rows is not None:
            row_union = set().union(
                *(rows[row_id] for row_id in target_tx_rows)
            ) if target_tx_rows else set()
            if row_union != target_tx_lines:
                target_tx_rows = None

        if current_tx:
            target_tx = section_target(
                current_tx, line_ids=target_tx_lines, row_ids=target_tx_rows
            )
            target_tx["validation_status"] = "PENDING"
            target_tx["model_source"] = model_source
            tx_op = "modify"
            fingerprint = canonical_hash(current_tx)
        else:
            target_tx = {
                "PK": f"IMAGE#{image_id}",
                "SK": f"RECEIPT#{receipt_id:05d}#SECTION#{TXINFO}",
                "TYPE": "RECEIPT_SECTION",
                "section_type": TXINFO,
                "line_ids": sorted(target_tx_lines),
                "created_at": generated_at,
                "model_source": model_source,
                "validation_status": "PENDING",
            }
            if target_tx_rows is not None:
                target_tx["row_ids"] = sorted(target_tx_rows)
            tx_op = "create"
            fingerprint = None
        operations.append(
            {
                "op": tx_op,
                "section_type": TXINFO,
                "current_fingerprint": fingerprint,
                "current_item": current_tx,
                "target_item": target_tx,
                "added_line_ids": sorted(target_tx_lines - current_tx_lines),
                "added_row_ids": sorted(
                    set(target_tx.get("row_ids", []))
                    - set((current_tx or {}).get("row_ids", []))
                ),
                "row_coverage_complete": target_tx_rows is not None,
            }
        )

    receipt_plan = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant": hits[0].get("merchant", ""),
        "candidate_line_ids": sorted(hit_by_line),
        "row_expansions": row_expansions,
        "expanded_move_line_ids": sorted(
            set().union(*move_lines_by_source.values())
            if move_lines_by_source
            else set()
        ),
        "line_snapshots": {
            str(line_id): {
                "text": str(
                    lines.get((image_id, receipt_id, line_id), {}).get(
                        "text", ""
                    )
                ),
                "item_fingerprint": canonical_hash(
                    lines.get((image_id, receipt_id, line_id), {})
                ),
            }
            for line_id in sorted(
                set().union(*move_lines_by_source.values())
                if move_lines_by_source
                else set()
            )
        },
        "operations": operations if not anomalies else [],
    }
    for anomaly in anomalies:
        anomaly.update({"image_id": image_id, "receipt_id": receipt_id})
    return receipt_plan, anomalies


def write_report(summary: dict[str, Any], path: Path) -> None:
    blocked = summary["blocked_receipts"]
    text = f"""# TRANSACTION_INFO recall correction plan

**Mode:** read-only planning; no DynamoDB writes performed.

| Check | Result |
|---|---:|
| Final candidate lines | {summary['final_candidate_lines']} |
| Candidate receipts | {summary['candidate_receipts']} |
| Visual rows to move | {summary['visual_rows_to_move']} |
| Expanded line IDs to move | {summary['expanded_line_ids_to_move']} |
| Row-expansion sibling lines | {summary['row_expansion_extra_lines']} |
| Existing TRANSACTION_INFO sections | {summary['existing_txinfo_sections']} |
| New TRANSACTION_INFO sections | {summary['new_txinfo_sections']} |
| Planned section operations | {summary['planned_operations']} |
| Blocked receipts | {blocked} |
| Spent-holdout overlap | {summary['holdout_overlap']} |

Every target OCR line was re-read with a consistent DynamoDB read and compared with
the frozen text and source label. Section mutations are expanded to whole visual
rows where the source section is row-granular. Each touched section carries a
canonical SHA-256 fingerprint for execution-time drift checks.

"""
    if blocked:
        text += "The plan is **not executable** until all anomalies are resolved.\n"
    else:
        text += (
            "The plan passed its read-only gates. Review the exact operations before "
            "authorizing any write.\n"
        )
    path.write_text(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--final-positives", type=Path, required=True)
    parser.add_argument("--holdout-mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--table", default=DEV_TABLE)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--model-source", default=DEFAULT_MODEL_SOURCE)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    if args.table == PROD_TABLE or args.table != DEV_TABLE:
        raise ValueError(f"planner is dev-only; refusing table {args.table!r}")

    hits = json.loads(args.final_positives.read_text())
    ids = [str(hit["id"]) for hit in hits]
    if len(ids) != len(set(ids)):
        raise ValueError("final positives contain duplicate canonical IDs")
    by_receipt: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for hit in hits:
        by_receipt[(str(hit["image_id"]), int(hit["receipt_id"]))].append(hit)

    holdout = holdout_keys(args.holdout_mapping)
    overlaps = sorted(set(by_receipt) & holdout)
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    client = boto3.client("dynamodb", region_name=args.region)

    states = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(read_receipt_state, client, args.table, key): key
            for key in sorted(by_receipt)
        }
        for index, future in enumerate(
            concurrent.futures.as_completed(futures), start=1
        ):
            key, state = future.result()
            states[key] = state
            if index % 100 == 0 or index == len(futures):
                print(f"read {index}/{len(futures)} receipt states", flush=True)

    all_line_keys = []
    for key, state in states.items():
        image_id, receipt_id = key
        needed = {int(hit["line_id"]) for hit in by_receipt[key]}
        for row in state["rows"]:
            row_lines = {int(value) for value in row.get("line_ids", [])}
            if needed & row_lines:
                needed.update(row_lines)
        all_line_keys.extend((image_id, receipt_id, line_id) for line_id in needed)
    live_lines = batch_get_lines(client, args.table, all_line_keys)

    per_receipt = {}
    anomalies = [
        {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "issue": "spent holdout overlap",
        }
        for image_id, receipt_id in overlaps
    ]
    for key in sorted(by_receipt):
        receipt_plan, receipt_anomalies = plan_receipt(
            key,
            by_receipt[key],
            states[key],
            live_lines,
            generated_at=generated_at,
            model_source=args.model_source,
        )
        if key in holdout:
            receipt_plan["operations"] = []
        per_receipt[f"{key[0]}::{key[1]}"] = receipt_plan
        anomalies.extend(receipt_anomalies)

    executable = [plan for plan in per_receipt.values() if plan["operations"]]
    operations = [op for plan in executable for op in plan["operations"]]
    expanded = {
        (plan["image_id"], plan["receipt_id"], line_id)
        for plan in executable
        for line_id in plan["expanded_move_line_ids"]
    }
    candidate_keys = {
        (str(hit["image_id"]), int(hit["receipt_id"]), int(hit["line_id"]))
        for hit in hits
    }
    summary = {
        "generated_at": generated_at,
        "table": args.table,
        "region": args.region,
        "mode": "read-only-plan",
        "model_source": args.model_source,
        "final_candidate_lines": len(hits),
        "candidate_receipts": len(by_receipt),
        "visual_rows_to_move": sum(
            len({item["row_id"] for item in plan["row_expansions"]})
            for plan in executable
        ),
        "expanded_line_ids_to_move": len(expanded),
        "row_expansion_extra_lines": len(expanded - candidate_keys),
        "existing_txinfo_sections": sum(
            1
            for op in operations
            if op["section_type"] == TXINFO and op["op"] == "modify"
        ),
        "new_txinfo_sections": sum(
            1
            for op in operations
            if op["section_type"] == TXINFO and op["op"] == "create"
        ),
        "planned_operations": len(operations),
        "operations_by_kind": dict(
            sorted(Counter(op["op"] for op in operations).items())
        ),
        "blocked_receipts": len(by_receipt) - len(executable),
        "anomalies": len(anomalies),
        "holdout_overlap": len(overlaps),
        "holdout_hits": [f"{image_id}::{receipt_id}" for image_id, receipt_id in overlaps],
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

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "CORRECTION_PLAN.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "CORRECTION_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    write_report(summary, args.output / "CORRECTION_PLAN.md")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
