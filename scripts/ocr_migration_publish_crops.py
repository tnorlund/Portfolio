#!/usr/bin/env python3
"""Publish regenerated receipt crops after the re-OCR migration.

``ocr_migration_apply.py`` rewrites the receipt hierarchy in dev DynamoDB but
deliberately leaves the CDN work out of scope: the new Receipt rows carry the
new bounds/dimensions and a conventional ``raw_s3_key`` while their ``cdn_*``
fields are still null (or stale) and the site bucket still serves the OLD
crops. This tool closes that gap, per migrated image:

  1. Query the image's top-level RECEIPT rows from dev DynamoDB
     (SK exactly ``RECEIPT#{id:05d}``, TYPE=RECEIPT).
  2. Re-warp each crop from the LOCAL raw PNG using the receipt's corner
     geometry — the exact PIL PERSPECTIVE transform the ingestion lambda's
     in-process fallback uses (``ocr_processor._obtain_receipt_crop``), so the
     published crop matches what ingestion would have produced.
  3. Upload the raw crop PNG to the receipt's ``raw_s3_bucket`` and all 12 CDN
     formats (full/thumbnail/small/medium x jpg/webp/avif) to the site bucket
     under ``assets/{image_id}/{receipt_id}``.
  4. ``update_item`` the Receipt row's ``cdn_*`` fields exactly as ingestion's
     ``_apply_cdn_keys`` does (S for present keys, NULL for missing).
  5. Detect ORPHANED old crops: ``assets/{image_id}/`` objects whose leading
     receipt_id no longer exists (re-segmentation changed receipt counts) and
     delete them.

At the end, ONE CloudFront invalidation batches ``/assets/{image_id}/*`` for
every touched image (chunked at the API's 3000-path limit).

Safety (same philosophy as ``ocr_migration_rehearsal._make_client``): this
tool targets REAL AWS by design, so any non-``--dry-run`` invocation is
refused without an explicit ``--allow-aws``. ``--dry-run`` computes and
reports everything — including which orphans WOULD be deleted — with zero
writes.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

import boto3
from PIL import Image as PIL_Image

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "receipt_dynamo"))
sys.path.insert(0, str(REPO_ROOT / "receipt_upload"))
from receipt_dynamo.entities import Receipt  # noqa: E402
from receipt_upload.geometry import find_perspective_coeffs  # noqa: E402
from receipt_upload.utils import (  # noqa: E402
    upload_all_cdn_formats,
    upload_png_to_s3,
)
from scripts import ocr_migration_rehearsal as reh  # noqa: E402

LOG = logging.getLogger("ocr_migration_publish_crops")

DEFAULT_STACK = "tnorlund/portfolio/dev"
INVALIDATION_MAX_PATHS = 3000  # CloudFront create-invalidation hard limit

# Top-level receipt rows only (children are RECEIPT#..#LINE#.. etc.).
_RECEIPT_SK = re.compile(r"^RECEIPT#\d{5}$")
# A receipt CDN asset basename starts with the integer receipt_id followed by
# a size/format suffix: "3.jpg", "3_thumbnail.webp", "12_medium.avif", ...
_ASSET_RID = re.compile(r"^(\d+)[._]")

# Maps a Receipt CDN entity field to the key returned by
# upload_all_cdn_formats — identical to ocr_processor._CDN_KEY_FIELDS.
CDN_KEY_FIELDS: tuple[tuple[str, str], ...] = (
    ("cdn_s3_key", "jpeg_full"),
    ("cdn_webp_s3_key", "webp_full"),
    ("cdn_avif_s3_key", "avif_full"),
    ("cdn_thumbnail_s3_key", "jpeg_thumbnail"),
    ("cdn_thumbnail_webp_s3_key", "webp_thumbnail"),
    ("cdn_thumbnail_avif_s3_key", "avif_thumbnail"),
    ("cdn_small_s3_key", "jpeg_small"),
    ("cdn_small_webp_s3_key", "webp_small"),
    ("cdn_small_avif_s3_key", "avif_small"),
    ("cdn_medium_s3_key", "jpeg_medium"),
    ("cdn_medium_webp_s3_key", "webp_medium"),
    ("cdn_medium_avif_s3_key", "avif_medium"),
)


# --------------------------------------------------------------------------- #
# Guardrail                                                                    #
# --------------------------------------------------------------------------- #


def ensure_write_allowed(dry_run: bool, allow_aws: bool) -> None:
    """Refuse any real write without an explicit --allow-aws opt-in.

    This tool has no local-endpoint mode (S3 + CloudFront + the migrated dev
    table ARE the target), so the rehearsal harness's local-first guardrail
    degenerates to: writes require --allow-aws, always.
    """
    if dry_run or allow_aws:
        return
    raise SystemExit(
        "REFUSING to write to real AWS without --allow-aws. This tool uploads "
        "S3 crops, updates dev DynamoDB Receipt rows, and deletes orphaned "
        "CDN objects. Pass --dry-run to preview everything, or add "
        "--allow-aws to deliberately publish."
    )


# --------------------------------------------------------------------------- #
# Warp (pure): port of ocr_processor._get_perspective_coeffs / crop fallback   #
# --------------------------------------------------------------------------- #


def perspective_coeffs(
    receipt: Any, image_width: int, image_height: int
) -> list[float]:
    """Coefficients mapping receipt-PIL -> image-PIL for PIL PERSPECTIVE.

    Identical formulation to ocr_processor._get_perspective_coeffs: receipt
    corners are normalized with a bottom-left origin (hence the ``1 - y``
    flip into PIL's top-left pixel space); the destination is the receipt's
    warped (width, height) rectangle.
    """
    src_points = [
        (
            receipt.top_left["x"] * image_width,
            (1.0 - receipt.top_left["y"]) * image_height,
        ),
        (
            receipt.top_right["x"] * image_width,
            (1.0 - receipt.top_right["y"]) * image_height,
        ),
        (
            receipt.bottom_right["x"] * image_width,
            (1.0 - receipt.bottom_right["y"]) * image_height,
        ),
        (
            receipt.bottom_left["x"] * image_width,
            (1.0 - receipt.bottom_left["y"]) * image_height,
        ),
    ]
    dst_points = [
        (0.0, 0.0),
        (float(receipt.width - 1), 0.0),
        (float(receipt.width - 1), float(receipt.height - 1)),
        (0.0, float(receipt.height - 1)),
    ]
    return find_perspective_coeffs(src_points, dst_points)


def warp_receipt_crop(original: PIL_Image.Image, receipt: Any) -> PIL_Image.Image:
    """Warp the ORIGINAL image to the receipt's bounds/dimensions.

    Same call ingestion's in-process fallback makes, so the published crop is
    pixel-for-pixel what a fresh ingestion would have generated.
    """
    coeffs = perspective_coeffs(receipt, original.width, original.height)
    return original.transform(
        (receipt.width, receipt.height),
        PIL_Image.Transform.PERSPECTIVE,
        coeffs,
        resample=PIL_Image.Resampling.BICUBIC,
    )


# --------------------------------------------------------------------------- #
# CDN key mapping (pure)                                                       #
# --------------------------------------------------------------------------- #


def expected_cdn_keys(base_key: str) -> dict[str, str]:
    """The keys upload_all_cdn_formats WOULD return for base_key (dry-run)."""
    keys: dict[str, str] = {}
    for size in ("full", "thumbnail", "small", "medium"):
        size_key = base_key if size == "full" else f"{base_key}_{size}"
        for fmt, ext in (("jpeg", "jpg"), ("webp", "webp"), ("avif", "avif")):
            keys[f"{fmt}_{size}"] = f"{size_key}.{ext}"
    return keys


def build_cdn_update(
    cdn_keys: Mapping[str, str | None], bucket: str
) -> tuple[str, dict[str, Any]]:
    """(UpdateExpression, ExpressionAttributeValues) for a Receipt row.

    Field-for-field what ingestion persists: ``_apply_cdn_keys`` sets
    ``cdn_s3_bucket`` plus the 12 mapped keys, and ``CDNFieldsMixin``
    serializes a missing key as NULL (e.g. AVIF encode unavailable).
    """
    fields: dict[str, str | None] = {"cdn_s3_bucket": bucket}
    for field_name, dict_key in CDN_KEY_FIELDS:
        fields[field_name] = cdn_keys.get(dict_key)
    expression = "SET " + ", ".join(f"{name} = :v{i}" for i, name in enumerate(fields))
    values: dict[str, Any] = {
        f":v{i}": ({"S": value} if value else {"NULL": True})
        for i, value in enumerate(fields.values())
    }
    return expression, values


# --------------------------------------------------------------------------- #
# Orphan detection (pure)                                                      #
# --------------------------------------------------------------------------- #


def find_orphan_asset_keys(
    listed_keys: Iterable[str],
    image_id: str,
    live_receipt_ids: set[int],
) -> tuple[list[str], list[str]]:
    """Split ``assets/{image_id}/`` listings into (orphans, unrecognized).

    An orphan is an object whose leading integer receipt_id is not a live
    receipt (re-segmentation changed the receipt count, stranding the old
    crop). A key whose basename does not start with ``<digits>[._]`` is
    reported as unrecognized and NEVER deleted.
    """
    prefix = f"assets/{image_id}/"
    orphans: list[str] = []
    unrecognized: list[str] = []
    for key in listed_keys:
        if not key.startswith(prefix):
            continue
        match = _ASSET_RID.match(key[len(prefix) :])
        if not match:
            unrecognized.append(key)
        elif int(match.group(1)) not in live_receipt_ids:
            orphans.append(key)
    return sorted(orphans), sorted(unrecognized)


def chunk_paths(paths: list[str], size: int = INVALIDATION_MAX_PATHS):
    """Yield CloudFront-sized path batches (max 3000 paths per call)."""
    for start in range(0, len(paths), size):
        yield paths[start : start + size]


# --------------------------------------------------------------------------- #
# AWS plumbing                                                                 #
# --------------------------------------------------------------------------- #


def resolve_stack_output(name: str, stack: str) -> str:
    """``pulumi stack output <name>`` from infra/ (explicit flags skip this)."""
    proc = subprocess.run(
        ["pulumi", "stack", "output", name, "--stack", stack],
        cwd=REPO_ROOT / "infra",
        capture_output=True,
        text=True,
        check=True,
    )
    value = proc.stdout.strip()
    if not value:
        raise SystemExit(f"pulumi stack output {name!r} is empty for {stack}")
    return value


def load_receipts(client: Any, table_name: str, image_id: str) -> list[Receipt]:
    """Top-level Receipt rows for an image (SK RECEIPT#{id:05d}, TYPE=RECEIPT)."""
    kwargs: dict[str, Any] = {
        "TableName": table_name,
        "KeyConditionExpression": "PK = :pk AND begins_with(SK, :sk)",
        "ExpressionAttributeValues": {
            ":pk": {"S": f"IMAGE#{image_id}"},
            ":sk": {"S": "RECEIPT#"},
        },
        "ConsistentRead": True,
    }
    receipts: list[Receipt] = []
    while True:
        resp = client.query(**kwargs)
        for item in resp.get("Items", []):
            sk = item.get("SK", {}).get("S", "")
            if _RECEIPT_SK.match(sk) and item.get("TYPE", {}).get("S") == ("RECEIPT"):
                receipts.append(Receipt.from_item(item))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            return sorted(receipts, key=lambda r: r.receipt_id)
        kwargs["ExclusiveStartKey"] = lek


def list_asset_keys(s3: Any, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
    while True:
        resp = s3.list_objects_v2(**kwargs)
        keys.extend(obj["Key"] for obj in resp.get("Contents", []))
        if not resp.get("IsTruncated"):
            return keys
        kwargs["ContinuationToken"] = resp["NextContinuationToken"]


def delete_keys(s3: Any, bucket: str, keys: list[str]) -> None:
    for start in range(0, len(keys), 1000):
        chunk = keys[start : start + 1000]
        resp = s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True},
        )
        errors = resp.get("Errors") or []
        if errors:
            raise RuntimeError(f"delete_objects failed: {errors[:5]}")


def invalidate(cloudfront: Any, distribution_id: str, paths: list[str]) -> list[str]:
    """One create-invalidation per <=3000-path chunk; returns invalidation ids."""
    ids: list[str] = []
    for batch in chunk_paths(sorted(set(paths))):
        resp = cloudfront.create_invalidation(
            DistributionId=distribution_id,
            InvalidationBatch={
                "Paths": {"Quantity": len(batch), "Items": batch},
                "CallerReference": f"publish-crops-{uuid.uuid4()}",
            },
        )
        ids.append(resp["Invalidation"]["Id"])
    return ids


# --------------------------------------------------------------------------- #
# Per-image publish                                                            #
# --------------------------------------------------------------------------- #


def publish_image(
    dynamo: Any,
    s3: Any,
    table_name: str,
    image_id: str,
    raw_dir: Path,
    site_bucket: str,
    dry_run: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "ok",
        "receipts_published": 0,
        "raw_crops_uploaded": [],
        "cdn_objects_uploaded": [],
        "orphans_deleted": [],
        "unrecognized_asset_keys": [],
        "errors": [],
    }
    if dry_run:
        result["dry_run"] = True

    receipts = load_receipts(dynamo, table_name, image_id)
    if not receipts:
        result["status"] = "no-receipts"
        result["errors"].append("no top-level RECEIPT rows in DynamoDB")
        return result

    raw_path = raw_dir / f"{image_id}.png"
    if not raw_path.exists():
        result["status"] = "no-raw"
        result["errors"].append(f"missing local raw image {raw_path}")
        return result
    original = PIL_Image.open(raw_path)
    original.load()

    # Orphans first (independent of per-receipt failures): old crops whose
    # receipt_id vanished when re-OCR re-segmented the image.
    live_ids = {r.receipt_id for r in receipts}
    existing = list_asset_keys(s3, site_bucket, f"assets/{image_id}/")
    orphans, unrecognized = find_orphan_asset_keys(existing, image_id, live_ids)
    result["unrecognized_asset_keys"] = unrecognized

    for receipt in receipts:
        rid = receipt.receipt_id
        try:
            crop = warp_receipt_crop(original, receipt)
            raw_key = (
                receipt.raw_s3_key
                or f"receipts/{image_id}/{image_id}_RECEIPT_{rid:05d}.png"
            )
            base_key = f"assets/{image_id}/{rid}"
            if dry_run:
                cdn_keys: Mapping[str, str | None] = expected_cdn_keys(base_key)
            else:
                upload_png_to_s3(crop, receipt.raw_s3_bucket, raw_key)
                cdn_keys = upload_all_cdn_formats(
                    crop, site_bucket, base_key, generate_thumbnails=True
                )
                expression, values = build_cdn_update(cdn_keys, site_bucket)
                dynamo.update_item(
                    TableName=table_name,
                    Key={
                        "PK": {"S": f"IMAGE#{image_id}"},
                        "SK": {"S": f"RECEIPT#{rid:05d}"},
                    },
                    UpdateExpression=expression,
                    ExpressionAttributeValues=values,
                )
            result["raw_crops_uploaded"].append(raw_key)
            result["cdn_objects_uploaded"].extend(
                cdn_keys[f"{fmt}_{size}"]
                for size in ("full", "thumbnail", "small", "medium")
                for fmt in ("jpeg", "webp", "avif")
                if cdn_keys.get(f"{fmt}_{size}")
            )
            result["receipts_published"] += 1
        except Exception as exc:  # per-receipt: report, keep going
            LOG.exception("publish failed for %s/%s", image_id, rid)
            result["errors"].append(f"receipt {rid}: {exc}")

    if orphans and not dry_run:
        delete_keys(s3, site_bucket, orphans)
    result["orphans_deleted"] = orphans

    if result["errors"]:
        result["status"] = "error"
    return result


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--table-name", required=True)
    p.add_argument(
        "--images",
        type=Path,
        required=True,
        help="file of migrated image_ids, one per line",
    )
    p.add_argument(
        "--raw-dir",
        type=Path,
        required=True,
        help="directory of local raw PNGs (<raw-dir>/<image_id>.png)",
    )
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--allow-aws",
        action="store_true",
        help="required for any real write; refused otherwise",
    )
    p.add_argument("--region", default=None)
    p.add_argument(
        "--site-bucket",
        default=None,
        help="CDN site bucket (default: pulumi output cdn_bucket_name)",
    )
    p.add_argument(
        "--invalidate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="create ONE CloudFront invalidation for touched images",
    )
    p.add_argument(
        "--distribution-id",
        default=None,
        help="CloudFront distribution (default: pulumi output " "cdn_distribution_id)",
    )
    p.add_argument("--stack", default=DEFAULT_STACK)
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper())

    ensure_write_allowed(args.dry_run, args.allow_aws)

    image_ids = reh._read_image_ids(args.images)
    site_bucket = args.site_bucket or resolve_stack_output(
        "cdn_bucket_name", args.stack
    )
    session = boto3.Session(region_name=args.region)
    dynamo = session.client("dynamodb")
    s3 = session.client("s3")

    results: dict[str, Any] = {}
    touched: list[str] = []
    for n, image_id in enumerate(image_ids, 1):
        try:
            results[image_id] = publish_image(
                dynamo,
                s3,
                args.table_name,
                image_id,
                args.raw_dir,
                site_bucket,
                args.dry_run,
            )
        except Exception as exc:  # keep going; the report shows the failure
            LOG.exception("publish failed for %s", image_id)
            results[image_id] = {"status": "error", "errors": [str(exc)]}
        r = results[image_id]
        if r.get("receipts_published") or r.get("orphans_deleted"):
            touched.append(image_id)
        LOG.info(
            "[%d/%d] %s: %s (%d receipts, %d orphans, %d errors)",
            n,
            len(image_ids),
            image_id[:8],
            r["status"],
            r.get("receipts_published", 0),
            len(r.get("orphans_deleted", [])),
            len(r.get("errors", [])),
        )

    # ONE invalidation covering every touched image (chunked at 3000 paths).
    paths = [f"/assets/{i}/*" for i in touched]
    invalidation: dict[str, Any] = {
        "requested": bool(args.invalidate and paths),
        "paths": paths,
    }
    if args.invalidate and paths and not args.dry_run:
        distribution_id = args.distribution_id or resolve_stack_output(
            "cdn_distribution_id", args.stack
        )
        invalidation["distribution_id"] = distribution_id
        invalidation["invalidation_ids"] = invalidate(
            session.client("cloudfront"), distribution_id, paths
        )

    report = {"images": results, "invalidation": invalidation}
    args.report.write_text(json.dumps(report, indent=2))

    errors = {i: r["errors"] for i, r in results.items() if r.get("errors")}
    summary = {
        "images": len(results),
        "ok": sum(1 for r in results.values() if r["status"] == "ok"),
        "receipts_published": sum(
            r.get("receipts_published", 0) for r in results.values()
        ),
        "cdn_objects_uploaded": sum(
            len(r.get("cdn_objects_uploaded", [])) for r in results.values()
        ),
        "orphans_deleted": sum(
            len(r.get("orphans_deleted", [])) for r in results.values()
        ),
        "invalidation_paths": len(paths),
        "dry_run": args.dry_run,
        "images_with_errors": sorted(errors),
    }
    print(json.dumps(summary, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
