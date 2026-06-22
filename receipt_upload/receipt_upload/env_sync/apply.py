"""Gated executor for the cross-environment record migration.

DRY-RUN unless ``apply=True``. Writes the target DynamoDB items in batches and
copies the referenced S3 objects cross-bucket, rewriting each item's
``*_s3_bucket`` attribute to the target env's bucket. Idempotent: a DynamoDB
key already present is harmless to re-put, and an S3 object already present is
skipped. A missing S3 *source* (see issue #993) is counted, not fatal — the
CDN copy still carries the image.

A backup of every key written and object created is saved BEFORE mutation so
``rollback`` can fully reverse the migration.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List

from botocore.exceptions import ClientError

from receipt_upload.dedup._ddb import AWS_ERRORS, raw_client
from receipt_upload.env_sync.plan import (
    MigrationPlan,
    partition_items,
    remap_bucket,
)


def reconcile_receipt_counts(dynamo, image_ids=None) -> dict:
    """Recompute ``Image.receipt_count`` from the actual RECEIPT items.

    ``receipt_count`` drives the GSI3 ``list_images_by_type(receipt_count=)``
    query and is NOT auto-maintained when receipts are added (migration) or
    removed (dedup), so it drifts. Pass ``image_ids`` to scope the repair, or
    leave it None to reconcile every image. Only mismatches are written.
    """
    images = {im.image_id: im for im in dynamo.list_images()[0]}
    targets = list(images) if image_ids is None else list(set(image_ids))
    report = {"checked": 0, "fixed": 0, "errors": []}
    for iid in targets:
        img = images.get(iid)
        if img is None:
            continue
        report["checked"] += 1
        actual = sum(
            1
            for it in partition_items(dynamo, iid)
            if it.get("TYPE", {}).get("S") == "RECEIPT"
        )
        if img.receipt_count != actual:
            try:
                img.receipt_count = actual
                dynamo.update_image(img)
                report["fixed"] += 1
            except AWS_ERRORS as exc:
                report["errors"].append(f"{iid}: {exc}")
    return report


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _remap_item_buckets(item: dict) -> dict:
    """Rewrite ``*_s3_bucket`` attribute values to the counterpart bucket."""
    for attr, val in item.items():
        if (
            attr.endswith("_s3_bucket")
            and isinstance(val, dict)
            and "S" in val
        ):
            val["S"] = remap_bucket(val["S"])
    return item


def _batch_put(client, table: str, items: List[dict]) -> int:
    written = 0
    for i in range(0, len(items), 25):
        chunk = items[i : i + 25]
        request = {
            table: [{"PutRequest": {"Item": it}} for it in chunk]
        }
        resp = client.batch_write_item(RequestItems=request)
        unprocessed = resp.get("UnprocessedItems") or {}
        while unprocessed:
            resp = client.batch_write_item(RequestItems=unprocessed)
            unprocessed = resp.get("UnprocessedItems") or {}
        written += len(chunk)
    return written


def _s3_source_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        # AccessDenied / throttling / expired creds etc. are NOT "absent" —
        # surface them so the caller doesn't silently treat them as missing.
        raise


def execute_migration(
    plan: MigrationPlan,
    dst_dynamo,
    s3,
    *,
    apply: bool = False,
    backup_path: str = None,
    sample_s3_check: int = 250,
) -> dict:
    """DRY-RUN unless ``apply=True``. Backs up before any write."""
    report = {
        "dry_run": not apply,
        "src": plan.src_env,
        "dst": plan.dst_env,
        "items_to_write": len(plan.dynamo_items),
        "s3_to_copy": len(plan.s3_copies),
        "items_written": 0,
        "s3_copied": 0,
        "s3_already_present": 0,
        "s3_source_missing": 0,
        "backup_path": None,
        "errors": [],
    }

    if not apply:
        # sample source existence to estimate the missing-crop (#993) rate
        sample = plan.s3_copies[:sample_s3_check]
        missing = 0
        for b, k, _, _ in sample:
            try:
                if not _s3_source_exists(s3, b, k):
                    missing += 1
            except ClientError:
                pass  # transient/access error in the estimate -> skip
        report["s3_source_missing_sampled"] = f"{missing}/{len(sample)}"
        return report

    if not backup_path:
        raise ValueError("apply=True requires backup_path")

    items = [_remap_item_buckets(dict(it)) for it in plan.dynamo_items]
    backup = {
        "dst_table": getattr(dst_dynamo, "table_name", None),
        "created_at": _now_iso(),
        "added_keys": [
            {"PK": it["PK"], "SK": it["SK"]} for it in items
        ],
        "s3_created": [],
    }

    client = raw_client(dst_dynamo)
    # ---- S3 first: copy objects so migrated rows never dangle ----
    created = []
    for src_b, src_k, dst_b, dst_k in plan.s3_copies:
        try:
            if _s3_source_exists(s3, dst_b, dst_k):
                report["s3_already_present"] += 1
                continue
            if not _s3_source_exists(s3, src_b, src_k):
                report["s3_source_missing"] += 1
                continue
            s3.copy_object(
                CopySource={"Bucket": src_b, "Key": src_k},
                Bucket=dst_b,
                Key=dst_k,
            )
            created.append([dst_b, dst_k])
            report["s3_copied"] += 1
        except AWS_ERRORS as exc:
            report["errors"].append(f"s3 {dst_b}/{dst_k}: {exc}")
    backup["s3_created"] = created

    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(backup, f)
    report["backup_path"] = backup_path

    # If any S3 copy FAILED (not merely a missing source), do NOT write the
    # DynamoDB rows — they would reference target objects that were never
    # copied (dangling). The migration is idempotent, so re-run after the
    # cause (AccessDenied / throttling / etc.) is resolved.
    if report["errors"]:
        report["aborted_before_dynamo"] = True
        return report

    # ---- DynamoDB: batched puts ----
    try:
        report["items_written"] = _batch_put(
            client, dst_dynamo.table_name, items
        )
    except AWS_ERRORS as exc:
        report["errors"].append(f"dynamo batch: {exc}")
        return report

    # Adding a receipt to an EXISTING target image leaves its receipt_count
    # stale (GSI3); recompute it for those images. New full-partition images
    # carry their own count from the copied IMAGE item.
    if plan.new_receipts:
        recount = reconcile_receipt_counts(
            dst_dynamo, {img for img, _rid in plan.new_receipts}
        )
        report["receipt_count_fixed"] = recount["fixed"]
        report["errors"].extend(recount["errors"])
    return report


def rollback(backup_path: str, dst_dynamo, s3) -> dict:
    """Reverse a migration: delete added items + delete created S3 objects."""
    with open(backup_path, encoding="utf-8") as f:
        data = json.load(f)
    table = getattr(dst_dynamo, "table_name", None) or data.get("dst_table")
    client = raw_client(dst_dynamo)
    report = {"items_deleted": 0, "s3_deleted": 0, "errors": []}
    keys = data.get("added_keys", [])
    for i in range(0, len(keys), 25):
        chunk = keys[i : i + 25]
        request = {
            table: [{"DeleteRequest": {"Key": k}} for k in chunk]
        }
        try:
            resp = client.batch_write_item(RequestItems=request)
            unprocessed = resp.get("UnprocessedItems") or {}
            while unprocessed:
                resp = client.batch_write_item(RequestItems=unprocessed)
                unprocessed = resp.get("UnprocessedItems") or {}
            report["items_deleted"] += len(chunk)
        except AWS_ERRORS as exc:
            report["errors"].append(f"dynamo delete: {exc}")
    for bucket, key in data.get("s3_created", []):
        try:
            s3.delete_object(Bucket=bucket, Key=key)
            report["s3_deleted"] += 1
        except AWS_ERRORS as exc:
            report["errors"].append(f"s3 {bucket}/{key}: {exc}")
    return report
