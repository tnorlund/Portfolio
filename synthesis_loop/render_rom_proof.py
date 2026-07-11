#!/usr/bin/env python3
"""render_rom_proof.py -- REAL | CURRENT | ROM-FONT side-by-side for one receipt.

Renders a vetted golden receipt three ways at matched canvas height:
  REAL     -- the scanned receipt (CDN S3).
  CURRENT  -- the merchant's shipped render profile (its current atlas/font).
  ROM-FONT -- the SAME profile with the winning recreated ROM atlas swapped in
              for the bitmap_font faces (everything else -- condense, pitch,
              bitmap_thin, stylemap, section scale -- held fixed, so only the
              glyph SHAPES change).

For a merchant that ships a vector font instead of a bitmap atlas (Smith's uses
VT323), the ROM panel injects a bitmap_font block with borrowed grocery knobs so
the ROM glyphs can be placed -- an approximate, eyeball-only config.

Scratch render cache + RECEIPT_PAPER_STRENGTH honored from the environment.

Usage:
  RECEIPT_PAPER_STRENGTH=0.3 python render_rom_proof.py \
      --merchant Vons --image <id> --receipt 1 \
      --rom .out/rom-fonts/bitMatrix-C1-heavy.glyphs.npz \
      --out .out/rom-fonts/proof_vons.png
"""
from __future__ import annotations

import argparse
import copy
import os
import sys
from io import BytesIO

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_HERE, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "receipt_dynamo")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PIL import Image, ImageDraw  # noqa: E402

# Borrowed bitmap knobs for a vector-font merchant (Smith's): grocery defaults.
_BITMAP_FALLBACK = dict(
    bitmap_cap_ratio=0.66,
    ocr_font_sizing=True,
    ocr_cap_height_ratio=0.649,
    mixed_layout=True,
    bitmap_thin=0.3,
    pitch_ratio=0.538,
)


def _render_synth(rsr, merchant, rec, words, barcodes, prof, ss, typ, wt, ht, path):
    rsr._render_cached_hybrid(
        {"words": words, "barcodes": barcodes, "merchant_name": merchant},
        None if "bitmap_font" in typ else typ.get("_atlas"),
        profile=prof,
        width=wt,
        height=ht,
        path=path,
        section_scale=ss,
        **{k: v for k, v in typ.items() if not k.startswith("_")},
    )
    return Image.open(path).convert("RGB")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--merchant", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--receipt", type=int, required=True)
    ap.add_argument("--rom", required=True, help="winning ROM .glyphs.npz")
    ap.add_argument("--rom-heavy", default=None, help="ROM heavy face (default: --rom)")
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--table",
        default=os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22"),
    )
    args = ap.parse_args(argv)
    os.environ["DYNAMODB_TABLE_NAME"] = args.table
    region = os.environ.get("AWS_REGION", "us-east-1")

    import boto3

    import render_synthetic_receipts as rsr
    from receipt_dynamo.data.dynamo_client import DynamoClient

    c = DynamoClient(table_name=args.table, region=region)
    s3 = boto3.client("s3", region_name=region)

    prof = rsr.cached_font_profile(args.table, args.merchant, region=region, max_receipts=12)
    ss = rsr.section_scale_for_merchant(args.merchant)
    typ_current = rsr.merchant_typography(args.merchant)

    # atlas needed when the merchant has no bitmap_font (renders from cached glyphs)
    atlas = None
    if "bitmap_font" not in typ_current:
        atlas = rsr.cached_glyph_atlas(args.table, args.merchant, region=region, max_receipts=8)
        typ_current["_atlas"] = atlas

    d = c.get_image_details(args.image)
    rec = next((r for r in d.receipts if str(r.receipt_id) == str(args.receipt)), None)
    if rec is None:
        raise SystemExit(f"receipt {args.receipt} not in image {args.image}")
    ws = [w for w in d.receipt_words if w.receipt_id == args.receipt]
    words = [
        {
            "text": w.text,
            "line_id": w.line_id,
            "word_id": w.word_id,
            "bbox": [
                w.top_left["x"] * 1000,
                w.top_left["y"] * 1000,
                w.bottom_right["x"] * 1000,
                w.bottom_right["y"] * 1000,
            ],
            "labels": [],
        }
        for w in ws
    ]
    barcodes = []

    wt = 760
    ht = int(round(wt * rec.height / rec.width))

    # CURRENT panel
    syn_cur = _render_synth(
        rsr, args.merchant, rec, words, barcodes, prof, ss, typ_current, wt, ht,
        args.out + ".current.png",
    )

    # ROM panel: swap bitmap_font faces to the ROM npz, hold everything else.
    rom_heavy = args.rom_heavy or args.rom
    typ_rom = copy.deepcopy({k: v for k, v in typ_current.items() if k != "_atlas"})
    if "bitmap_font" in typ_rom:
        typ_rom["bitmap_font"] = {"regular": args.rom, "heavy": rom_heavy}
    else:
        # vector-font merchant: inject a bitmap_font + borrowed knobs
        typ_rom["bitmap_font"] = {"regular": args.rom, "heavy": rom_heavy}
        for k, v in _BITMAP_FALLBACK.items():
            typ_rom.setdefault(k, v)
    syn_rom = _render_synth(
        rsr, args.merchant, rec, words, barcodes, prof, ss, typ_rom, wt, ht,
        args.out + ".rom.png",
    )

    # REAL panel
    real = None
    for bkt, key in [(rec.cdn_s3_bucket, rec.cdn_s3_key), (rec.raw_s3_bucket, rec.raw_s3_key)]:
        if not bkt or not key:
            continue
        try:
            real = Image.open(BytesIO(s3.get_object(Bucket=bkt, Key=key)["Body"].read())).convert("RGB")
            break
        except Exception:  # noqa: BLE001
            continue

    # compose at matched panel height
    wd = 380
    def byw(im):
        w, h = im.size
        return im.resize((wd, int(h * wd / w)))

    panels = [("REAL", real), ("CURRENT", syn_cur), ("ROM-FONT", syn_rom)]
    panels = [(t, byw(im)) for t, im in panels if im is not None]
    hh = max(im.height for _, im in panels) + 40
    cv = Image.new("RGB", (wd * len(panels) + 20 * (len(panels) + 1), hh), (235, 235, 235))
    dd = ImageDraw.Draw(cv)
    x = 20
    for t, im in panels:
        cv.paste(im, (x, 34))
        dd.text((x, 12), t, fill=(200, 0, 0))
        x += wd + 20
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    cv.save(args.out)
    print(f"{args.merchant} {args.image}:{args.receipt} -> {args.out}")
    for t, p in [("current", args.out + ".current.png"), ("rom", args.out + ".rom.png")]:
        print(f"  {t}: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
