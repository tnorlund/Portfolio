#!/usr/bin/env python3
"""A/B/C render harness for a candidate HEAVY atlas.

Renders one real receipt through the standard profile/stylemap pipeline three
times, differing only in the profile's ``bitmap_font.heavy`` slot:

  A) heavy := regular       -> stylemap-bold rows fall back to 1px double-strike
  B) heavy := CANDIDATE     -> bold rows draw the candidate face (chars the
                               candidate lacks fall through to the TTF fallback)
  C) heavy := $COMPOSITE_HEAVY_NPZ (optional env) -> e.g. candidate glyphs
                               backfilled with the regular face for coverage

Same canvas geometry for every variant. Writes the per-variant renders plus a
REAL | A | B [| C] side-by-side composite, and prints the line-scorecard
summary per variant (severity counts must not regress vs A).

Usage:
  render_face_ab.py <merchant> <slug> <image_id> <receipt_id> <heavy.npz> <outdir>

Env: DYNAMODB_TABLE_NAME (default ReceiptsTable-dc5be22), AWS creds,
COMPOSITE_HEAVY_NPZ (optional C variant), BITMATRIX_DIR for the atlases.
"""
from __future__ import annotations

import json
import os
import sys
from io import BytesIO

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (
    _HERE,
    os.path.join(_ROOT, "scripts"),
    os.path.join(_ROOT, "receipt_agent"),
    os.path.join(_ROOT, "receipt_dynamo"),
    os.path.join(_ROOT, "receipt_upload"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main() -> int:
    if len(sys.argv) < 7:
        print(__doc__)
        return 2
    merchant, slug, iid, rid_s, heavy_npz, outdir = sys.argv[1:7]
    rid = int(rid_s)
    os.makedirs(outdir, exist_ok=True)

    import boto3
    from PIL import Image, ImageDraw, ImageFont

    import render_synthetic_receipts as rsr
    from receipt_line_scorecard import score_receipt_images

    from receipt_dynamo.data.dynamo_client import DynamoClient

    table = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    region = os.environ.get("AWS_REGION", "us-east-1")
    c = DynamoClient(table_name=table, region=region)
    s3 = boto3.client("s3", region_name=region)
    prof = rsr.cached_font_profile(table, merchant, region=region, max_receipts=12)
    ss = rsr.section_scale_for_merchant(merchant)
    typ = rsr.merchant_typography(merchant)
    if "bitmap_font" not in typ:
        raise SystemExit(f"{merchant}: no resolvable bitmap_font (atlases missing)")

    d = c.get_image_details(iid)
    rec = next((r for r in d.receipts if str(r.receipt_id) == str(rid)), None)
    if rec is None:
        raise SystemExit(f"receipt {rid} not found for {iid}")
    lbl = {
        (l.line_id, l.word_id): l.label
        for l in d.receipt_word_labels
        if l.receipt_id == rid
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
        for w in d.receipt_words
        if w.receipt_id == rid
    ]
    barcodes = []
    if hasattr(c, "list_receipt_barcodes_from_receipt"):
        try:
            barcodes = [
                {
                    "text": getattr(b, "text", "") or "",
                    "symbology": getattr(b, "symbology", ""),
                    "top_left": getattr(b, "top_left", None),
                    "bottom_right": getattr(b, "bottom_right", None),
                    "confidence": getattr(b, "confidence", None),
                }
                for b in c.list_receipt_barcodes_from_receipt(iid, rid)
            ]
        except Exception:  # noqa: BLE001
            barcodes = []

    wt = 760
    ht = int(round(wt * rec.height / rec.width))
    reg = typ["bitmap_font"]["regular"]
    variants = {
        "A_regular_only": {"regular": reg, "heavy": reg},
        "B_section_heavy": {"regular": reg, "heavy": heavy_npz},
    }
    comp = os.environ.get("COMPOSITE_HEAVY_NPZ")
    if comp:
        variants["C_composite_heavy"] = {"regular": reg, "heavy": comp}

    synths = {}
    for name, bf in variants.items():
        t2 = dict(typ)
        t2["bitmap_font"] = bf
        path = os.path.join(outdir, f"{slug}_{iid[:8]}_{rid}_{name}.png")
        rsr._render_cached_hybrid(
            {"words": words, "barcodes": barcodes, "merchant_name": merchant},
            None,
            profile=prof,
            width=wt,
            height=ht,
            path=path,
            section_scale=ss,
            **t2,
        )
        synths[name] = Image.open(path).convert("RGB")

    real = None
    for bkt, key in (
        (rec.cdn_s3_bucket, rec.cdn_s3_key),
        (rec.raw_s3_bucket, rec.raw_s3_key),
    ):
        if not bkt or not key:
            continue
        try:
            real = Image.open(
                BytesIO(s3.get_object(Bucket=bkt, Key=key)["Body"].read())
            ).convert("RGB")
            break
        except Exception:  # noqa: BLE001
            continue

    scores = {}
    if real is not None:
        real_m = real.resize((wt, ht))
        for name, syn in synths.items():
            try:
                sm = score_receipt_images(real_m, syn, words)["summary"]
                scores[name] = {
                    "severity_counts": sm["severity_counts"],
                    "height_ratio_median": sm["height_ratio_median"],
                    "density_ratio_median": sm["density_ratio_median"],
                    "wpc_ratio_median": sm["wpc_ratio_median"],
                }
            except Exception as e:  # noqa: BLE001
                scores[name] = {"error": str(e)[:80]}

    wd = 380

    def _byw(im):
        w, h = im.size
        return im.resize((wd, int(h * wd / w)))

    panels = [("REAL", real)] if real is not None else []
    panels += [(n.replace("_", " "), im) for n, im in synths.items()]
    panels = [(t, _byw(im)) for t, im in panels]
    hh = max(im.height for _, im in panels) + 40
    cv = Image.new(
        "RGB", (wd * len(panels) + 20 * (len(panels) + 1), hh), (235, 235, 235)
    )
    dd = ImageDraw.Draw(cv)
    try:
        lab = ImageFont.truetype(
            "/System/Library/Fonts/Supplemental/Arial.ttf", 15
        )
    except OSError:
        lab = ImageFont.load_default()
    x = 20
    for t, im in panels:
        cv.paste(im, (x, 34))
        dd.text((x, 12), t, fill=(200, 0, 0), font=lab)
        x += wd + 20
    out = os.path.join(outdir, f"ab_{slug}_{iid[:8]}_{rid}.png")
    cv.save(out)
    print(json.dumps({"out": out, "scores": scores}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
