#!/usr/bin/env python3
"""Variant-clustered layout template for one merchant (W-G).

READ-ONLY against DynamoDB/S3 (dev): enumerates the merchant's receipts via
the ReceiptPlace GSI1 (``MERCHANT#<SLUG>``), keeps SCAN images only (PHOTO
receipts are recorded as excluded in the output provenance -- perspective
distortion poisons column geometry), runs ``glyphstudio.stylescan.measure``
per receipt into a persisted scan dir, then clusters BEFORE pooling via
``glyphstudio.variant_cluster`` and emits a v3.1-shaped layout payload:

* ``template``  -- dominant cluster as the top-level default (``version: 1``,
  ``columns``/``sections``/``separators``; passes the existing
  ``validate_layout_template`` unchanged), other clusters as
  ``template.variants[]``.
* ``verdict``   -- CONFIRM/REFUTE for the layout-variant proposal, with
  per-cluster support, receipt refs, and distinguishing features.
* ``provenance``-- full corpus + parameter provenance, including the PHOTO
  exclusion list.

Persisted-artifact convention (gitignored via tools/glyph-studio/.gitignore
``.out/``; the LAYOUT of these dirs is the committed contract, the artifacts
are not):

    tools/glyph-studio/.out/stylescan/<merchant_slug>/
        <image_id>_<receipt_id>.json   one stylescan.measure record
        manifest.json                  sha256 per record + run provenance
    tools/glyph-studio/.out/variant_layout/
        <merchant_slug>_variant_layout.json

Usage:
  python scripts/build_variant_layout.py                      # Costco, dev
  python scripts/build_variant_layout.py --skip-existing      # reuse scans
  python scripts/build_variant_layout.py --print-distances    # calibration

This script never writes to DynamoDB and never touches
scripts/merchant_profiles.json -- consuming the payload is W-J's mint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
for _p in (
    os.path.join(_REPO, "tools", "glyph-studio", "py"),
    os.path.join(_REPO, "receipt_dynamo"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from glyphstudio.layout_template import (  # noqa: E402
    validate_layout_template,
)
from glyphstudio.variant_cluster import (  # noqa: E402
    DEFAULT_THRESHOLD,
    MIN_VARIANT_SUPPORT,
    build_variant_payload,
    median_link_summary,
    pairwise_distance_report,
    receipt_key,
)

GLYPH_OUT = os.path.join(_REPO, "tools", "glyph-studio", ".out")


def merchant_slug(merchant_name: str) -> str:
    """Same normalization as ReceiptPlace GSI1PK, lowercased for paths."""
    return re.sub(r"[^A-Z0-9]+", "_", merchant_name.upper()).strip("_").lower()


def _sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def enumerate_receipts(
    table: str, merchant: str
) -> list[tuple[str, int, str]]:
    """All (image_id, receipt_id, image_type) for a merchant, sorted."""
    from receipt_dynamo.data.dynamo_client import DynamoClient

    client = DynamoClient(table)
    places = []
    lek = None
    while True:
        page, lek = client.get_receipt_places_by_merchant(
            merchant, last_evaluated_key=lek
        )
        places.extend(page)
        if not lek:
            break
    image_types: dict[str, str] = {}
    out = []
    for place in places:
        if place.image_id not in image_types:
            image_types[place.image_id] = str(
                client.get_image(place.image_id).image_type
            )
        out.append(
            (
                place.image_id,
                int(place.receipt_id),
                image_types[place.image_id],
            )
        )
    return sorted(set(out))


def run_stylescans(
    receipts: list[tuple[str, int]],
    scan_dir: str,
    *,
    stylescan_merchant: str,
    skip_existing: bool,
) -> tuple[list[tuple[str, dict]], list[dict]]:
    """Measure (or reload) each receipt into ``scan_dir``.

    Returns ``(scans_by_key, failed)``; failures are recorded, not fatal --
    a drifted receipt must not block the corpus measurement.
    """
    from glyphstudio.stylescan import measure

    os.makedirs(scan_dir, exist_ok=True)
    scans_by_key: list[tuple[str, dict]] = []
    failed: list[dict] = []
    for image_id, receipt_id in receipts:
        key = receipt_key(image_id, receipt_id)
        path = os.path.join(scan_dir, f"{image_id}_{receipt_id}.json")
        try:
            if skip_existing and os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    record = json.load(fh)
            else:
                record = measure(
                    image_id, receipt_id, merchant=stylescan_merchant
                )
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(record, fh, indent=1)
            if not record.get("lines"):
                raise RuntimeError("stylescan produced no lines")
            scans_by_key.append((key, record))
            print(
                f"  scan {key}: {len(record['lines'])} lines -> "
                f"{os.path.relpath(path, _REPO)}"
            )
        except Exception as exc:  # noqa: BLE001 - record, keep measuring
            failed.append({"receipt_key": key, "error": str(exc)[:200]})
            print(f"  FAILED {key}: {exc}", file=sys.stderr)
    return scans_by_key, failed


def write_manifest(
    scan_dir: str,
    *,
    merchant: str,
    table: str,
    scans_by_key: list[tuple[str, dict]],
    excluded: list[dict],
    failed: list[dict],
) -> str:
    entries = []
    for key, record in scans_by_key:
        fname = f"{record['image_id']}_{record['receipt_id']}.json"
        entries.append(
            {
                "receipt_key": key,
                "file": fname,
                "sha256": _sha256_file(os.path.join(scan_dir, fname)),
                "n_lines": len(record.get("lines") or []),
            }
        )
    manifest = {
        "merchant": merchant,
        "table": table,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scans": entries,
        "excluded": excluded,
        "failed": failed,
    }
    path = os.path.join(scan_dir, "manifest.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
        fh.write("\n")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--merchant", default="Costco Wholesale")
    ap.add_argument(
        "--stylescan-merchant",
        default="costco",
        help="stylescan rule-set key (see stylescan._MERCHANT_RULES)",
    )
    ap.add_argument(
        "--table",
        default=os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22"),
    )
    ap.add_argument("--scan-dir", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument(
        "--min-variant-support", type=int, default=MIN_VARIANT_SUPPORT
    )
    ap.add_argument("--proposal", default="self-checkout-layout-variant")
    ap.add_argument(
        "--skip-existing",
        action="store_true",
        help="reuse scan records already in the scan dir (default: fresh)",
    )
    ap.add_argument(
        "--print-distances",
        action="store_true",
        help="print the full pairwise distance report (calibration)",
    )
    args = ap.parse_args(argv)
    os.environ.setdefault("DYNAMODB_TABLE_NAME", args.table)

    slug = merchant_slug(args.merchant)
    scan_dir = args.scan_dir or os.path.join(GLYPH_OUT, "stylescan", slug)
    out_path = args.out or os.path.join(
        GLYPH_OUT, "variant_layout", f"{slug}_variant_layout.json"
    )

    print(f"enumerating {args.merchant!r} receipts from {args.table} ...")
    all_receipts = enumerate_receipts(args.table, args.merchant)
    scan_receipts = [(i, r) for i, r, t in all_receipts if t.upper() == "SCAN"]
    excluded = [
        {
            "receipt_key": receipt_key(i, r),
            "reason": f"image_type={t.upper()}",
        }
        for i, r, t in all_receipts
        if t.upper() != "SCAN"
    ]
    print(
        f"  {len(all_receipts)} receipts: {len(scan_receipts)} SCAN, "
        f"{len(excluded)} excluded (non-SCAN)"
    )

    print(f"stylescan -> {os.path.relpath(scan_dir, _REPO)}")
    scans_by_key, failed = run_stylescans(
        scan_receipts,
        scan_dir,
        stylescan_merchant=args.stylescan_merchant,
        skip_existing=args.skip_existing,
    )
    manifest_path = write_manifest(
        scan_dir,
        merchant=args.merchant,
        table=args.table,
        scans_by_key=scans_by_key,
        excluded=excluded,
        failed=failed,
    )
    print(f"manifest -> {os.path.relpath(manifest_path, _REPO)}")
    if len(scans_by_key) < 2:
        raise SystemExit(
            f"need >= 2 usable scans, got {len(scans_by_key)} "
            f"({len(failed)} failed)"
        )

    if args.print_distances:
        report = pairwise_distance_report(scans_by_key)
        for a, b, d in sorted(report, key=lambda t: t[2]):
            print(f"  d={d:.4f}  {a}  {b}")
        print(f"  summary: {median_link_summary(report)}")

    payload = build_variant_payload(
        scans_by_key,
        threshold=args.threshold,
        min_variant_support=args.min_variant_support,
        proposal=args.proposal,
        excluded_receipt_keys=excluded,
        measured_at=datetime.now(timezone.utc).isoformat(),
    )
    payload["provenance"]["failed_receipt_keys"] = failed
    payload["provenance"]["scan_manifest"] = {
        "path": os.path.relpath(manifest_path, _REPO),
        "sha256": _sha256_file(manifest_path),
    }

    problems = validate_layout_template(payload["template"])
    if problems:
        raise SystemExit(f"emitted template failed validation: {problems}")
    for variant in payload["template"]["variants"]:
        problems = validate_layout_template({"version": 1, **variant})
        if problems:
            raise SystemExit(
                f"variant {variant['variant_id']!r} failed validation: "
                f"{problems}"
            )

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
        fh.write("\n")

    verdict = payload["verdict"]
    print(f"\npayload -> {os.path.relpath(out_path, _REPO)}")
    print(f"payload sha256: {_sha256_file(out_path)}")
    print(f"\nverdict: {verdict['status']} ({verdict['reason']})")
    for cluster in verdict["clusters"]:
        print(
            f"  cluster {cluster['variant_id']!r}: "
            f"support={cluster['support']}"
        )
        for ref in cluster["receipt_refs"]:
            print(f"    {ref}")
    for deg in verdict["degenerate_clusters"]:
        print(f"  DEGENERATE cluster ({deg['reason']}): {deg['receipt_refs']}")
    for out in verdict["outliers"]:
        print(f"  outlier(s): {out['receipt_refs']}")
    if verdict["distinguishing_features"]:
        print("distinguishing features:")
        for feat in verdict["distinguishing_features"]:
            print(f"  - {feat}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
