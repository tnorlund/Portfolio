#!/usr/bin/env python3
"""glyph_review.py -- review artifacts for a data-built merchant glyph atlas.

Two modes, so the builder (codex) and the per-letter reviewer (headless Claude)
share the SAME deterministic images:

  glyph_review.py sheet   <atlas.npz> <out.png>
      A labeled per-glyph contact sheet: every glyph drawn on its baseline at a
      uniform cap height, char label in red. This is the per-letter review input.

  glyph_review.py receipt <merchant> <image_id> <receipt_id> <out.png>
      A REAL-vs-SYNTH side-by-side of one receipt rendered in the current profile
      (the on-receipt spacing/legibility test). Needs AWS + the render env.

Env for receipt mode: PYTHONPATH=receipt_agent:receipt_dynamo:receipt_upload,
DYNAMODB_TABLE_NAME, AWS_REGION, BITMATRIX_DIR, FONT_LIB, PORTFOLIO_ENV=dev.
"""
from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
_CAP_REF = "ABDEFGHKLMNPRSTUVXZ0123456789"
_LABEL_FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"


def _label_font(size: int):
    try:
        return ImageFont.truetype(_LABEL_FONT, size)
    except OSError:
        return ImageFont.load_default()


def sheet(atlas_path: str, out_png: str) -> int:
    a = np.load(atlas_path)
    chars = sorted(chr(int(k[1:])) for k in a.files if k.startswith("c"))
    caph = float(np.median([a[f"c{ord(c)}"].shape[0]
                            for c in _CAP_REF if f"c{ord(c)}" in a.files]))
    cap, tw, th, cols = 54, 96, 110, 10
    rows = (len(chars) + cols - 1) // cols
    img = Image.new("RGB", (cols * tw, rows * th), (255, 255, 255))
    dd = ImageDraw.Draw(img)
    lab = _label_font(16)
    for i, ch in enumerate(chars):
        g = a[f"c{ord(ch)}"]
        off = int(a[f"o{ord(ch)}"])
        sc = cap / caph
        nh, nw = max(1, int(g.shape[0] * sc)), max(1, int(g.shape[1] * sc))
        gi = Image.fromarray(((1 - g) * 255).astype(np.uint8), "L").convert("RGB")
        gi = gi.resize((nw, nh), Image.NEAREST)
        cx, cy = (i % cols) * tw, (i // cols) * th
        base = cy + 20 + cap
        dd.line([(cx, base), (cx + tw, base)], fill=(210, 210, 255))
        img.paste(gi, (cx + (tw - nw) // 2, base + int(off * sc) - nh))
        dd.rectangle([cx, cy, cx + tw - 1, cy + 18], fill=(240, 240, 240))
        dd.text((cx + 3, cy + 1), repr(ch), fill=(200, 0, 0), font=lab)
        dd.rectangle([cx, cy, cx + tw - 1, cy + th - 1], outline=(220, 220, 220))
    img.save(out_png)
    missing = "".join(ch for ch in "!\"#$%&'()*+,-./0123456789:;<=>?@"
                      "ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]_"
                      "abcdefghijklmnopqrstuvwxyz|~" if ch not in chars)
    print(f"{len(chars)} glyphs -> {out_png}")
    print(f"absent (TTF-fallback): {missing!r}")
    return 0


def receipt(merchant: str, image_id: str, receipt_id: int, out_png: str) -> int:
    from io import BytesIO

    import boto3
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
    sys.path.insert(0, HERE)
    import render_synthetic_receipts as rsr  # noqa: E402
    from receipt_dynamo.data.dynamo_client import DynamoClient  # noqa: E402

    table = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    region = os.environ.get("AWS_REGION", "us-east-1")
    c = DynamoClient(table_name=table, region=region)
    s3 = boto3.client("s3", region_name=region)
    atlas = rsr.cached_glyph_atlas(table, merchant, region=region, max_receipts=8)
    prof = rsr.cached_font_profile(table, merchant, region=region, max_receipts=12)
    ss = rsr.section_scale_for_merchant(merchant)
    typ = rsr.merchant_typography(merchant)

    d = c.get_image_details(image_id)
    rec = next(r for r in d.receipts if r.receipt_id == receipt_id)
    ws = [w for w in d.receipt_words if w.receipt_id == receipt_id]
    lbl = {(l.line_id, l.word_id): l.label
           for l in d.receipt_word_labels if l.receipt_id == receipt_id}
    words = [{
        "text": w.text,
        "bbox": [w.top_left["x"] * 1000, w.top_left["y"] * 1000,
                 w.bottom_right["x"] * 1000, w.bottom_right["y"] * 1000],
        "labels": ([lbl[(w.line_id, w.word_id)]]
                   if lbl.get((w.line_id, w.word_id)) not in (None, "O") else []),
    } for w in ws]
    wt = 760
    ht = int(round(wt * rec.height / rec.width))
    tmp = out_png + ".syn.png"
    rsr._render_cached_hybrid({"words": words, "merchant_name": merchant}, atlas,
                              profile=prof, width=wt, height=ht, path=tmp,
                              section_scale=ss, **typ)
    syn = Image.open(tmp).convert("RGB")
    real = None
    for bkt, key in [(rec.cdn_s3_bucket, rec.cdn_s3_key),
                     (rec.raw_s3_bucket, rec.raw_s3_key)]:
        if not bkt or not key:
            continue
        try:
            real = Image.open(BytesIO(
                s3.get_object(Bucket=bkt, Key=key)["Body"].read())).convert("RGB")
            break
        except Exception:  # noqa: BLE001
            continue
    wd = 400

    def byw(im):
        w, h = im.size
        return im.resize((wd, int(h * wd / w)))
    panels = [("REAL", real), ("SYNTH", syn)]
    panels = [(t, byw(im)) for t, im in panels if im is not None]
    hh = max(im.height for _, im in panels) + 40
    cv = Image.new("RGB", (wd * len(panels) + 20 * (len(panels) + 1), hh),
                   (235, 235, 235))
    dd = ImageDraw.Draw(cv)
    lab = _label_font(16)
    x = 20
    for t, im in panels:
        cv.paste(im, (x, 34))
        dd.text((x, 12), t, fill=(200, 0, 0), font=lab)
        x += wd + 20
    cv.save(out_png)
    print(f"{merchant} {image_id}:{receipt_id} -> {out_png}")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    mode = sys.argv[1]
    if mode == "sheet" and len(sys.argv) == 4:
        return sheet(sys.argv[2], sys.argv[3])
    if mode == "receipt" and len(sys.argv) == 6:
        return receipt(sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5])
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
