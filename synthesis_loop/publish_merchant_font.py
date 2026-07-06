#!/usr/bin/env python3
"""Publish a Glyph Studio font: compile -> S3 -> Dynamo pointer -> local cache.

The glyph JSON sources stay in git (authored, reviewed, rolled back there);
the compiled .glyphs.npz artifacts are runtime data and deliberately NOT in
git. Before this publisher they lived only in /tmp/bitmatrix — one reboot
from gone and invisible to any other machine. Now:

  S3 (private raw bucket, merchant_fonts/ prefix)  <- content-addressed npz
  Dynamo MerchantFont item per (merchant, face)    <- pointer + metrics
  $BITMATRIX_DIR                                   <- local cache, refreshed

Faces: "regular" from the font sources as-is; "heavy" from the same
skeletons compiled at params.weight = HEAVY_WEIGHT (the fleet-measured
BALANCE DUE +33% stroke).

Usage:
  publish_merchant_font.py <merchant> <font_dir> [--bucket B] [--skip-heavy]
Env: DYNAMODB_TABLE_NAME, AWS_REGION, BITMATRIX_DIR (default /tmp/bitmatrix),
     MERCHANT_FONT_BUCKET (default raw-image-bucket-c779c32).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

import boto3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_HERE = os.path.dirname(os.path.abspath(__file__))
_STUDIO_PY = os.path.join(_HERE, "..", "tools", "glyph-studio", "py")
sys.path.insert(0, os.path.abspath(_STUDIO_PY))

HEAVY_WEIGHT = 1.33
DEFAULT_BUCKET = os.environ.get(
    "MERCHANT_FONT_BUCKET", "raw-image-bucket-c779c32"
)


def _slug(merchant: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in merchant.lower()).strip("_")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit(font_dir: str) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=font_dir, capture_output=True, text=True, timeout=10,
        ).stdout.strip()[:12] or "unknown"
    except Exception:
        return "unknown"


def _compile(font_dir: str, out_npz: str) -> dict:
    """Compile via glyphstudio and enforce the self-checks."""
    from glyphstudio.compile import compile_font  # noqa: PLC0415
    from glyphstudio.schema import load_font  # noqa: PLC0415

    report = compile_font(font_dir, out_npz)
    if report.get("missing") or report.get("empty"):
        raise SystemExit(
            f"compile self-check failed: missing={report.get('missing')} "
            f"empty={report.get('empty')}")
    font = load_font(font_dir)
    target = (font.get("metrics") or {}).get("pitchRatioTarget")
    pitch_check = "no target"
    if target:
        condense = float((font.get("preview") or {}).get("condense", 1.0))
        got = report["advance_ratio"] * condense
        drift = abs(got - target) / target
        pitch_check = f"{got:.3f} vs {target:.3f} " + (
            "OK" if drift <= 0.02 else "DRIFT"
        )
        if drift > 0.02:
            raise SystemExit(
                f"pitch guard failed ({pitch_check}) -- refusing to publish; "
                "glyph width edits moved global spacing"
            )
    report["pitch_check"] = pitch_check
    return report


def _heavy_variant_dir(font_dir: str, tmp: str) -> str:
    hdir = os.path.join(tmp, "font-heavy")
    shutil.copytree(font_dir, hdir)
    fpath = os.path.join(hdir, "font.json")
    font = json.load(open(fpath, encoding="utf-8"))
    font["params"]["weight"] = HEAVY_WEIGHT
    json.dump(font, open(fpath, "w", encoding="utf-8"), indent=1)
    return hdir


def _prebuilt_report(npz_path: str) -> dict:
    """Metrics for a prebuilt atlas (e.g. chart-derived) via BitmapFont."""
    import numpy as np  # noqa: PLC0415
    from receipt_agent.agents.label_evaluator.rendering.bitmap_font import (  # noqa: PLC0415,E501
        BitmapFont,
    )

    bf = BitmapFont(npz_path)
    z = np.load(npz_path)
    return {
        "glyph_count": sum(1 for k in z.files if k.startswith("c")),
        "cap_h": float(bf.cap_h),
        "advance_ratio": float(bf.advance(1.0)),
        "pitch_check": "prebuilt (no source pitch target)",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("merchant")
    ap.add_argument("font_dir")
    ap.add_argument("--bucket", default=DEFAULT_BUCKET)
    ap.add_argument("--skip-heavy", action="store_true")
    ap.add_argument("--npz", default=None,
                    help="publish this prebuilt regular npz instead of "
                         "compiling from sources (chart-derived atlases)")
    ap.add_argument("--heavy-npz", default=None,
                    help="prebuilt heavy npz to publish alongside --npz")
    ap.add_argument("--logo", default=None,
                    help="canonical logo master PNG to vault + cache "
                         "(cache name <base>_logo.png; profile 'logo' field)")
    ap.add_argument("--atlas-name", default=None,
                    help="local cache filename base (default <font dir name>)")
    args = ap.parse_args()

    from receipt_dynamo import DynamoClient  # noqa: PLC0415
    from receipt_dynamo.entities import MerchantFont  # noqa: PLC0415

    table = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    client = DynamoClient(table)
    s3 = boto3.client("s3")
    cache_dir = os.environ.get("BITMATRIX_DIR", "/tmp/bitmatrix")
    os.makedirs(cache_dir, exist_ok=True)

    slug = _slug(args.merchant)
    base = args.atlas_name or os.path.basename(os.path.normpath(args.font_dir))
    commit = _git_commit(args.font_dir)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    prebuilt = {}
    if args.npz:
        prebuilt["regular"] = args.npz
        if args.heavy_npz:
            prebuilt["heavy"] = args.heavy_npz
    faces = {"regular": args.font_dir}
    with tempfile.TemporaryDirectory() as tmp:
        if prebuilt:
            faces = dict.fromkeys(prebuilt, None)
        elif not args.skip_heavy:
            faces["heavy"] = _heavy_variant_dir(args.font_dir, tmp)

        logo_key = None
        if args.logo:
            logo_key = f"merchant_fonts/{slug}/logo-{_sha256(args.logo)[:8]}.png"
            s3.upload_file(args.logo, args.bucket, logo_key)
            logo_cache = os.path.join(cache_dir, f"{base}_logo.png")
            shutil.copy(args.logo, logo_cache)
            print(f"logo -> s3://{args.bucket}/{logo_key} + cache {logo_cache}")

        stylemap_local = os.path.join(args.font_dir, "stylemap.json")
        stylemap_key = None
        if os.path.exists(stylemap_local):
            stylemap_key = f"merchant_fonts/{slug}/stylemap-{_sha256(stylemap_local)[:8]}.json"
            s3.upload_file(stylemap_local, args.bucket, stylemap_key)
            shutil.copy(stylemap_local, os.path.join(cache_dir, f"{base}.stylemap.json"))
            print(f"stylemap -> s3://{args.bucket}/{stylemap_key}")

        for face, fdir in faces.items():
            suffix = ".glyphs.npz" if face == "regular" else "-heavy.glyphs.npz"
            cache_filename = f"{base}{suffix}"
            if prebuilt:
                npz = prebuilt[face]
                report = _prebuilt_report(npz)
            else:
                npz = os.path.join(tmp, f"{face}.npz")
                report = _compile(fdir, npz)
            digest = _sha256(npz)
            key = f"merchant_fonts/{slug}/{face}-{digest[:12]}.npz"
            s3.upload_file(npz, args.bucket, key)
            item = MerchantFont(
                merchant_name=args.merchant,
                face=face,
                s3_bucket=args.bucket,
                s3_key=key,
                content_hash=digest,
                source_commit=commit,
                compiled_at=now,
                cap_h=float(report["cap_h"]),
                advance_ratio=float(report["advance_ratio"]),
                pitch_check=report["pitch_check"],
                glyph_count=int(report["glyph_count"]),
                stylemap_s3_key=stylemap_key if face == "regular" else None,
                logo_s3_key=logo_key if face == "regular" else None,
                cache_filename=cache_filename,
            )
            client.add_merchant_font(item)
            # refresh the local cache under the profile's filenames
            local = os.path.join(cache_dir, cache_filename)
            if os.path.exists(local):
                shutil.copy(local, local + f".bak-{now.replace(':', '')}")
            if os.path.islink(local):
                os.unlink(local)
            if os.path.abspath(npz) != os.path.abspath(local):
                shutil.copy(npz, local)
            print(f"{face}: {report['glyph_count']} glyphs "
                  f"cap_h={report['cap_h']:.1f} adv={report['advance_ratio']:.3f} "
                  f"pitch[{report['pitch_check']}]")
            print(f"  -> s3://{args.bucket}/{key}")
            print(f"  -> dynamo MERCHANT_FONT#{args.merchant} FACE#{face}")
            print(f"  -> cache {local}")

    # inkthin caches are per-atlas; stale ones poison density
    cleared = 0
    for f in os.listdir("/tmp/render_cache") if os.path.isdir("/tmp/render_cache") else []:
        if "inkthin" in f:
            os.remove(os.path.join("/tmp/render_cache", f))
            cleared += 1
    print(f"cleared {cleared} inkthin cache(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
