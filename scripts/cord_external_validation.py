"""CORD-v2 external validation of the band-block decoder.

Usage: python scripts/cord_external_validation.py
Needs: pip install datasets (network access to HuggingFace).
Not a CI gate (network dependency); run manually when decoder
structure changes. Baseline 2026-07-30: receipts=100 truth_items=246
recall=0.797 names=0.765 precision=0.980 -- pure structural fallback
(zero template priors apply to CORD).

CORD receipts are Indonesian (dot-thousands prices, no decimals). The
decoder's amount lexer is US-format by design, so the harness applies a
narrow, documented shim: standalone dot/comma-thousands numerals become
"NNNN.00" in both OCR words and ground truth. What this validates is the
STRUCTURE (banding, role fallback, META absorption, name assignment) on
a receipt population the priors have never seen -- every template lookup
misses, so the decoder runs on pure structural fallback.
"""

import json
import re
import sys

from datasets import load_dataset

from receipt_upload.line_items.geometry import extract_items

NUM = re.compile(r"^(\d{1,3}(?:[.,]\d{3})+)$")


def shim(t):
    m = NUM.match(t.strip())
    if m:
        return re.sub(r"[.,]", "", m.group(1)) + ".00"
    return t


def norm_price(p):
    if p is None:
        return None
    t = re.sub(r"[^\d.,]", "", str(p))
    m = NUM.match(t)
    if m:
        return re.sub(r"[.,]", "", m.group(1)) + ".00"
    try:
        return f"{float(t.replace(',','')):.2f}"
    except Exception:
        return None


def norm_name(s):
    return re.sub(
        r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", (s or "").upper())
    ).strip()


ds = load_dataset("naver-clova-ix/cord-v2", split="test", streaming=True)
tot_truth = tot_matched = tot_name = tot_pred = 0
n_rec = 0
for ex in ds:
    gt = json.loads(ex["ground_truth"])
    meta = gt["meta"]["image_size"]
    W, H = meta["width"], meta["height"]
    menu = gt["gt_parse"].get("menu") or []
    if isinstance(menu, dict):
        menu = [menu]
    truth = []
    for m in menu:
        p = norm_price(m.get("price") or m.get("itemsubtotal"))
        if p is not None:
            truth.append({"name": m.get("nm") or "", "price": p})
    if not truth:
        continue
    words, lids = [], set()
    for li, line in enumerate(gt["valid_line"], start=1):
        cat = line.get("category") or ""
        if not cat.startswith("menu."):
            continue
        lids.add(li)
        for wi, w in enumerate(line["words"], start=1):
            q = w["quad"]
            x = min(q["x1"], q["x4"]) / W
            y_top = min(q["y1"], q["y2"]) / H
            y_bot = max(q["y3"], q["y4"]) / H
            words.append(
                {
                    "line_id": li,
                    "word_id": wi,
                    "text": shim(w["text"]),
                    "x": x,
                    "y_mid": 1.0 - (y_top + y_bot) / 2,
                    "h": (y_bot - y_top),
                }
            )
    if not words:
        continue
    items, _ = extract_items(words, lids)
    pred = [i for i in items if not i.get("is_discount")]
    used = set()
    matched = name_ok = 0
    for t in truth:
        best = None
        for j, p in enumerate(pred):
            if j in used:
                continue
            if f"{p['price']:.2f}" == t["price"]:
                ta, pa = set(norm_name(t["name"]).split()), set(
                    norm_name(p.get("name")).split()
                )
                score = len(ta & pa)
                if best is None or score > best[1]:
                    best = (j, score)
        if best is not None:
            used.add(best[0])
            matched += 1
            if norm_name(t["name"]) == norm_name(pred[best[0]].get("name")):
                name_ok += 1
    tot_truth += len(truth)
    tot_matched += matched
    tot_name += name_ok
    tot_pred += len(pred)
    n_rec += 1
print(f"receipts={n_rec} truth_items={tot_truth}")
print(
    f"recall={tot_matched/tot_truth:.3f} names={tot_name/max(1,tot_matched):.3f} precision={tot_matched/max(1,tot_pred):.3f}"
)
