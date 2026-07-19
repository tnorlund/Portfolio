#!/usr/bin/env python3
"""section_compare.py -- per-section REAL-vs-SYNTH crops for one receipt.

Renders a receipt through the current merchant profile (identical recipe to
``glyph_review.py receipt``), fetches the real cdn pixels, resizes both to a
common canvas, then crops named vertical bands and stacks REAL | SYNTH per
section. Drives the fidelity loop: fix what the crops reveal, re-run, re-compare.

Usage:
  section_compare.py <merchant> <image_id> <receipt_id> <slug> [out_root]

Bands are read from ``<slug>`` in SECTION_BANDS below (top-down fractions of
receipt height). Add a merchant by dumping its rows and eyeballing the splits.

Env: same as glyph_review receipt mode (PYTHONPATH, DYNAMODB_TABLE_NAME,
AWS_REGION, BITMATRIX_DIR, PORTFOLIO_ENV=dev).
"""

from __future__ import annotations

import os
import sys
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)

_LABEL_FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"

# Top-down band fractions (y0, y1) of full receipt height, per merchant slug.
SECTION_BANDS = {
    "dollartree": [
        ("STOREFRONT", 0.00, 0.105),
        ("ADDRESS", 0.105, 0.205),
        ("ITEMS", 0.205, 0.585),
        ("SUMMARY", 0.585, 0.670),
        ("PAYMENT", 0.670, 0.775),
        ("FOOTER", 0.775, 1.00),
    ],
    "gelsons": [
        ("STOREFRONT", 0.00, 0.09),
        ("PAYMENT_HDR", 0.09, 0.34),
        ("ITEMS", 0.34, 0.80),
        ("SUMMARY", 0.80, 0.88),
        ("FOOTER", 0.88, 1.00),
    ],
    "thestand": [
        ("STOREFRONT", 0.00, 0.14),
        ("ADDRESS", 0.14, 0.26),
        ("ITEMS", 0.26, 0.66),
        ("SUMMARY", 0.66, 0.80),
        ("PAYMENT", 0.80, 0.90),
        ("FOOTER", 0.90, 1.00),
    ],
}


def _font(size: int):
    try:
        return ImageFont.truetype(_LABEL_FONT, size)
    except OSError:
        return ImageFont.load_default()


def render_pair(merchant: str, image_id: str, receipt_id: int):
    """Return (real_img, syn_img) both at the same (wt, ht) canvas."""
    import boto3
    import render_synthetic_receipts as rsr

    from receipt_dynamo.data.dynamo_client import DynamoClient

    region = os.environ.get("AWS_REGION", "us-east-1")
    table = os.environ["DYNAMODB_TABLE_NAME"]
    c = DynamoClient(table)
    s3 = boto3.client("s3", region_name=region)

    prof = rsr.cached_font_profile(
        table, merchant, region=region, max_receipts=12
    )
    ss = rsr.section_scale_for_merchant(merchant)
    typ = rsr.merchant_typography(merchant)
    atlas = None
    needs_atlas = "bitmap_font" not in typ or (
        "bitmap_font" in typ and "bitmap_thin" not in typ
    )
    if needs_atlas:
        atlas = rsr.cached_glyph_atlas(
            table, merchant, region=region, max_receipts=8
        )

    d = c.get_receipt_details(image_id, receipt_id)
    rec = d.receipt
    ws = [w for w in d.words if w.receipt_id == receipt_id]
    lbl = {
        (l.line_id, l.word_id): l.label
        for l in d.labels
        if l.receipt_id == receipt_id and l.label not in (None, "O")
    }
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
            "labels": (
                [lbl[(w.line_id, w.word_id)]]
                if lbl.get((w.line_id, w.word_id)) not in (None, "O")
                else []
            ),
        }
        for w in ws
    ]
    barcodes = []
    if hasattr(c, "list_receipt_barcodes_from_receipt"):
        try:
            for b in c.list_receipt_barcodes_from_receipt(
                image_id, receipt_id
            ):
                barcodes.append(
                    {
                        "text": getattr(b, "text", "") or "",
                        "symbology": getattr(b, "symbology", ""),
                        "top_left": getattr(b, "top_left", None),
                        "bottom_right": getattr(b, "bottom_right", None),
                    }
                )
        except Exception:  # noqa: BLE001
            barcodes = []

    wt = 760
    ht = int(round(wt * rec.height / rec.width))
    tmp = f"/tmp/_seccompare_{image_id}_{receipt_id}.png"
    rsr._render_cached_hybrid(
        {"words": words, "barcodes": barcodes, "merchant_name": merchant},
        atlas,
        profile=prof,
        width=wt,
        height=ht,
        path=tmp,
        section_scale=ss,
        **typ,
    )
    syn = Image.open(tmp).convert("RGB")
    real = None
    for bkt, key in [
        (rec.cdn_s3_bucket, rec.cdn_s3_key),
        (rec.raw_s3_bucket, rec.raw_s3_key),
    ]:
        if not bkt or not key:
            continue
        try:
            real = Image.open(
                BytesIO(s3.get_object(Bucket=bkt, Key=key)["Body"].read())
            ).convert("RGB")
            break
        except Exception:  # noqa: BLE001
            continue
    if real is not None:
        real = real.resize((wt, ht))
    return real, syn, len(barcodes)


def stack_section(real, syn, name, y0, y1, pad=14):
    W, H = syn.size
    a, b = int(H * y0), int(H * y1)
    rc = real.crop((0, a, W, b)) if real is not None else None
    sc = syn.crop((0, a, W, b))
    ch = (b - a) + 34
    cols = 2 if rc is not None else 1
    cv = Image.new("RGB", (W * cols + pad * (cols + 1), ch), (238, 238, 238))
    dd = ImageDraw.Draw(cv)
    f = _font(15)
    x = pad
    for tag, im in (
        [("REAL", rc), ("SYNTH", sc)] if rc is not None else [("SYNTH", sc)]
    ):
        cv.paste(im, (x, 30))
        dd.text((x, 8), f"{name} · {tag}", fill=(190, 0, 0), font=f)
        x += W + pad
    return cv


def main():
    merchant, image_id, receipt_id, slug = (
        sys.argv[1],
        sys.argv[2],
        int(sys.argv[3]),
        sys.argv[4],
    )
    out_root = sys.argv[5] if len(sys.argv) > 5 else ".out"
    out_dir = os.path.join(out_root, f"{slug}_sections")
    os.makedirs(out_dir, exist_ok=True)
    real, syn, n_bar = render_pair(merchant, image_id, receipt_id)
    print(
        f"rendered {slug}: syn={syn.size} real={'ok' if real else 'MISSING'} "
        f"barcodes_detected={n_bar}"
    )
    bands = SECTION_BANDS.get(slug)
    if not bands:
        raise SystemExit(f"no SECTION_BANDS for slug {slug!r}")
    tiles = []
    for name, y0, y1 in bands:
        cv = stack_section(real, syn, name, y0, y1)
        p = os.path.join(out_dir, f"{name}.png")
        cv.save(p)
        tiles.append(cv)
        print(f"  {name:12s} y[{y0:.3f},{y1:.3f}] -> {p}")
    # full montage
    tw = max(t.width for t in tiles)
    th = sum(t.height for t in tiles) + 10 * (len(tiles) + 1)
    mont = Image.new("RGB", (tw, th), (255, 255, 255))
    y = 10
    for t in tiles:
        mont.paste(t, (0, y))
        y += t.height + 10
    mp = os.path.join(out_dir, f"{slug}_sections_all.png")
    mont.save(mp)
    print(f"montage -> {mp}")


if __name__ == "__main__":
    main()
