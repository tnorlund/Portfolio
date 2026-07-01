#!/usr/bin/env python3
"""Backfill ReceiptBarcode records for existing receipts.

Detects barcodes / QR codes on each receipt's CDN crop with the Swift
``receipt-ocr --detect-barcodes-only`` binary (fast: no OCR/warp) and writes
``ReceiptBarcode`` rows. Because detection runs on the per-receipt crop, the
normalized barcode geometry is already in that receipt's coordinate space --
the same space as its lines/words.

Idempotent: with ``--apply`` a receipt's existing barcodes are deleted before
the freshly detected ones are written, so the backfill can be re-run safely.
Dry-run by default (enumerates + detects + reports, writes nothing).

Env / dev-prod:
  Table resolved from ``DYNAMODB_TABLE_NAME`` if set, else via
  ``receipt_dynamo.data._pulumi.load_env(PORTFOLIO_ENV)['dynamodb_table_name']``
  (PORTFOLIO_ENV defaults to "dev").

Usage:
  PORTFOLIO_ENV=dev python scripts/backfill_receipt_barcodes.py \
      [--apply] [--limit N] [--merchant "The Home Depot"] \
      [--binary receipt_ocr_swift/.build/debug/receipt-ocr]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
import tempfile
from math import atan2, degrees

import boto3

from receipt_dynamo import DynamoClient, ReceiptBarcode

REGION = os.environ.get("AWS_REGION", "us-east-1")
# C0 control characters + DEL, stripped from barcode payload ends (Vision
# prepends a mode/ECI control byte to some QR/byte-mode payloads).
_CTRL_CHARS = "".join(chr(i) for i in range(0x20)) + "\x7f"
DEFAULT_BINARY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "receipt_ocr_swift/.build/debug/receipt-ocr",
)


def resolve_table() -> str:
    table = os.environ.get("DYNAMODB_TABLE_NAME")
    if table:
        return table
    from receipt_dynamo.data._pulumi import load_env

    env = os.environ.get("PORTFOLIO_ENV", "dev")
    return load_env(env=env)["dynamodb_table_name"]


def barcode_from_swift(image_id, receipt_id, idx, data) -> ReceiptBarcode | None:
    """Map one Swift barcode dict -> ReceiptBarcode (or None if malformed)."""
    symbology = data.get("symbology")
    bbox = data.get("bounding_box", {})
    if not symbology or not all(
        k in bbox for k in ("x", "y", "width", "height")
    ):
        return None
    corners = ("top_left", "top_right", "bottom_left", "bottom_right")
    if not all(
        all(k in data.get(c, {}) for k in ("x", "y")) for c in corners
    ):
        return None
    tl, tr = data["top_left"], data["top_right"]
    ar = atan2(tr["y"] - tl["y"], tr["x"] - tl["x"])
    conf = min(1.0, max(0.01, float(data.get("confidence") or 1.0)))
    try:
        return ReceiptBarcode(
            image_id=image_id,
            receipt_id=receipt_id,
            barcode_id=idx,
            symbology=str(symbology),
            text=(data.get("payload") or "").strip(_CTRL_CHARS),
            bounding_box=bbox,
            top_left=tl,
            top_right=tr,
            bottom_left=data["bottom_left"],
            bottom_right=data["bottom_right"],
            angle_degrees=degrees(ar),
            angle_radians=ar,
            confidence=conf,
        )
    except (ValueError, AssertionError):
        return None


def detect_on_crop(binary, s3, receipt, workdir) -> list[dict] | None:
    """Download a receipt's CDN crop and run barcode-only detection.

    Returns the list of raw Swift barcode dicts, or None if the crop is
    unavailable / detection failed.
    """
    bkt, key = receipt.cdn_s3_bucket, receipt.cdn_s3_key
    if not bkt or not key:
        return None
    img_path = os.path.join(workdir, "crop.png")
    out_dir = os.path.join(workdir, "out")
    out_json = os.path.join(out_dir, "crop.json")
    # Clear any prior receipt's output so a failed/crashed detection can never
    # be misread as this receipt's barcodes (the temp paths are reused).
    try:
        os.remove(out_json)
    except FileNotFoundError:
        pass
    try:
        with open(img_path, "wb") as fh:
            fh.write(s3.get_object(Bucket=bkt, Key=key)["Body"].read())
    except Exception:  # noqa: BLE001
        return None
    try:
        result = subprocess.run(
            [binary, "--detect-barcodes-only", img_path,
             "--output-dir", out_dir],
            capture_output=True, timeout=60, check=False,
        )
        # Only trust output from a clean exit that produced a fresh file.
        if result.returncode != 0 or not os.path.exists(out_json):
            return None
        with open(out_json, encoding="utf-8") as fh:
            return json.load(fh).get("barcodes") or []
    except Exception:  # noqa: BLE001
        return None


def iter_receipts(client, limit, merchant):
    """Yield receipts (optionally filtered to one merchant), up to ``limit``."""
    if merchant:
        places, _ = client.get_receipt_places_by_merchant(merchant)
        seen = set()
        for p in places:
            k = (str(p.image_id), int(p.receipt_id))
            if k in seen:
                continue
            seen.add(k)
            # get_receipt avoids get_image_details, which scans the whole image
            # partition and throws if any sibling item is malformed (missing
            # TYPE) -- a pre-existing data issue that must not abort the backfill.
            try:
                yield client.get_receipt(str(p.image_id), int(p.receipt_id))
            except Exception as exc:  # noqa: BLE001
                print(f"  skip {k}: {type(exc).__name__}", file=sys.stderr)
            if limit and len(seen) >= limit:
                return
        return
    lek = None
    n = 0
    while True:
        receipts, lek = client.list_receipts(
            limit=100, last_evaluated_key=lek
        )
        for r in receipts:
            yield r
            n += 1
            if limit and n >= limit:
                return
        if not lek:
            return


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="Write ReceiptBarcode rows (default: dry-run)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--merchant", default=None,
                    help="Restrict to one merchant (exact name)")
    ap.add_argument("--binary", default=DEFAULT_BINARY)
    args = ap.parse_args()

    if not os.path.exists(args.binary):
        print(f"ERROR: receipt-ocr binary not found at {args.binary}\n"
              f"Build it: (cd receipt_ocr_swift && swift build --product "
              f"receipt-ocr)", file=sys.stderr)
        return 2

    table = resolve_table()
    client = DynamoClient(table_name=table, region=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] table={table} merchant={args.merchant or 'ALL'} "
          f"limit={args.limit or 'ALL'}")

    stats = collections.Counter()
    symbologies = collections.Counter()
    with tempfile.TemporaryDirectory() as workdir:
        for receipt in iter_receipts(client, args.limit, args.merchant):
            stats["receipts"] += 1
            try:
                raw = detect_on_crop(args.binary, s3, receipt, workdir)
                if raw is None:
                    stats["no_crop"] += 1
                    continue
                barcodes = []
                for idx, d in enumerate(raw):
                    bc = barcode_from_swift(
                        str(receipt.image_id), int(receipt.receipt_id), idx, d
                    )
                    if bc is not None:
                        barcodes.append(bc)
                        symbologies[bc.symbology] += 1
                if barcodes:
                    stats["with_barcode"] += 1
                    stats["total_barcodes"] += len(barcodes)
                    if any(b.payload for b in barcodes):
                        stats["decoded"] += 1
                if args.apply:
                    client.delete_receipt_barcodes_from_receipt(
                        str(receipt.image_id), int(receipt.receipt_id)
                    )
                    if barcodes:
                        client.add_receipt_barcodes(barcodes)
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                print(f"  error on {receipt.image_id} r{receipt.receipt_id}: "
                      f"{type(exc).__name__}: {exc}", file=sys.stderr)
            if stats["receipts"] % 25 == 0:
                print(f"  ...{stats['receipts']} receipts, "
                      f"{stats['decoded']} decoded", flush=True)

    n = stats["receipts"] or 1
    print("\n===== BACKFILL REPORT =====")
    print(f"  receipts scanned:   {stats['receipts']}")
    print(f"  crop unavailable:   {stats['no_crop']}")
    print(f"  errors (skipped):   {stats['errors']}")
    print(f"  with >=1 barcode:   {stats['with_barcode']} "
          f"({100 * stats['with_barcode'] // n}%)")
    print(f"  with decoded:       {stats['decoded']} "
          f"({100 * stats['decoded'] // n}%)")
    print(f"  total barcodes:     {stats['total_barcodes']}")
    print(f"  symbologies:        {dict(symbologies)}")
    print(f"  written:            "
          f"{'yes' if args.apply else 'NO (dry-run, use --apply)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
