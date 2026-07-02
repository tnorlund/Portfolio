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

import json
import os
import sys
import time

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

    d = None
    rec = None
    for _ in range(3):
        d = c.get_image_details(image_id)
        rec = next(
            (r for r in d.receipts if str(r.receipt_id) == str(receipt_id)),
            None,
        )
        if rec is not None:
            break
        time.sleep(0.5)
    if d is None or rec is None:
        found = [] if d is None else [r.receipt_id for r in d.receipts]
        raise RuntimeError(
            f"receipt {receipt_id} not found for image {image_id}; found {found}"
        )
    ws = [w for w in d.receipt_words if w.receipt_id == receipt_id]
    lbl = {(l.line_id, l.word_id): l.label
           for l in d.receipt_word_labels if l.receipt_id == receipt_id}
    words = [{
        "text": w.text,
        "line_id": w.line_id,
        "word_id": w.word_id,
        "bbox": [w.top_left["x"] * 1000, w.top_left["y"] * 1000,
                 w.bottom_right["x"] * 1000, w.bottom_right["y"] * 1000],
        "labels": ([lbl[(w.line_id, w.word_id)]]
                   if lbl.get((w.line_id, w.word_id)) not in (None, "O") else []),
    } for w in ws]
    barcodes = []
    if hasattr(c, "list_receipt_barcodes_from_receipt"):
        try:
            for b in c.list_receipt_barcodes_from_receipt(image_id, receipt_id):
                barcodes.append({
                    "text": getattr(b, "text", "") or "",
                    "symbology": getattr(b, "symbology", ""),
                    "top_left": getattr(b, "top_left", None),
                    "bottom_right": getattr(b, "bottom_right", None),
                    "confidence": getattr(b, "confidence", None),
                })
        except Exception:  # noqa: BLE001
            barcodes = []
    wt = 760
    ht = int(round(wt * rec.height / rec.width))
    tmp = out_png + ".syn.png"
    rsr._render_cached_hybrid({"words": words, "barcodes": barcodes,
                               "merchant_name": merchant}, atlas,
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
    _save_zoom_crops(out_png)
    if real is not None:
        real_for_metrics = real.resize((wt, ht))
        _print_metric_summary(real_for_metrics, syn, words)
        _save_line_scorecard(real_for_metrics, syn, words, out_png)
    print(f"{merchant} {image_id}:{receipt_id} -> {out_png}")
    return 0


def _save_line_scorecard(real, syn, words, out_png: str) -> None:
    from receipt_line_scorecard import score_receipt_images, _write_markdown

    report = score_receipt_images(real, syn, words)
    stem = os.path.splitext(out_png)[0]
    json_path = f"{stem}.scorecard.json"
    md_path = f"{stem}.scorecard.md"
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")
    _write_markdown(report, md_path)
    counts = report["summary"]["severity_counts"]
    print(
        "scorecard "
        f"blockers={counts.get('BLOCKER', 0)} minors={counts.get('MINOR', 0)} "
        f"-> {json_path} {md_path}"
    )


def _metric_box(
    bbox, width: int, height: int, *, pad: bool = True
) -> tuple[int, int, int, int] | None:
    try:
        x0, y0, x1, y1 = (float(v) for v in bbox[:4])
    except (TypeError, ValueError):
        return None
    left = int(round(min(x0, x1) / 1000.0 * width))
    right = int(round(max(x0, x1) / 1000.0 * width))
    top = int(round((1.0 - max(y0, y1) / 1000.0) * height))
    bottom = int(round((1.0 - min(y0, y1) / 1000.0) * height))
    if right <= left or bottom <= top:
        return None
    if not pad:
        return (max(0, left), max(0, top), min(width, right), min(height, bottom))
    pad_x = max(2, int(round((right - left) * 0.12)))
    pad_y = max(2, int(round((bottom - top) * 0.20)))
    return (
        max(0, left - pad_x),
        max(0, top - pad_y),
        min(width, right + pad_x),
        min(height, bottom + pad_y),
    )


def _ink_metrics(image, words) -> dict[str, float]:
    heights: list[float] = []
    widths: list[float] = []
    densities: list[float] = []
    gray_image = image.convert("L")
    width, height = gray_image.size
    for word in words:
        text = str(word.get("text") or "").strip()
        glyphs = text.replace(" ", "")
        if len(glyphs) < 2 or not any(ch.isalnum() for ch in glyphs):
            continue
        digits = sum(ch.isdigit() for ch in glyphs)
        if digits >= 14 and digits >= 0.8 * len(glyphs):
            continue
        box = _metric_box(word.get("bbox") or (), width, height)
        if box is None:
            continue
        crop = gray_image.crop(box)
        arr = np.asarray(crop)
        paper = float(np.median(arr))
        threshold = max(0.0, min(230.0, paper - 22.0))
        ink = arr < threshold
        if int(ink.sum()) < 8:
            continue
        ys, xs = np.where(ink)
        heights.append(float(ys.max() - ys.min() + 1))
        widths.append(float(xs.max() - xs.min() + 1) / len(glyphs))
        densities.append(float(ink.mean()))
    if not heights:
        return {}
    return {
        "h_med": float(np.median(heights)),
        "wpc_med": float(np.median(widths)),
        "density_med": float(np.median(densities)),
    }


def _ocr_pitch_metrics(words, width: int, height: int) -> dict[str, float]:
    by_line: dict[object, list[dict]] = {}
    for word in words:
        line_id = word.get("line_id")
        if line_id is None:
            continue
        by_line.setdefault(line_id, []).append(word)
    pitches: list[float] = []
    for row in by_line.values():
        placed = []
        for word in row:
            box = _metric_box(word.get("bbox") or (), width, height, pad=False)
            text = str(word.get("text") or "").strip()
            if box is None or not text:
                continue
            placed.append((box[0], text))
        placed.sort()
        for (left_a, text_a), (left_b, _text_b) in zip(placed, placed[1:]):
            cells = len(text_a.replace(" ", "")) + 1
            if cells < 3:
                continue
            pitch = (left_b - left_a) / cells
            if 8.0 <= pitch <= 26.0:
                pitches.append(float(pitch))
    if not pitches:
        return {}
    return {"pitch_med": float(np.median(pitches)), "pitch_n": float(len(pitches))}


def _print_metric_summary(real, syn, words) -> None:
    real_m = _ink_metrics(real, words)
    syn_m = _ink_metrics(syn, words)
    pitch = _ocr_pitch_metrics(words, syn.width, syn.height)
    if not real_m or not syn_m:
        return
    h_ratio = syn_m["h_med"] / max(1.0, real_m["h_med"])
    d_ratio = syn_m["density_med"] / max(1e-6, real_m["density_med"])
    print(
        "metrics "
        f"ocr_pitch_med={pitch.get('pitch_med', 0.0):.2f} "
        f"real_h_med={real_m['h_med']:.2f} synth_h_med={syn_m['h_med']:.2f} "
        f"h_ratio={h_ratio:.3f} "
        f"real_wpc_med={real_m['wpc_med']:.2f} synth_wpc_med={syn_m['wpc_med']:.2f} "
        f"real_density={real_m['density_med']:.3f} "
        f"synth_density={syn_m['density_med']:.3f} density_ratio={d_ratio:.3f}"
    )


def _save_zoom_crops(out_png: str) -> None:
    im = Image.open(out_png).convert("RGB")
    top = 34
    body_h = max(1, im.height - top)
    regions = {
        "header": (0, top, im.width, top + int(body_h * 0.22)),
        "body": (
            0,
            top + int(body_h * 0.38),
            im.width,
            top + int(body_h * 0.58),
        ),
        "codes": (
            0,
            top + int(body_h * 0.62),
            im.width,
            top + int(body_h * 0.82),
        ),
        "footer": (
            0,
            top + int(body_h * 0.78),
            im.width,
            im.height,
        ),
    }
    stem = os.path.splitext(out_png)[0]
    for name, box in regions.items():
        crop = im.crop(box)
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.NEAREST)
        crop.save(f"{stem}.zoom_{name}.png")


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
