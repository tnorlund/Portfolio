#!/usr/bin/env python3
"""glyph_prototype_dynamo.py -- per-zone canonical glyphs from ALL a merchant's receipts.

Scales :mod:`glyph_prototype` from one receipt to every real receipt of a merchant,
using the OCR's actual per-LETTER boxes (ReceiptLetter) and the S3 receipt crops --
so even the sparse zones (headings/totals/footer) accumulate enough samples per
character for a confident prototype + font match.

Pipeline per receipt: get_receipt_places_by_merchant -> get_image_details
(receipt_letters) -> load_raw_image_from_s3 -> crop each letter -> Sauvola tight
crop -> normalize. Aggregate per character; split into BODY vs EMPHASIS by letter
height relative to the per-receipt median; prototype + font-match each zone.

Usage: glyph_prototype_dynamo.py <merchant_name> <out_dir> [max_receipts]
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from glyph_prototype import (  # noqa: E402
    BOX, GH, CHARS, FONTS, _normalize, _font_glyph, _sim,
)

TABLE = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
REGION = os.environ.get("AWS_REGION", "us-east-1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from glyph_segment import sauvola_mask  # noqa: E402


def _collect(merchant, max_receipts):
    from receipt_dynamo.data.dynamo_client import DynamoClient
    from receipt_upload.font_analysis import load_raw_image_from_s3

    client = DynamoClient(table_name=TABLE, region=REGION)
    places, _ = client.get_receipt_places_by_merchant(merchant)
    targets = [(str(p.image_id), int(p.receipt_id)) for p in places]
    if max_receipts:
        targets = targets[:max_receipts]
    print(f"{merchant}: {len(targets)} receipts to pull", flush=True)

    # samples[zone][char] -> list of normalized glyph arrays
    samples = {"body": defaultdict(list), "emph": defaultdict(list)}
    used = 0
    for k, (iid, rid) in enumerate(targets):
        try:
            details = client.get_image_details(iid)
            letters = [l for l in details.receipt_letters if l.receipt_id == rid]
            receipt = next((c for c in details.receipts if c.receipt_id == rid), None)
            if receipt is None or not letters:
                continue
            raw = load_raw_image_from_s3(receipt).convert("L")
        except Exception as e:  # noqa: BLE001
            continue
        W, H = raw.size
        arr = np.asarray(raw)
        # per-receipt median letter height for zone split
        hs = []
        boxed = []
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
            crop = arr[t:b, l:r]
            mask = sauvola_mask(crop)
            norm = _normalize(mask)
            if norm is None:
                continue
            ratio = (b - t) / med
            zone = "emph" if ratio >= 1.3 else ("body" if 0.82 <= ratio <= 1.22 else None)
            if zone:
                samples[zone][ch].append(norm)
        used += 1
        if used % 5 == 0:
            print(f"  ...{used} receipts processed", flush=True)
    print(f"used {used} receipts", flush=True)
    return samples


def _rank_and_sheet(samples_zone, label, out_dir):
    protos = {ch: np.mean(s, axis=0) for ch, s in samples_zone.items() if len(s) >= 3}
    if not protos:
        print(f"[{label}] too sparse ({sum(len(s) for s in samples_zone.values())} glyphs)")
        return None
    print(f"[{label}] {sum(len(s) for s in samples_zone.values())} glyphs, "
          f"{len(protos)} chars")
    ranking = []
    for name, path in FONTS.items():
        if not os.path.exists(path):
            continue
        font = ImageFont.truetype(path, GH)
        sims = [_sim(p, _font_glyph(font, ch)) for ch, p in protos.items()
                if _font_glyph(font, ch) is not None]
        if sims:
            ranking.append((name, float(np.mean(sims))))
    ranking.sort(key=lambda kv: -kv[1])
    print(f"[{label}] font match:", "  ".join(f"{n}={s:.3f}" for n, s in ranking))
    best = ranking[0][0] if ranking else None
    cols = [c for c in CHARS if c in protos]
    sheet = Image.new("RGB", (len(cols) * BOX, BOX * 2 + 16), (235, 235, 235))
    bf = ImageFont.truetype(FONTS[best], GH) if best else None
    for i, ch in enumerate(cols):
        sheet.paste(Image.fromarray((protos[ch] * 255).astype(np.uint8)).convert("RGB"),
                    (i * BOX, 0))
        if bf is not None:
            sheet.paste(Image.fromarray((_font_glyph(bf, ch) * 255).astype(np.uint8)).convert("RGB"),
                        (i * BOX, BOX))
    ImageDraw.Draw(sheet).text((2, BOX * 2 + 2),
                               f"{label}: top=prototype  bottom={best}", fill=(0, 0, 0))
    out = os.path.join(out_dir, f"prototypes_{label}.png")
    sheet.save(out)
    print(f"[{label}] sheet -> {out}")
    return best, ranking


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    merchant = sys.argv[1]
    out_dir = sys.argv[2]
    max_receipts = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    os.makedirs(out_dir, exist_ok=True)
    samples = _collect(merchant, max_receipts)
    print(f"=== {merchant} per-zone prototypes ===")
    _rank_and_sheet(samples["body"], "body", out_dir)
    _rank_and_sheet(samples["emph"], "emphasis", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
