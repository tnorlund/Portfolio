"""Stage 4 — gated cleanup of ORPHANED images (0 receipts) left by the merge.

When a cross-image duplicate receipt is dropped (Stage 3), its parent image can
be left with zero receipts. This removes such orphaned images completely:

  * DynamoDB: every item under ``PK = IMAGE#{id}`` (Image + any image-level OCR
    lines/words/letters + OCR jobs/routing), via ``delete_image_details``.
  * S3: the raw object + up to 13 CDN variants (specific keys only — NEVER a
    bucket sync/--delete).

**Hard safety guard:** an image is only eligible if it currently has ZERO
``RECEIPT`` items. Multi-receipt images and survivors are refused.

DRY-RUN unless ``apply=True``. On apply a restore bundle is written first (the
raw DynamoDB items + the downloaded S3 objects), and ``rollback_cleanup`` re-puts
the items and re-uploads the objects — because the sitebucket has no versioning.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

# CDN variant key attributes on the Image entity (raw is handled separately).
CDN_VARIANT_FIELDS = [
    "cdn_s3_key", "cdn_webp_s3_key", "cdn_avif_s3_key",
    "cdn_thumbnail_s3_key", "cdn_thumbnail_webp_s3_key", "cdn_thumbnail_avif_s3_key",
    "cdn_small_s3_key", "cdn_small_webp_s3_key", "cdn_small_avif_s3_key",
    "cdn_medium_s3_key", "cdn_medium_webp_s3_key", "cdn_medium_avif_s3_key",
]


@dataclass
class S3Obj:
    bucket: str
    key: str


@dataclass
class ImageCleanup:
    image_id: str
    s3_objects: List[S3Obj] = field(default_factory=list)
    dynamo_type_counts: Dict[str, int] = field(default_factory=dict)


def image_s3_targets(image) -> List[S3Obj]:
    """Extract the raw + CDN S3 objects to delete for an Image entity (pure)."""
    out: List[S3Obj] = []
    raw_b, raw_k = getattr(image, "raw_s3_bucket", None), getattr(image, "raw_s3_key", None)
    if raw_b and raw_k:
        out.append(S3Obj(raw_b, raw_k))
    cdn_b = getattr(image, "cdn_s3_bucket", None)
    if cdn_b:
        for fld in CDN_VARIANT_FIELDS:
            k = getattr(image, fld, None)
            if k:
                out.append(S3Obj(cdn_b, k))
    # de-dup (raw_key sometimes equals a cdn key)
    seen, uniq = set(), []
    for o in out:
        if (o.bucket, o.key) not in seen:
            seen.add((o.bucket, o.key)); uniq.append(o)
    return uniq


def _query_image_items(dynamo, image_id: str) -> List[dict]:
    """Raw DynamoDB items under PK=IMAGE#{id} (paginated, low-level)."""
    items, lek = [], None
    while True:
        kw = dict(
            TableName=dynamo.table_name,
            KeyConditionExpression="#pk = :pk",
            ExpressionAttributeNames={"#pk": "PK"},
            ExpressionAttributeValues={":pk": {"S": f"IMAGE#{image_id}"}},
        )
        if lek:
            kw["ExclusiveStartKey"] = lek
        resp = dynamo._client.query(**kw)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
    return items


def _type_counts(items: List[dict]) -> Dict[str, int]:
    c: Dict[str, int] = {}
    for it in items:
        t = it.get("TYPE", {}).get("S", "UNKNOWN")
        c[t] = c.get(t, 0) + 1
    return c


def plan_image_cleanup(dynamo, image_records: Dict[str, object], image_ids: List[str]
                       ) -> Tuple[List[ImageCleanup], List[dict]]:
    """Build cleanups for orphaned images; REFUSE any image with receipts.

    ``image_records`` maps image_id -> Image entity (for S3 keys).
    Returns (eligible_cleanups, refused).
    """
    cleanups, refused = [], []
    for image_id in image_ids:
        items = _query_image_items(dynamo, image_id)
        counts = _type_counts(items)
        n_receipts = counts.get("RECEIPT", 0)
        if n_receipts > 0:
            refused.append({"image_id": image_id, "receipt_count": n_receipts,
                            "reason": "still has receipts"})
            continue
        img = image_records.get(image_id)
        s3 = image_s3_targets(img) if img is not None else []
        cleanups.append(ImageCleanup(image_id=image_id, s3_objects=s3,
                                     dynamo_type_counts=counts))
    return cleanups, refused


def summarize(cleanups: List[ImageCleanup]) -> dict:
    return {
        "images": len(cleanups),
        "s3_objects": sum(len(c.s3_objects) for c in cleanups),
        "dynamo_items": sum(sum(c.dynamo_type_counts.values()) for c in cleanups),
    }


def execute_cleanup(cleanups: List[ImageCleanup], dynamo=None, s3=None, *,
                    apply: bool = False, backup_dir: Optional[str] = None) -> dict:
    """DRY-RUN unless apply=True. Backs up Dynamo items + S3 objects first."""
    report = {"dry_run": not apply, "images_deleted": 0, "dynamo_items_deleted": 0,
              "s3_deleted": 0, "s3_missing": 0, "backup_dir": None, "errors": []}
    if not apply:
        report["images_deleted"] = len(cleanups)
        return report
    if dynamo is None or s3 is None:
        raise ValueError("apply=True requires dynamo + s3 clients")
    if not backup_dir:
        raise ValueError("apply=True requires a backup_dir (no S3 versioning)")
    os.makedirs(backup_dir, exist_ok=True)

    for c in cleanups:
        try:
            # 1) re-verify orphaned (guard against races) and capture items
            items = _query_image_items(dynamo, c.image_id)
            if _type_counts(items).get("RECEIPT", 0) > 0:
                report["errors"].append(f"{c.image_id}: gained receipts, skipped")
                continue
            # 2) backup dynamo items
            with open(os.path.join(backup_dir, f"{c.image_id}.dynamo.json"), "w") as f:
                json.dump(items, f)
            # 3) backup + delete S3 objects (download first; no versioning)
            obj_manifest = []
            for o in c.s3_objects:
                local = os.path.join(backup_dir, "s3", o.bucket, o.key)
                os.makedirs(os.path.dirname(local), exist_ok=True)
                try:
                    s3.download_file(o.bucket, o.key, local)
                except Exception:
                    report["s3_missing"] += 1
                    continue  # object doesn't exist; nothing to delete/restore
                s3.delete_object(Bucket=o.bucket, Key=o.key)
                report["s3_deleted"] += 1
                obj_manifest.append({"bucket": o.bucket, "key": o.key, "local": local})
            with open(os.path.join(backup_dir, f"{c.image_id}.s3.json"), "w") as f:
                json.dump(obj_manifest, f)
            # 4) delete all DynamoDB items under the image
            counts = dynamo.delete_image_details(c.image_id)
            report["images_deleted"] += 1
            report["dynamo_items_deleted"] += sum(counts.values()) if counts else 0
        except Exception as e:  # pragma: no cover
            report["errors"].append(f"{c.image_id}: {e}")
    report["backup_dir"] = backup_dir
    return report


def rollback_cleanup(backup_dir: str, dynamo, s3) -> dict:
    """Reverse a cleanup: re-put Dynamo items + re-upload S3 objects."""
    report = {"items_restored": 0, "s3_restored": 0, "errors": []}
    for fn in sorted(os.listdir(backup_dir)):
        path = os.path.join(backup_dir, fn)
        if fn.endswith(".dynamo.json"):
            for item in json.load(open(path)):
                try:
                    dynamo._client.put_item(TableName=dynamo.table_name, Item=item)
                    report["items_restored"] += 1
                except Exception as e:  # pragma: no cover
                    report["errors"].append(f"restore item: {e}")
        elif fn.endswith(".s3.json"):
            for o in json.load(open(path)):
                try:
                    s3.upload_file(o["local"], o["bucket"], o["key"])
                    report["s3_restored"] += 1
                except Exception as e:  # pragma: no cover
                    report["errors"].append(f"restore s3 {o['key']}: {e}")
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=["dev", "prod"], required=True)
    ap.add_argument("--image-ids", help="comma-separated, or a path to a .txt (one per line)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backup-dir", help="restore bundle dir (required with --apply)")
    ap.add_argument("--rollback", help="backup_dir of a prior cleanup to reverse")
    args = ap.parse_args()

    from receipt_dynamo import DynamoClient
    from receipt_upload.dedup.dossiers import ENV_TABLE
    import boto3

    dynamo = DynamoClient(ENV_TABLE[args.env])
    s3 = boto3.client("s3")

    if args.rollback:
        print(json.dumps(rollback_cleanup(args.rollback, dynamo, s3), indent=2))
        return

    raw = args.image_ids or ""
    ids = [x.strip() for x in (open(raw).read().splitlines() if os.path.exists(raw)
                               else raw.split(",")) if x.strip()]
    images = {im.image_id: im for im in dynamo.list_images()[0]}
    cleanups, refused = plan_image_cleanup(dynamo, images, ids)
    print(f"Eligible orphaned images: {len(cleanups)} | refused (have receipts): {len(refused)}")
    for r in refused:
        print(f"  REFUSE {r['image_id'][:12]}: {r['reason']} ({r['receipt_count']} receipts)")
    print(json.dumps(summarize(cleanups), indent=2))
    for c in cleanups:
        print(f"  - {c.image_id[:12]} | {sum(c.dynamo_type_counts.values())} dynamo items "
              f"{dict(c.dynamo_type_counts)} | {len(c.s3_objects)} S3 objects")

    if not args.apply:
        print("\nDRY-RUN — nothing deleted. Add --apply --backup-dir <dir> to execute.")
        return
    if not args.backup_dir:
        raise SystemExit("--apply requires --backup-dir")
    report = execute_cleanup(cleanups, dynamo, s3, apply=True, backup_dir=args.backup_dir)
    print(f"\nCLEANED: {json.dumps(report, indent=2)}")
    print(f"\nRollback with:\n  python -m receipt_upload.dedup.cleanup_images "
          f"--env {args.env} --rollback {args.backup_dir}")


if __name__ == "__main__":
    main()
