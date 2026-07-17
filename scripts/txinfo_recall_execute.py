#!/usr/bin/env python3
"""Conditionally execute a verified TRANSACTION_INFO correction plan in dev.

The default mode is a full consistent-read preflight. Writes require both
``--apply`` and the exact plan SHA. Each receipt is mutated atomically with a
DynamoDB transaction and immediately re-read before execution continues.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer


DEV_TABLE = "ReceiptsTable-dc5be22"
PROD_TABLE = "ReceiptsTable-d7ff76a"
TXINFO = "TRANSACTION_INFO"
_DESERIALIZER = TypeDeserializer()
_SERIALIZER = TypeSerializer()


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    return value


def decimalize(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: decimalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decimalize(item) for item in value]
    return value


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        jsonable(value), separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def serialize_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _SERIALIZER.serialize(decimalize(value))
        for key, value in item.items()
    }


def deserialize_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: _DESERIALIZER.deserialize(value) for key, value in item.items()}


def item_key(item: dict[str, Any]) -> tuple[str, str]:
    return str(item["PK"]), str(item["SK"])


def raw_key(key: tuple[str, str]) -> dict[str, Any]:
    return {"PK": {"S": key[0]}, "SK": {"S": key[1]}}


def batch_get_items(
    client: Any, table: str, keys: Iterable[tuple[str, str]]
) -> dict[tuple[str, str], dict[str, Any]]:
    unique = sorted(set(keys))
    items: list[dict[str, Any]] = []
    for offset in range(0, len(unique), 100):
        pending = [raw_key(key) for key in unique[offset : offset + 100]]
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
            raise RuntimeError(
                f"batch-get left {len(pending)} unprocessed section keys"
            )
    return {item_key(item): item for item in items}


def validate_plan(
    plan: dict[str, Any],
    table: str,
    confirmed_sha: str | None,
    txinfo_target_status: str = "PENDING",
) -> str:
    summary = plan["summary"]
    if table == PROD_TABLE or table != DEV_TABLE:
        raise ValueError(f"executor is dev-only; refusing table {table!r}")
    if summary.get("table") != table:
        raise ValueError(
            f"plan table {summary.get('table')!r} does not match {table!r}"
        )
    if plan.get("anomalies") or summary.get("anomalies"):
        raise ValueError("plan contains anomalies")
    if summary.get("blocked_receipts") or summary.get("holdout_overlap"):
        raise ValueError("plan has blocked receipts or spent-holdout overlap")
    computed = canonical_hash(
        {
            "anomalies": plan["anomalies"],
            "per_receipt": plan["per_receipt"],
        }
    )
    if computed != summary.get("plan_sha256"):
        raise ValueError("plan SHA does not match plan contents")
    if confirmed_sha is not None and confirmed_sha != computed:
        raise ValueError("--confirm-plan-sha does not match the plan")

    for receipt in plan["per_receipt"].values():
        for operation in receipt["operations"]:
            current = operation.get("current_item")
            target = operation.get("target_item")
            if operation["op"] == "create" and current is not None:
                raise ValueError("create operation unexpectedly has a current item")
            if operation["op"] != "create" and current is None:
                raise ValueError("modify/delete operation lacks a current item")
            if operation["op"] == "delete" and target is not None:
                raise ValueError("delete operation unexpectedly has a target item")
            if operation["op"] != "delete" and target is None:
                raise ValueError("create/modify operation lacks a target item")
            for item in (current, target):
                if item is None:
                    continue
                expected_pk = f"IMAGE#{receipt['image_id']}"
                expected_prefix = f"RECEIPT#{int(receipt['receipt_id']):05d}#SECTION#"
                if item.get("PK") != expected_pk or not str(item.get("SK", "")).startswith(
                    expected_prefix
                ):
                    raise ValueError("operation item escapes its planned receipt")
            if operation["section_type"] == TXINFO and target is not None:
                if target.get("validation_status") != txinfo_target_status:
                    raise ValueError(
                        "TRANSACTION_INFO target status must be "
                        f"{txinfo_target_status}"
                    )
    return computed


def preflight(
    client: Any, table: str, plan: dict[str, Any]
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    operations = [
        operation
        for receipt in plan["per_receipt"].values()
        for operation in receipt["operations"]
    ]
    keys = [
        item_key(operation.get("current_item") or operation["target_item"])
        for operation in operations
    ]
    live = batch_get_items(client, table, keys)
    drift = []
    for operation in operations:
        current = operation.get("current_item")
        target = operation.get("target_item")
        key = item_key(current or target)
        actual = live.get(key)
        if current is None:
            if actual is not None:
                drift.append({"key": key, "issue": "create target already exists"})
            continue
        expected_hash = operation["current_fingerprint"]
        if actual is None:
            drift.append({"key": key, "issue": "current item is missing"})
        elif canonical_hash(actual) != expected_hash:
            drift.append(
                {
                    "key": key,
                    "issue": "current item fingerprint drift",
                    "expected": expected_hash,
                    "actual": canonical_hash(actual),
                }
            )
    return live, drift


def current_condition(item: dict[str, Any]) -> tuple[str, dict[str, str], dict[str, Any]]:
    names = {"#pk": "PK", "#sk": "SK"}
    values: dict[str, Any] = {}
    clauses = ["attribute_exists(#pk)", "attribute_exists(#sk)"]
    for index, (name, value) in enumerate(
        sorted((key, value) for key, value in item.items() if key not in {"PK", "SK"})
    ):
        name_key = f"#n{index}"
        value_key = f":v{index}"
        names[name_key] = name
        values[value_key] = _SERIALIZER.serialize(decimalize(value))
        clauses.append(f"{name_key} = {value_key}")
    return " AND ".join(clauses), names, values


def transaction_item(table: str, operation: dict[str, Any]) -> dict[str, Any]:
    current = operation.get("current_item")
    target = operation.get("target_item")
    if operation["op"] == "create":
        return {
            "Put": {
                "TableName": table,
                "Item": serialize_item(target),
                "ConditionExpression": "attribute_not_exists(#pk) AND attribute_not_exists(#sk)",
                "ExpressionAttributeNames": {"#pk": "PK", "#sk": "SK"},
            }
        }

    condition, names, values = current_condition(current)
    request = {
        "TableName": table,
        "Key": raw_key(item_key(current)),
        "ConditionExpression": condition,
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": values,
    }
    if operation["op"] == "delete":
        return {"Delete": request}
    request.pop("Key")
    request["Item"] = serialize_item(target)
    return {"Put": request}


def receipt_operations(plan: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (key, receipt)
        for key, receipt in sorted(plan["per_receipt"].items())
        if receipt["operations"]
    ]


def verify_receipt(
    client: Any, table: str, receipt: dict[str, Any]
) -> list[dict[str, Any]]:
    operations = receipt["operations"]
    keys = [
        item_key(operation.get("target_item") or operation["current_item"])
        for operation in operations
    ]
    live = batch_get_items(client, table, keys)
    failures = []
    for operation in operations:
        target = operation.get("target_item")
        current = operation.get("current_item")
        key = item_key(target or current)
        actual = live.get(key)
        if target is None and actual is not None:
            failures.append({"key": key, "issue": "deleted item still exists"})
        elif target is not None and actual is None:
            failures.append({"key": key, "issue": "target item is missing"})
        elif target is not None and canonical_hash(actual) != canonical_hash(target):
            failures.append({"key": key, "issue": "target fingerprint mismatch"})
    return failures


def save_log(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output-log", type=Path, required=True)
    parser.add_argument("--table", default=DEV_TABLE)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-plan-sha")
    parser.add_argument(
        "--txinfo-target-status",
        choices=("PENDING", "VALID"),
        default="PENDING",
    )
    args = parser.parse_args()

    plan = json.loads(args.plan.read_text())
    confirmed = args.confirm_plan_sha if args.apply else None
    plan_sha = validate_plan(
        plan,
        args.table,
        confirmed,
        txinfo_target_status=args.txinfo_target_status,
    )
    if args.apply and not args.confirm_plan_sha:
        raise ValueError("--apply requires --confirm-plan-sha")

    client = boto3.client("dynamodb", region_name=args.region)
    _live, drift = preflight(client, args.table, plan)
    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    log: dict[str, Any] = {
        "mode": "apply" if args.apply else "preflight-only",
        "table": args.table,
        "region": args.region,
        "plan_sha256": plan_sha,
        "started_at": started_at,
        "preflight_drift": drift,
        "receipts": [],
        "completed_receipts": 0,
        "stopped": None,
    }
    if drift:
        log["stopped"] = "PREFLIGHT_DRIFT"
        log["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        save_log(args.output_log, log)
        raise RuntimeError(f"preflight found {len(drift)} drifted operations")

    receipts = receipt_operations(plan)
    if not args.apply:
        log["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        log["planned_receipts"] = len(receipts)
        log["planned_operations"] = sum(
            len(receipt["operations"]) for _key, receipt in receipts
        )
        save_log(args.output_log, log)
        print(json.dumps(log, indent=2, sort_keys=True))
        return

    for index, (key, receipt) in enumerate(receipts, start=1):
        entry = {
            "receipt_key": key,
            "image_id": receipt["image_id"],
            "receipt_id": receipt["receipt_id"],
            "merchant": receipt.get("merchant", ""),
            "operations": len(receipt["operations"]),
            "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        try:
            client.transact_write_items(
                TransactItems=[
                    transaction_item(args.table, operation)
                    for operation in receipt["operations"]
                ],
                ClientRequestToken=hashlib.sha256(
                    f"{plan_sha}:{key}".encode()
                ).hexdigest()[:36],
            )
            failures = verify_receipt(client, args.table, receipt)
            if failures:
                raise RuntimeError(f"post-write verification failed: {failures}")
            entry["status"] = "PASS"
            log["completed_receipts"] = index
        except Exception as error:
            entry["status"] = "FAIL"
            entry["error"] = f"{type(error).__name__}: {error}"
            log["stopped"] = {"receipt_key": key, "reason": entry["error"]}
            log["receipts"].append(entry)
            log["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            save_log(args.output_log, log)
            raise
        entry["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        log["receipts"].append(entry)
        save_log(args.output_log, log)
        if index % 25 == 0 or index == len(receipts):
            print(f"verified {index}/{len(receipts)} receipts", flush=True)

    log["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    save_log(args.output_log, log)
    print(
        json.dumps(
            {
                "completed_receipts": log["completed_receipts"],
                "plan_sha256": plan_sha,
                "stopped": log["stopped"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
