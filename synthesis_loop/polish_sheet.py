#!/usr/bin/env python3
"""polish_sheet.py -- REAL | v0 | POLISHED 3-panel sheet for one merchant.

Renders the merchant fresh (current profile = polished), pulls the real cdn
pixels and the prior v0 synth render, scales all three to a common height and
lays them side by side with captions.

Usage:
  polish_sheet.py <merchant> <image_id> <receipt_id> <v0_syn_png> <out_png>
"""

from __future__ import annotations

import os
import sys
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
_FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def _font(sz):
    try:
        return ImageFont.truetype(_FONT, sz)
    except OSError:
        return ImageFont.load_default()


def main():
    merchant, iid, rid, v0_png, out_png = (
        sys.argv[1],
        sys.argv[2],
        int(sys.argv[3]),
        sys.argv[4],
        sys.argv[5],
    )
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
    atlas = rsr.cached_glyph_atlas(
        table, merchant, region=region, max_receipts=8
    )
    d = c.get_receipt_details(iid, rid)
    rec = d.receipt
    ws = [w for w in d.words if w.receipt_id == rid]
    lbl = {
        (l.line_id, l.word_id): l.label
        for l in d.labels
        if l.receipt_id == rid and l.label not in (None, "O")
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
    wt = 760
    ht = int(round(wt * rec.height / rec.width))
    tmp = "/tmp/_polish_syn.png"
    rsr._render_cached_hybrid(
        {"words": words, "barcodes": [], "merchant_name": merchant},
        atlas,
        profile=prof,
        width=wt,
        height=ht,
        path=tmp,
        section_scale=ss,
        **typ,
    )
    polished = Image.open(tmp).convert("RGB")
    real = None
    for bkt, key in [
        (rec.cdn_s3_bucket, rec.cdn_s3_key),
        (rec.raw_s3_bucket, rec.raw_s3_key),
    ]:
        if bkt and key:
            try:
                real = Image.open(
                    BytesIO(s3.get_object(Bucket=bkt, Key=key)["Body"].read())
                ).convert("RGB")
                break
            except Exception:  # noqa: BLE001
                continue
    v0 = Image.open(v0_png).convert("RGB") if os.path.exists(v0_png) else None

    H = 1500
    cols = []

    def scaled(im):
        return im.resize((int(im.width * H / im.height), H))

    for tag, im in [
        ("REAL", real),
        ("v0 (before)", v0),
        ("POLISHED", polished),
    ]:
        if im is not None:
            cols.append((tag, scaled(im)))
    pad = 24
    cap_h = 40
    total_w = sum(im.width for _, im in cols) + pad * (len(cols) + 1)
    sheet = Image.new("RGB", (total_w, H + cap_h + pad), (245, 245, 245))
    dd = ImageDraw.Draw(sheet)
    f = _font(26)
    x = pad
    for tag, im in cols:
        sheet.paste(im, (x, cap_h))
        dd.text((x, 8), tag, fill=(190, 0, 0), font=f)
        x += im.width + pad
    sheet.save(out_png)
    print(f"{merchant} -> {out_png}  {sheet.size}")


if __name__ == "__main__":
    main()
