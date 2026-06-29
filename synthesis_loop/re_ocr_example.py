#!/usr/bin/env python3
"""re_ocr_example.py — SPIKE for the re-OCR training-data pathway.

Takes a synthesized candidate (ground-truth tokens/bboxes/BIO labels) and a HYBRID render of it,
re-OCRs the render with Apple Vision (the production engine), and PROPAGATES the GT labels onto the
re-OCR'd tokens. Because we rendered the image FROM the GT, each OCR word sits almost exactly where
we drew its GT token, so spatial IoU matching is near-exact; the OCR text is allowed to be noisy
(that realistic OCR noise is the entire point). Emits a re-OCR'd training example + a report, and
scores it with the objective verifier.

Usage: re_ocr_example.py <bundle.json> <hybrid.png> <out_dir> [candidate_index]
"""
from __future__ import annotations
import json, os, sys, glob

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from receipt_upload.ocr import apple_vision_ocr  # noqa: E402
import verify_candidates as V  # noqa: E402

# Render frame of _render_cached_hybrid (RenderConfig width=460,height=1100,margin=10; coord_max=1000)
W, H, MARGIN, COORD = 460, 1100, 10, 1000.0
INNER_W, INNER_H = W - 2 * MARGIN, H - 2 * MARGIN


def gt_to_px(bbox):
    """GT bbox [x0,y0,x1,y1] in 0..1000, y-high-is-top -> pixel box (left,top,right,bottom), origin top-left."""
    x0, y0, x1, y1 = bbox
    left = MARGIN + (min(x0, x1) / COORD) * INNER_W
    right = MARGIN + (max(x0, x1) / COORD) * INNER_W
    top = MARGIN + ((COORD - max(y0, y1)) / COORD) * INNER_H
    bottom = MARGIN + ((COORD - min(y0, y1)) / COORD) * INNER_H
    return [left, top, right, bottom]


def ocr_to_px(bb):
    """Apple Vision bbox {x,y,width,height} normalized [0,1], origin BOTTOM-left -> pixel box top-left."""
    x, y, w, h = bb["x"], bb["y"], bb["width"], bb["height"]
    left, right = x * W, (x + w) * W
    top = (1.0 - (y + h)) * H
    bottom = (1.0 - y) * H
    return [left, top, right, bottom]


def px_to_gt(px):
    """Invert gt_to_px: pixel box -> GT 0..1000 y-high-is-top, for verifier compatibility."""
    left, top, right, bottom = px
    x0 = (left - MARGIN) / INNER_W * COORD
    x1 = (right - MARGIN) / INNER_W * COORD
    y_top = COORD - (top - MARGIN) / INNER_H * COORD
    y_bot = COORD - (bottom - MARGIN) / INNER_H * COORD
    return [round(x0, 1), round(min(y_top, y_bot), 1), round(x1, 1), round(max(y_top, y_bot), 1)]


def iou(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / ua if ua > 0 else 0.0


def entity(tag):
    return tag[2:] if tag and tag[:2] in ("B-", "I-") else (None if tag in (None, "O", "") else tag)


def main():
    bundle_p, hybrid_png, out_dir = sys.argv[1:4]
    ci = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    os.makedirs(out_dir, exist_ok=True)

    ex = json.load(open(bundle_p))["synthetic_training_examples"][ci]
    cid = ex.get("candidate_id", f"candidate-{ci}")
    gt_tokens, gt_bboxes, gt_tags = ex["tokens"], ex["bboxes"], ex["ner_tags"]
    gt_px = [gt_to_px(b) for b in gt_bboxes]

    # Re-OCR the render
    res = list(apple_vision_ocr([hybrid_png]).values())[0]
    words = res[1]  # (lines, words, letters)
    ocr = [{"text": w.text, "px": ocr_to_px(w.bounding_box),
            "line_id": getattr(w, "line_id", 0), "x": w.bounding_box["x"]} for w in words]

    # Propagate: each OCR word -> best GT token by IoU; assign that GT token's entity type
    matched = 0
    for o in ocr:
        best_i, best_iou = -1, 0.0
        for i, g in enumerate(gt_px):
            v = iou(o["px"], g)
            if v > best_iou:
                best_iou, best_i = v, i
        o["gt_i"] = best_i if best_iou >= 0.05 else -1
        o["iou"] = round(best_iou, 3)
        o["ent"] = entity(gt_tags[best_i]) if o["gt_i"] >= 0 else None
        o["gt_text"] = gt_tokens[best_i] if o["gt_i"] >= 0 else None
        if o["gt_i"] >= 0:
            matched += 1

    # Reading order: by OCR line (top->bottom) then x (left->right); recompute BIO over the run
    def line_top(lid):
        ys = [w["px"][1] for w in ocr if w["line_id"] == lid]
        return min(ys) if ys else 0
    ocr.sort(key=lambda o: (line_top(o["line_id"]), o["x"]))
    out_tokens, out_bboxes, out_tags = [], [], []
    prev_ent = None
    for o in ocr:
        ent = o["ent"]
        if ent is None:
            tag = "O"
            prev_ent = None
        else:
            tag = ("I-" if ent == prev_ent else "B-") + ent
            prev_ent = ent
        out_tokens.append(o["text"])
        out_bboxes.append(px_to_gt(o["px"]))
        out_tags.append(tag)

    reocr_ex = {"candidate_id": f"{cid}.reocr", "tokens": out_tokens,
                "bboxes": out_bboxes, "ner_tags": out_tags,
                "metadata": {**(ex.get("metadata") or {}), "source": "reocr-apple-vision"}}
    json.dump(reocr_ex, open(os.path.join(out_dir, f"{cid}.reocr.json"), "w"), indent=2)

    # Report
    gt_label_words = sum(1 for t in gt_tags if t not in ("O", "", None))
    re_label_words = sum(1 for t in out_tags if t != "O")
    gt_ents = {e for e in (entity(t) for t in gt_tags) if e}
    re_ents = {e for e in (entity(t) for t in out_tags) if e}
    print(f"candidate: {cid}")
    print(f"GT tokens: {len(gt_tokens)}  OCR words: {len(ocr)}  matched-to-GT: {matched} ({matched*100//max(1,len(ocr))}%)")
    print(f"labeled words: GT={gt_label_words}  re-OCR={re_label_words}")
    print(f"GT entity types: {sorted(gt_ents)}")
    print(f"re-OCR entity types: {sorted(re_ents)}   MISSING: {sorted(gt_ents - re_ents)}")
    print("\n--- sample matched tokens (GT_text -> OCR_text [label] iou) ---")
    shown = 0
    for o in ocr:
        if o["gt_i"] >= 0 and shown < 18:
            flag = "" if o["text"] == o["gt_text"] else "  <-- OCR DIFF"
            print(f"  {o['gt_text']!r:>16} -> {o['text']!r:<16} [{o['ent']}] iou={o['iou']}{flag}")
            shown += 1

    print("\n--- verifier on the re-OCR'd example ---")
    r = V.check(reocr_ex)
    print(f"score: {r.get('_score')}")
    for k, val in r.items():
        if k != "_score" and isinstance(val, dict) and val.get("pass") is False:
            print(f"  FAIL {k}: {val.get('detail','')[:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
