#!/usr/bin/env python3
"""re_ocr_align.py — re-OCR label propagation via CHARACTER-LEVEL ALIGNMENT (Genalog method).

Supersedes the IoU spike. Because we control the synthetic source text, aligning the OCR string to the
known GT string is strictly more reliable than matching boxes (the IoU spike dropped SUBTOTAL/TAX on
low-IoU key/value lines). Pipeline:
  reading-order GT string (label per char span) + reading-order OCR string
  -> Needleman-Wunsch char alignment
  -> project each OCR token's label from the GT chars it aligns to
  -> IoU spatial match ONLY as a fallback for tokens the alignment can't place
  -> recompute BIO over reading order; boxes come from Apple Vision.

Usage: re_ocr_align.py <bundle.json> <hybrid.png> <out_dir> [candidate_index]
"""
from __future__ import annotations
import json, os, sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from receipt_upload.ocr import apple_vision_ocr  # noqa: E402
import verify_candidates as V  # noqa: E402

W, H, MARGIN, COORD = 460, 1100, 10, 1000.0
INNER_W, INNER_H = W - 2 * MARGIN, H - 2 * MARGIN


def gt_to_px(b):
    x0, y0, x1, y1 = b
    return [MARGIN + (min(x0, x1) / COORD) * INNER_W,
            MARGIN + ((COORD - max(y0, y1)) / COORD) * INNER_H,
            MARGIN + (max(x0, x1) / COORD) * INNER_W,
            MARGIN + ((COORD - min(y0, y1)) / COORD) * INNER_H]


def ocr_to_px(bb):
    x, y, w, h = bb["x"], bb["y"], bb["width"], bb["height"]
    return [x * W, (1.0 - (y + h)) * H, (x + w) * W, (1.0 - y) * H]


def px_to_gt(px):
    l, t, r, b = px
    return [round((l - MARGIN) / INNER_W * COORD, 1),
            round(COORD - (b - MARGIN) / INNER_H * COORD, 1),
            round((r - MARGIN) / INNER_W * COORD, 1),
            round(COORD - (t - MARGIN) / INNER_H * COORD, 1)]


def iou(a, b):
    ix0, iy0, ix1, iy1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def entity(tag):
    return tag[2:] if tag and tag[:2] in ("B-", "I-") else (None if tag in (None, "O", "") else tag)


def build_seq(items):
    """items: list of (text, payload). Returns joined string + per-char payload + per-char token index."""
    s, char_pl, char_tok = [], [], []
    for ti, (txt, pl) in enumerate(items):
        if ti > 0:
            s.append(" "); char_pl.append(None); char_tok.append(-1)
        for ch in txt:
            s.append(ch); char_pl.append(pl); char_tok.append(ti)
    return "".join(s), char_pl, char_tok


def nw_align(a, b, MATCH=2, MIS=-1, GAP=-1):
    """Needleman-Wunsch. Returns dict: OCR-char-index -> GT-char-index (only aligned, non-gap pairs)."""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = i * GAP
    for j in range(1, m + 1):
        dp[0][j] = j * GAP
    for i in range(1, n + 1):
        ai = a[i - 1]; row = dp[i]; prev = dp[i - 1]
        for j in range(1, m + 1):
            sc = MATCH if ai == b[j - 1] else MIS
            d = prev[j - 1] + sc
            u = prev[j] + GAP
            ll = row[j - 1] + GAP
            row[j] = d if d >= u and d >= ll else (u if u >= ll else ll)
    i, j, omap = n, m, {}
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + (MATCH if a[i - 1] == b[j - 1] else MIS):
            omap[j - 1] = i - 1; i -= 1; j -= 1            # a=GT(i), b=OCR(j) -> ocr j-1 -> gt i-1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + GAP:
            i -= 1
        else:
            j -= 1
    return omap


def gt_lines(tokens, tags, bboxes):
    """Group GT tokens into reading-order lines. Each item: {ents, toks, pxs, yc_px} left-to-right."""
    items = []
    for tok, tag, bb in zip(tokens, tags, bboxes):
        px = gt_to_px(bb)
        items.append({"tok": tok, "ent": entity(tag), "px": px, "ycpx": (px[1] + px[3]) / 2})
    items.sort(key=lambda d: (round(d["ycpx"] / 14), d["px"][0]))  # top->bottom, left->right (pixel y)
    lines, cur, cur_y = [], [], None
    for d in items:
        if cur_y is None or abs(d["ycpx"] - cur_y) <= 10:
            cur.append(d); cur_y = d["ycpx"] if cur_y is None else (cur_y + d["ycpx"]) / 2
        else:
            lines.append(cur); cur, cur_y = [d], d["ycpx"]
    if cur:
        lines.append(cur)
    return lines


def main():
    bundle_p, hybrid_png, out_dir = sys.argv[1:4]
    ci = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    os.makedirs(out_dir, exist_ok=True)
    ex = json.load(open(bundle_p))["synthetic_training_examples"][ci]
    cid = ex.get("candidate_id", f"candidate-{ci}")
    glines = gt_lines(ex["tokens"], ex["ner_tags"], ex["bboxes"])
    gline_yc = [sum(d["ycpx"] for d in ln) / len(ln) for ln in glines]
    all_gt = [d for ln in glines for d in ln]  # for IoU fallback

    res = list(apple_vision_ocr([hybrid_png]).values())[0]
    words = res[1]
    ocr = [{"text": w.text, "px": ocr_to_px(w.bounding_box), "line_id": getattr(w, "line_id", 0)}
           for w in words]
    # group OCR words into their Apple-Vision lines, ordered top->bottom, left->right
    olines_map = {}
    for o in ocr:
        olines_map.setdefault(o["line_id"], []).append(o)
    olines = sorted(olines_map.values(), key=lambda ln: min(o["px"][1] for o in ln))
    for ln in olines:
        ln.sort(key=lambda o: o["px"][0])

    ents = {id(o): None for o in ocr}
    src = {id(o): "O" for o in ocr}

    # ---- per-line: spatially anchor each OCR line to the nearest GT line, then char-align WITHIN it ----
    for oln in olines:
        oyc = sum((o["px"][1] + o["px"][3]) / 2 for o in oln) / len(oln)
        gi = min(range(len(glines)), key=lambda k: abs(gline_yc[k] - oyc)) if glines else None
        if gi is None or abs(gline_yc[gi] - oyc) > 40:  # no GT line near this OCR line
            continue
        gln = glines[gi]
        gt_str, gt_char_ent, _ = build_seq([(d["tok"], d["ent"]) for d in gln])
        ocr_str, _, ocr_char_tok = build_seq([(o["text"], j) for j, o in enumerate(oln)])
        omap = nw_align(gt_str, ocr_str)  # ocr_char -> gt_char (within this line only -> no global drift)
        per_tok = [[] for _ in oln]
        for oc_i, gt_i in omap.items():
            tj = ocr_char_tok[oc_i]
            if tj >= 0 and gt_char_ent[gt_i]:
                per_tok[tj].append(gt_char_ent[gt_i])
        for j, o in enumerate(oln):
            if per_tok[j]:
                ents[id(o)] = Counter(per_tok[j]).most_common(1)[0][0]
                src[id(o)] = "align"

    # ---- IoU fallback ONLY for tokens still unlabeled ----
    for o in ocr:
        if ents[id(o)] is not None:
            continue
        best_d, best = None, 0.0
        for d in all_gt:
            v = iou(o["px"], d["px"])
            if v > best:
                best, best_d = v, d
        if best_d is not None and best >= 0.10 and best_d["ent"]:
            ents[id(o)] = best_d["ent"]
            src[id(o)] = "iou"

    # flatten OCR back to reading order for output
    ocr = [o for ln in olines for o in ln]

    # ---- BIO over reading order ----
    out_tokens, out_bboxes, out_tags = [], [], []
    prev = None
    for o in ocr:
        e = ents[id(o)]
        tag = "O" if e is None else (("I-" if e == prev else "B-") + e)
        prev = e
        out_tokens.append(o["text"]); out_bboxes.append(px_to_gt(o["px"])); out_tags.append(tag)

    reocr_ex = {"candidate_id": f"{cid}.reocr-align", "tokens": out_tokens, "bboxes": out_bboxes,
                "ner_tags": out_tags, "metadata": {**(ex.get("metadata") or {}),
                "source": "reocr-apple-vision-charalign"}}
    json.dump(reocr_ex, open(os.path.join(out_dir, f"{cid}.reocr-align.json"), "w"), indent=2)

    # ---- report ----
    gt_ents = {d["ent"] for d in all_gt if d["ent"]}
    re_ents = {ents[id(o)] for o in ocr if ents[id(o)]}
    labeled = sum(1 for o in ocr if ents[id(o)])
    n_align = sum(1 for o in ocr if src[id(o)] == "align")
    n_iou = sum(1 for o in ocr if src[id(o)] == "iou")
    print(f"candidate: {cid}")
    print(f"GT tokens: {len(all_gt)}  GT lines: {len(glines)}  OCR words: {len(ocr)}  labeled: {labeled} "
          f"(align={n_align}, iou-fallback={n_iou})")
    print(f"GT entity types ({len(gt_ents)}): {sorted(gt_ents)}")
    print(f"re-OCR entity types ({len(re_ents)}): {sorted(re_ents)}")
    missing = sorted(gt_ents - re_ents)
    print(f"MISSING entity types: {missing if missing else 'NONE  <-- all recovered'}")
    print("\n--- labeled tokens (text [label] via) ---")
    for o in ocr:
        if ents[id(o)]:
            print(f"  {o['text']!r:<20} [{ents[id(o)]}] ({src[id(o)]})")
    print("\n--- verifier ---")
    r = V.check(reocr_ex)
    print(f"score: {r.get('_score')}  fails: "
          f"{[k for k,v in r.items() if k!='_score' and isinstance(v,dict) and v.get('pass') is False]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
