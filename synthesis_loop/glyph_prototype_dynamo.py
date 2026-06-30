#!/usr/bin/env python3
"""glyph_prototype_dynamo.py -- per-zone canonical glyphs from ALL a merchant's receipts.

Scales glyph prototyping to every real receipt of a merchant via ReceiptLetter
boxes + S3 crops, with three refinements:

* reverse-video polarity auto-detection (black-box amounts inverted before binarizing),
* emphasis SUB-clustering by style (aspect x weight) so display headings and bold
  totals are prototyped apart instead of averaged into mud,
* emboldened (double-strike) font candidates, since printers emphasize by
  double-striking the SAME font rather than switching typeface.

Usage: glyph_prototype_dynamo.py <merchant_name> <out_dir> [max_receipts]
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from glyph_prototype import BOX, GH, CHARS, _normalize, _font_glyph, _sim, font_candidates  # noqa: E402
from glyph_segment import sauvola_mask, auto_polarity  # noqa: E402
from section_cluster import _kmeans  # noqa: E402

TABLE = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
REGION = os.environ.get("AWS_REGION", "us-east-1")


def _glyph_features(mask):
    ys, xs = np.where(mask)
    if ys.size < 4:
        return None
    h = ys.max() - ys.min() + 1
    w = xs.max() - xs.min() + 1
    return (w / h, mask.sum() / float(w * h))  # aspect, density


def _collect(merchant, max_receipts):
    from receipt_dynamo.data.dynamo_client import DynamoClient
    from receipt_upload.font_analysis import load_raw_image_from_s3

    client = DynamoClient(table_name=TABLE, region=REGION)
    places, _ = client.get_receipt_places_by_merchant(merchant)
    targets = [(str(p.image_id), int(p.receipt_id)) for p in places]
    if max_receipts:
        targets = targets[:max_receipts]
    print(f"{merchant}: {len(targets)} receipts", flush=True)

    body = defaultdict(list)         # char -> [norm]
    emph = []                        # [{ch, norm, aspect, density}]
    rev_count = 0
    used = 0
    for iid, rid in targets:
        try:
            details = client.get_image_details(iid)
            letters = [l for l in details.receipt_letters if l.receipt_id == rid]
            receipt = next((c for c in details.receipts if c.receipt_id == rid), None)
            if receipt is None or not letters:
                continue
            raw = load_raw_image_from_s3(receipt).convert("L")
        except Exception:  # noqa: BLE001
            continue
        W, H = raw.size
        arr = np.asarray(raw)
        boxed, hs = [], []
        for lt in letters:
            ch = (lt.text or "").strip().upper()
            if ch not in CHARS:
                continue
            tl, br = lt.top_left, lt.bottom_right
            x0, x1 = tl["x"] * W, br["x"] * W
            y0, y1 = (1 - tl["y"]) * H, (1 - br["y"]) * H
            l, t, r, b = int(min(x0, x1)), int(min(y0, y1)), int(max(x0, x1)), int(max(y0, y1))
            if r - l < 2 or b - t < 6:
                continue
            boxed.append((ch, l, t, r, b))
            hs.append(b - t)
        if not hs:
            continue
        med = float(np.median(hs))
        for ch, l, t, r, b in boxed:
            crop, was_rev = auto_polarity(arr[t:b, l:r])
            rev_count += was_rev
            mask = sauvola_mask(crop)
            norm = _normalize(mask)
            if norm is None:
                continue
            ratio = (b - t) / med
            if 0.82 <= ratio <= 1.22:
                body[ch].append(norm)
            elif ratio >= 1.3:
                feat = _glyph_features(mask)
                if feat:
                    emph.append({"ch": ch, "norm": norm, "aspect": feat[0], "density": feat[1]})
        used += 1
        if used % 10 == 0:
            print(f"  ...{used} used", flush=True)
    print(f"used {used} receipts  reverse-video letters={rev_count}", flush=True)
    return body, emph


def _match(protos):
    ranking = []
    for label, font, stroke in font_candidates():
        sims = [_sim(p, _font_glyph(font, ch, stroke)) for ch, p in protos.items()
                if _font_glyph(font, ch, stroke) is not None]
        if sims:
            ranking.append((label, float(np.mean(sims))))
    ranking.sort(key=lambda kv: -kv[1])
    return ranking


def _sheet(protos, best_font, best_stroke, path, title):
    cols = [c for c in CHARS if c in protos]
    sheet = Image.new("RGB", (len(cols) * BOX, BOX * 2 + 16), (235, 235, 235))
    for i, ch in enumerate(cols):
        sheet.paste(Image.fromarray((protos[ch] * 255).astype(np.uint8)).convert("RGB"), (i * BOX, 0))
        fg = _font_glyph(best_font, ch, best_stroke)
        if fg is not None:
            sheet.paste(Image.fromarray((fg * 255).astype(np.uint8)).convert("RGB"), (i * BOX, BOX))
    ImageDraw.Draw(sheet).text((2, BOX * 2 + 2), title, fill=(0, 0, 0))
    sheet.save(path)


def _report_zone(protos, label, out_dir):
    protos = {ch: v for ch, v in protos.items() if v is not None}
    if len(protos) < 5:
        print(f"[{label}] too sparse ({len(protos)} chars)")
        return
    ranking = _match(protos)
    print(f"[{label}] {len(protos)} chars  -> " + "  ".join(f"{n}={s:.3f}" for n, s in ranking[:4]))
    best = ranking[0][0]
    name, stroke = (best[:-5], 1) if best.endswith("+bold") else (best, 0)
    from glyph_prototype import _all_font_paths
    from PIL import ImageFont
    bf = ImageFont.truetype(_all_font_paths()[name], GH)
    _sheet(protos, bf, stroke, os.path.join(out_dir, f"proto_{label}.png"),
           f"{label}: top=prototype bottom={best}")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    merchant, out_dir = sys.argv[1], sys.argv[2]
    max_receipts = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    os.makedirs(out_dir, exist_ok=True)
    body, emph = _collect(merchant, max_receipts)

    print(f"=== {merchant} ===")
    body_protos = {ch: np.mean(s, axis=0) for ch, s in body.items() if len(s) >= 3}
    np.savez_compressed(os.path.join(out_dir, "body_protos.npz"), **body_protos)
    print(f"cached body prototypes -> {out_dir}/body_protos.npz")
    _report_zone(body_protos, "body", out_dir)

    # sub-cluster emphasis by style (aspect x density), prototype each sub-style
    if len(emph) >= 30:
        X = np.array([[g["aspect"], g["density"]] for g in emph])
        Xn = (X - X.mean(0)) / (X.std(0) + 1e-6)
        lab, cen = _kmeans(Xn, 2)
        for c in range(len(cen)):
            sub = defaultdict(list)
            for i, g in enumerate(emph):
                if lab[i] == c:
                    sub[g["ch"]].append(g["norm"])
            protos = {ch: np.mean(s, axis=0) for ch, s in sub.items() if len(s) >= 3}
            n = sum(len(s) for s in sub.values())
            asp, den = X[lab == c].mean(0)
            print(f"-- emphasis sub-style {c}: {n} glyphs, aspect~{asp:.2f} density~{den:.2f}")
            _report_zone(protos, f"emph{c}", out_dir)
    else:
        print(f"emphasis too sparse ({len(emph)} glyphs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
