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

import math
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
        ("STOREFRONT", 0.00, 0.05),
        ("ADDRESS", 0.05, 0.095),
        ("PAYMENT_HDR", 0.095, 0.34),
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


def _render_true_words(
    words: list[dict],
    box_sink: list[dict],
    *,
    width: int,
    height: int,
) -> list[dict]:
    """Return renderer-format words with bboxes from their actual draw plan.

    ``box_sink`` uses absolute output pixels while the fidelity evaluator
    consumes 0..1000 receipt coordinates with y increasing upward. Words
    intentionally represented by a graphic (the logo) or a structural draw
    path (a full asterisk rule) have no sink entry and retain their source box.
    """
    placements: dict[int, dict] = {}
    for placement in box_sink:
        index = placement.get("word_index")
        px = placement.get("px")
        if not isinstance(index, int) or not isinstance(px, (list, tuple)):
            continue
        if len(px) < 4:
            continue
        try:
            values = tuple(float(value) for value in px[:4])
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in values):
            continue
        placements[index] = dict(placement, px=values)

    rendered = []
    for index, source in enumerate(words):
        word = dict(source)
        word.pop("_box_index", None)
        placement = placements.get(index)
        if placement is not None and width > 0 and height > 0:
            x0, y0, x1, y1 = placement["px"]
            left, right = sorted((x0, x1))
            top, bottom = sorted((y0, y1))
            word["bbox"] = [
                left / width * 1000.0,
                (1.0 - top / height) * 1000.0,
                right / width * 1000.0,
                (1.0 - bottom / height) * 1000.0,
            ]
            word["text"] = str(placement.get("text") or word.get("text") or "")
        rendered.append(word)
    return rendered


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
    # Same recipe as glyph_review receipt mode: an unpinned bitmap_thin is
    # DERIVED, not defaulted, or density comparisons run at the wrong weight.
    if "bitmap_font" in typ and "bitmap_thin" not in typ:
        typ["bitmap_thin"] = rsr.resolve_bitmap_thin(
            table,
            merchant,
            region=region,
            atlas=atlas,
            profile=prof,
            section_scale=ss,
            typography=typ,
        )

    d = c.get_receipt_details(image_id, receipt_id)
    rec = d.receipt
    ws = [w for w in d.words if w.receipt_id == receipt_id]
    lbl = {
        (l.line_id, l.word_id): l.label
        for l in d.labels
        if l.receipt_id == receipt_id and l.label not in (None, "O")
        # INVALID labels are not on the printed receipt (same policy as
        # _real_receipt_dict / glyph_review after fix/render-systemic).
        and str(getattr(l, "validation_status", "") or "").upper() != "INVALID"
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
            "_box_index": index,
        }
        for index, w in enumerate(ws)
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
    box_sink: list[dict] = []
    rsr._render_cached_hybrid(
        {"words": words, "barcodes": barcodes, "merchant_name": merchant},
        atlas,
        profile=prof,
        width=wt,
        height=ht,
        path=tmp,
        section_scale=ss,
        box_sink=box_sink,
        **typ,
    )
    syn = Image.open(tmp).convert("RGB")
    rendered_words = _render_true_words(
        words, box_sink, width=wt, height=ht
    )
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
    return real, syn, len(barcodes), rendered_words


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


# Department-header pattern per slug, for the bold/underline band checks.
HEADER_ROW_RE = {
    "gelsons": r"^(GROCERY|PRODUCE|MEAT|SEAFOOD|REG DELI|SERVICE DELI|DELI|"
    r"BAKERY|DAIRY|FROZEN|LIQUOR|FLORAL|SUSHI)$",
}


def band_checks(real, syn, words, slug):
    """Measured per-band checks: header bold/underline + price column x.

    Uses the real OCR word geometry (shared canvas) to locate department
    header rows and the item price column, then measures BOTH images at the
    same coordinates: ink density ratio (bold), a below-baseline dark-row
    scan (underline), and the median right edge of price tokens (column x).
    Prints a JSON report; nonzero exit is left to the caller's judgement.
    """
    import json as _json
    import re as _re

    import numpy as np

    W, H = syn.size
    ra = np.asarray(real.convert("L"), dtype=np.uint8)
    sa = np.asarray(syn.convert("L"), dtype=np.uint8)
    hdr_re = HEADER_ROW_RE.get(slug)
    lines = {}
    for w in words:
        lines.setdefault(w["line_id"], []).append(w)

    def _geom(ws):
        x0 = min(min(w["bbox"][0], w["bbox"][2]) for w in ws) / 1000 * W
        x1 = max(max(w["bbox"][0], w["bbox"][2]) for w in ws) / 1000 * W
        yt = (1 - max(max(w["bbox"][1], w["bbox"][3]) for w in ws) / 1000) * H
        yb = (1 - min(min(w["bbox"][1], w["bbox"][3]) for w in ws) / 1000) * H
        return x0, x1, yt, yb

    def _density(arr, x0, x1, yt, yb):
        crop = arr[int(yt) : int(yb), int(x0) : int(x1)]
        return float((crop < 128).mean()) if crop.size else 0.0

    def _underlined(arr, x0, x1, yb, h):
        band = arr[int(yb - 1) : int(yb + 0.30 * h), int(x0) : int(x1)]
        return bool(band.size and max((r < 150).mean() for r in band) > 0.28)

    report = {"headers": [], "price_column": None}
    body_density_r, body_density_s = [], []
    price_right_r = []
    for lid, ws in sorted(lines.items()):
        text = " ".join(
            str(w["text"]) for w in sorted(ws, key=lambda w: w["word_id"])
        ).strip()
        x0, x1, yt, yb = _geom(ws)
        h = yb - yt
        if h <= 4:
            continue
        if hdr_re and _re.match(hdr_re, text):
            report["headers"].append(
                {
                    "text": text,
                    "real": {
                        "density": round(_density(ra, x0, x1, yt, yb), 3),
                        "underlined": _underlined(ra, x0, x1, yb, h),
                    },
                    "synth": {
                        "density": round(_density(sa, x0, x1, yt, yb), 3),
                        "underlined": _underlined(sa, x0, x1, yb, h),
                    },
                }
            )
        elif any(ch.isalpha() for ch in text):
            body_density_r.append(_density(ra, x0, x1, yt, yb))
            body_density_s.append(_density(sa, x0, x1, yt, yb))
        t = text.replace(" ", "")
        if (
            t.replace(".", "").isdigit()
            and "." in t
            and len(t) <= 6
            and x1 > 0.5 * W
        ):
            price_right_r.append((x1, yt, yb))
    if price_right_r:
        from statistics import median as _med

        # Compare LIKE with LIKE: rightmost ink per price row in BOTH images
        # (both include any trailing tax flag), so the delta isolates column
        # placement rather than token-vs-flag differences.
        rx, sx = [], []
        for x1, yt, yb in price_right_r:
            # constrain to the right half so unrelated left-column ink can
            # never win the "rightmost pixel" scan
            xoff = W // 2
            for arr, acc in ((ra, rx), (sa, sx)):
                rows = arr[int(yt) : int(yb), xoff:]
                cols = np.nonzero((rows < 128).any(axis=0))[0]
                if cols.size:
                    acc.append(float(cols.max() + xoff))
        report["price_column"] = {
            "real_right_x_med": round(_med(rx), 1) if rx else None,
            "synth_right_x_med": round(_med(sx), 1) if sx else None,
            "delta_px": (round(_med(sx) - _med(rx), 1) if rx and sx else None),
        }
    if body_density_r and body_density_s:
        from statistics import median as _med

        report["body_density"] = {
            "real_med": round(_med(body_density_r), 3),
            "synth_med": round(_med(body_density_s), 3),
        }
    print("CHECKS " + _json.dumps(report))
    return report


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
    real, syn, n_bar, words = render_pair(merchant, image_id, receipt_id)
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
    if real is not None:
        band_checks(real, syn, words, slug)


if __name__ == "__main__":
    main()
