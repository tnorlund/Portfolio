#!/usr/bin/env python3
"""logo_prototype.py -- canonical merchant logo averaged across ALL its receipts.

Same idea as the glyph prototypes, applied to the LOGO: every single receipt's
printed logo is thermal-degraded (the smear), but averaging many aligned crops
cancels the noise -> a clean canonical wordmark. Locates the logo via the
MERCHANT_NAME words (+ the bars region above the first body line), normalizes each
crop to a common canvas, and averages.

Usage: logo_prototype.py <Merchant> <out_dir> [max_receipts]
Output: <out_dir>/logo_mean.png (canonical), logo_samples.png (variation across receipts).
"""
from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image

TABLE = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
REGION = os.environ.get("AWS_REGION", "us-east-1")
CW, CH = 460, 116   # canonical logo canvas (wide wordmark)


def _otsu_ink(a):
    a = a.astype(np.float64)
    hist, _ = np.histogram(a, 256, (0, 255))
    tot = a.size
    sm = (np.arange(256) * hist).sum()
    wB = sB = 0.0
    best_t, best = 128, -1.0
    for t in range(256):
        wB += hist[t]
        if wB == 0:
            continue
        wF = tot - wB
        if wF == 0:
            break
        sB += t * hist[t]
        var = wB * wF * ((sB / wB) - ((sm - sB) / wF)) ** 2
        if var > best:
            best, best_t = var, t
    return a < best_t


def _logo_crop(arr, words):
    """Crop tightly to the MERCHANT_NAME wordmark only (COSTCO + bars + WHOLESALE),
    excluding the address below."""
    H, W = arr.shape
    mn = [w for w in words if "MERCHANT_NAME" in (w.get("labels") or [])
          or str(w.get("text", "")).strip().upper() in ("COSTCO", "WHOLESALE")]
    if not mn:
        return None
    tops, bottoms, lefts, rights = [], [], [], []
    for w in mn:
        tl, br = w["top_left"], w["bottom_right"]
        tops.append((1 - tl["y"]) * H)
        bottoms.append((1 - br["y"]) * H)
        lefts.append(tl["x"] * W)
        rights.append(br["x"] * W)
    y0 = int(max(0, min(tops) - 0.02 * H))
    y1 = int(min(H, max(bottoms) + 0.02 * H))   # stop at the wordmark, not address
    x0 = int(max(0, min(lefts) - 0.03 * W))
    x1 = int(min(W, max(rights) + 0.03 * W))
    crop = arr[y0:y1, x0:x1]
    return crop if crop.shape[0] >= 20 and crop.shape[1] >= 60 else None


def _norm(crop):
    """Binarize, TIGHT-CROP to the logo ink bbox (alignment), resize to canvas."""
    ink = _otsu_ink(crop)
    ys, xs = np.where(ink)
    if ys.size < 80:
        return None
    tight = ink[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.uint8) * 255
    return np.asarray(Image.fromarray(tight).resize((CW, CH))) / 255.0


def main() -> int:
    merchant, out_dir = sys.argv[1], sys.argv[2]
    maxr = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    os.makedirs(out_dir, exist_ok=True)
    from receipt_dynamo.data.dynamo_client import DynamoClient
    from receipt_upload.font_analysis import load_raw_image_from_s3

    client = DynamoClient(table_name=TABLE, region=REGION)
    places, _ = client.get_receipt_places_by_merchant(merchant)
    targets = [(str(p.image_id), int(p.receipt_id)) for p in places]
    if maxr:
        targets = targets[:maxr]
    print(f"{merchant}: {len(targets)} receipts", flush=True)

    norms, samples = [], []
    used = 0
    for iid, rid in targets:
        try:
            d = client.get_image_details(iid)
            words = [{"text": w.text, "labels": [], "top_left": w.top_left,
                      "bottom_right": w.bottom_right}
                     for w in d.receipt_words if w.receipt_id == rid]
            # attach MERCHANT_NAME from labels
            lab = {(l.line_id, l.word_id): l.label for l in d.receipt_word_labels
                   if l.receipt_id == rid}
            for w, ww in zip(words, [w for w in d.receipt_words if w.receipt_id == rid]):
                key = (ww.line_id, ww.word_id)
                if lab.get(key):
                    w["labels"] = [lab[key]]
            rec = next((c for c in d.receipts if c.receipt_id == rid), None)
            if rec is None:
                continue
            raw = load_raw_image_from_s3(rec).convert("L")
        except Exception:
            continue
        crop = _logo_crop(np.asarray(raw), words)
        if crop is None:
            continue
        n = _norm(crop)
        norms.append(n)
        if len(samples) < 8:
            samples.append(n)
        used += 1
        if used % 10 == 0:
            print(f"  ...{used}", flush=True)
    print(f"used {used} logos", flush=True)
    if not norms:
        print("no logos found")
        return 1

    mean = np.mean(norms, axis=0)
    Image.fromarray(((1 - mean) * 255).astype(np.uint8)).save(os.path.join(out_dir, "logo_mean.png"))
    # a crisp version (threshold the mean)
    Image.fromarray(((mean < 0.5) * 255).astype(np.uint8)).save(os.path.join(out_dir, "logo_mean_crisp.png"))
    # variation sheet
    sh = Image.new("L", (CW, (CH + 6) * len(samples)), 255)
    for i, s in enumerate(samples):
        sh.paste(Image.fromarray(((1 - s) * 255).astype(np.uint8)), (0, i * (CH + 6)))
    sh.save(os.path.join(out_dir, "logo_samples.png"))
    print(f"mean -> {out_dir}/logo_mean.png  (from {used} receipts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
