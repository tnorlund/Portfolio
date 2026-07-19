#!/usr/bin/env python3.12
"""compose_dollartree.py -- canonical Dollar Tree composition from real content.

All 5 dev Dollar Tree receipts are curled PHOTOS whose OCR shears every item
row (amounts land on separate source lines ~2% of page height above their
descriptions, price tokens shatter into ".25"/"251"). Rendering that OCR
faithfully reproduces the shear. So Dollar Tree renders are COMPOSED: the
real receipt's CONTENT is re-laid onto the canonical column grid measured
from the one clean-OCR store-5304 receipt (4bdefa9e):

  DESCRIPTION left x=0.01, item text capped at the 0.53 column
  QTY right-anchored at 0.655; PRICE right-anchored at 0.80
  TOTAL right-anchored at 0.995 with the OBSERVED tax flag (never invented)
  summary/payment labels left x=0.40, amounts right-anchored at 0.93
  footer narration centered; row pitch adapts so long receipts never clip

``canonical_words`` is a PURE transform over renderer-format word dicts and
is invoked by the production render path via the profile key
``"compose": "dollartree"`` (see scripts/render_synthetic_receipts.py), so
normal renders, glyph_review and section_compare all render the canonical
layout. This CLI is a thin wrapper for one-off renders; it refuses receipts
whose RECEIPT_PLACE merchant is not Dollar Tree.

Usage:
  compose_dollartree.py <image_id> <receipt_id> <out.png>
"""

from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.join(os.path.dirname(HERE), "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MERCHANT = "Dollar Tree"

DESC_X = 0.01
DESC_MAX_X = 0.53
QTY_RIGHT = 0.655
PRICE_RIGHT = 0.80
TOTAL_RIGHT = 0.995
LABEL_X = 0.40
AMOUNT_RIGHT = 0.93
ROW_PITCH = 38.0  # 1000-units per body row (measured ~0.038 of page)
CAP_H = 24.0
CHAR_W = 0.0182  # per-char advance, paper-width fraction (pitch/cap 0.50)

_PRICE_RE = re.compile(r"^\$?\d*\.?\d+T?$")
_QTY_RE = re.compile(r"^\d{1,2}$")


def _norm_price(text):
    """(normalized x.xx, observed_tax_flag) or None.

    Lazy leading group so dotless shattered tokens parse with the trailing
    OCR-confused flag ("1251" -> ("1.25", True)); the flag records whether
    the SOURCE token carried a T (or the trailing 1 OCR mistakes it for),
    so the render never fabricates taxability.
    """
    t = text.strip().lstrip("$")
    m = re.match(r"^(\d*?)\.?(\d{2})(T|1)?$", t)
    if not m:
        return None
    return f"{m.group(1) or '0'}.{m.group(2)}", m.group(3) is not None


def _money(value):
    return f"{value:.2f}"


def canonical_words(raw_words):
    """Re-lay renderer-format Dollar Tree words onto the canonical grid.

    ``raw_words``: [{"text", "bbox" ([x0,y0,x1,y1], y-up 0-1000), "labels",
    "line_id", "word_id"}, ...] with INVALID labels already filtered by the
    caller (the standard render input). Returns replacement words in the
    same format.
    """
    lines = {}
    for w in raw_words:
        if w.get("bbox"):
            lines.setdefault(w.get("line_id") or id(w), []).append(w)
    ordered = []
    for lid, ws in lines.items():
        ws.sort(key=lambda w: w["bbox"][0])
        yc = sum((w["bbox"][1] + w["bbox"][3]) / 2 for w in ws) / len(ws)
        yc /= 1000.0
        xc0 = min(w["bbox"][0] for w in ws) / 1000.0
        text = " ".join(str(w.get("text") or "") for w in ws)
        ordered.append((yc, xc0, lid, text, ws))
    ordered.sort(key=lambda t: -t[0])

    def is_price(t):
        return bool(_PRICE_RE.match(t.strip()))

    items = []
    header_rows = []
    summary_frags = []
    payment_rows = []
    footer_rows = []
    colhdr_seen = False
    wordmark_row = "DOLLAR TREE"
    price_frags = []

    for yc, xc0, lid, text, ws in ordered:
        T = text.upper().strip()
        if not T:
            continue
        if "DOLLAR" in T and "TREE" in T and yc > 0.85:
            wordmark_row = text
            continue
        if T.startswith(("DESCRIPTION", "QTY", "PRICE", "TOTAL")) and (
            len(ws) == 1 and yc > 0.5
        ):
            colhdr_seen = True
            continue
        if re.search(r"STORE#?|STORE:", T) or T.startswith("("):
            header_rows.append(text)
            continue
        if re.match(r"^\d{3,5} |^HENDERSON|^\d{3} STREET|SUITE|^\d{5}", T) or (
            "NV" in T.split() or "NU" in T.split()
        ):
            header_rows.append(text)
            continue
        if re.match(
            r"^SUB ?TOTAL\b|^SALES TAX\b|^SALES$|^TAX\b|^TOTAL\b|"
            r"^CHANGE\b|^AMERICAN|^EXPRES|^VISA|^MASTERCARD|^DEBIT|^CASH\b",
            T,
        ):
            summary_frags.append((yc, xc0, text, ws))
            continue
        if re.match(r"^\*{4,}|^APPROV|^PURCHASE|^CNTCTLESS|^AUTH", T):
            payment_rows.append(text)
            continue
        if re.search(
            r"FEEDBACK|CAREERS|PLEASE|GROW YOUR|HTTPS|WWW\.|ASSOCIATE|"
            r"^\d{4} \d{5}|^SOLAS",
            T,
        ):
            footer_rows.append(text)
            continue
        if all(is_price(w["text"]) or _QTY_RE.match(w["text"]) for w in ws):
            for w in ws:
                price_frags.append((yc, w["text"], w["bbox"][0] / 1000.0))
            continue
        # An INTACT row carries description AND amounts on one line
        # ("SCRUB BRUSH 2 1.25 2.50T"): split the numeric tail off and feed
        # it through the same fragment attachment as sheared lines (dy = 0,
        # so the amounts bind to this very item).
        num_ws = [
            w
            for w in ws
            if is_price(str(w["text"])) or _QTY_RE.match(str(w["text"]))
        ]
        desc_text = " ".join(str(w["text"]) for w in ws if w not in num_ws)
        for w in num_ws:
            price_frags.append((yc, str(w["text"]), w["bbox"][0] / 1000.0))
        letters = sum(ch.isalpha() for ch in desc_text)
        digits = sum(ch.isdigit() for ch in desc_text)
        if (colhdr_seen or yc < 0.8) and letters >= 4 and letters > digits:
            items.append(
                {
                    "desc": desc_text,
                    "y": yc,
                    "x0": xc0,
                    "qty": None,
                    "price": None,
                    "total": None,
                    "flag": False,
                    "_pr_clean": False,
                    "_tot_clean": False,
                }
            )

    # assemble summary rows by y (labels + amounts shear apart)
    summary_frags.sort(key=lambda f: -f[0])
    srows = []
    for yc, x0, text, ws in summary_frags:
        if srows and abs(srows[-1]["y"] - yc) < 0.014:
            srows[-1]["frags"].append((x0, text, ws))
            srows[-1]["y"] = (srows[-1]["y"] + yc) / 2
        else:
            srows.append({"y": yc, "frags": [(x0, text, ws)]})
    summary_rows = []
    for r in srows:
        r["frags"].sort()
        label_parts, amt = [], ""
        for _, text, ws in r["frags"]:
            for w in ws:
                p = _norm_price(w["text"]) if is_price(w["text"]) else None
                if p and w["bbox"][0] / 1000.0 > 0.6:
                    amt = p[0]  # normalize same-line amounts too
                else:
                    label_parts.append(w["text"])
        summary_rows.append(
            {"label": " ".join(label_parts), "amt": amt, "y": r["y"]}
        )

    # merge description fragments split at (nearly) the same y
    items.sort(key=lambda it: -it["y"])
    merged = []
    for it in items:
        if merged and abs(merged[-1]["y"] - it["y"]) < 0.008:
            frags = sorted(
                [
                    (merged[-1]["x0"], merged[-1]["desc"]),
                    (it["x0"], it["desc"]),
                ]
            )
            merged[-1]["desc"] = " ".join(t for _, t in frags)
            merged[-1]["x0"] = frags[0][0]
            merged[-1]["y"] = (merged[-1]["y"] + it["y"]) / 2
        else:
            merged.append(it)
    items = merged

    # attach price/qty fragments (nearest item by y)
    leftover_amounts = []
    for yc, t, x0 in price_frags:
        row = min(items, key=lambda it: abs(it["y"] - yc)) if items else None
        if row is None or abs(row["y"] - yc) > 0.02:
            leftover_amounts.append((yc, t, x0))
            continue
        p = _norm_price(t)
        clean = bool(re.match(r"^\d+\.\d{2}T?$", t.strip().lstrip("$")))
        if _QTY_RE.match(t) and 0.55 < x0 < 0.72:
            row["qty"] = t
        elif p and (x0 > 0.85 or p[1]):
            if row["total"] is None or (clean and not row["_tot_clean"]):
                row["total"] = p[0]
                row["flag"] = p[1] or row["flag"]
                row["_tot_clean"] = clean
        elif p:
            if row["price"] is None or (clean and not row["_pr_clean"]):
                row["price"] = p[0]
                row["_pr_clean"] = clean

    # summary amounts: normalized fragments (any length), nearest row by y
    for r in summary_rows:
        if r["amt"]:
            continue
        best = None
        for yc, t, x0 in leftover_amounts:
            p = _norm_price(t)
            if p is None or x0 <= 0.55:
                continue
            dy = abs(r["y"] - yc)
            if dy < 0.02 and (best is None or dy < best[0]):
                best = (dy, p[0])
        if best:
            r["amt"] = best[1]

    # repair with quantity arithmetic, never bare copies
    for it in items:
        qty = int(it["qty"]) if it["qty"] else 1
        it["qty"] = str(qty)
        try:
            if it["price"] is None and it["total"] is not None:
                it["price"] = _money(float(it["total"]) / max(1, qty))
            elif it["total"] is None and it["price"] is not None:
                it["total"] = _money(float(it["price"]) * qty)
        except ValueError:
            pass
    # a fragment with no amounts continues the item above it
    kept = []
    for it in items:
        if it["price"] or it["total"]:
            kept.append(it)
        elif kept and abs(kept[-1]["y"] - it["y"]) < 0.06:
            kept[-1]["desc"] += " " + it["desc"]
        # else: isolated junk fragment, dropped
    items = kept

    # ---- emit --------------------------------------------------------------
    n_rows = (
        2
        + len(header_rows)
        + 1
        + len(items)
        + len(summary_rows)
        + len(payment_rows)
        + len(footer_rows)
        + 3
    )
    # No floor: a long receipt gets proportionally smaller rows
    # rather than rows past the bottom of the canvas.
    pitch = min(ROW_PITCH, 900.0 / max(1, n_rows))
    cap = CAP_H * (pitch / ROW_PITCH)
    max_desc_chars = int((DESC_MAX_X - DESC_X) / CHAR_W)

    words = []
    y = 940.0

    def put(text, x_left=None, right=None, cap_h=None, labels=None):
        if not text:
            return
        c = cap_h or cap
        wpx = len(text) * CHAR_W * 1000
        if right is not None:
            x0 = right * 1000 - wpx
        else:
            x0 = (x_left or 0.0) * 1000
        words.append(
            {
                "text": text,
                "bbox": [x0, y, x0 + wpx, y + c],
                "labels": labels or [],
                "line_id": len(words) + 1,
                "word_id": 1,
            }
        )

    def put_tokens(text, x_left, cap_h=None, labels=None):
        x = x_left
        for tok in str(text).split():
            put(tok, x_left=x, cap_h=cap_h, labels=labels)
            x += (len(tok) + 1) * CHAR_W

    xw = 0.5 - len(wordmark_row) * CHAR_W * 1.6 / 2
    for tok in wordmark_row.split():
        put(tok, x_left=xw, cap_h=cap * 1.9, labels=["MERCHANT_NAME"])
        xw += (len(tok) + 1) * CHAR_W * 1.6
    y -= pitch * 2.2
    for text in header_rows:
        put_tokens(text, 0.01, labels=["ADDRESS_LINE"])
        y -= pitch
    y -= pitch * 0.5
    put("DESCRIPTION", x_left=0.0)
    put("QTY", right=QTY_RIGHT)
    put("PRICE", right=PRICE_RIGHT)
    put("TOTAL", right=TOTAL_RIGHT)
    y -= pitch
    for it in items:
        put_tokens(
            it["desc"][:max_desc_chars], DESC_X, labels=["PRODUCT_NAME"]
        )
        put(it["qty"], right=QTY_RIGHT, labels=["QUANTITY"])
        if it["price"]:
            put(it["price"], right=PRICE_RIGHT, labels=["UNIT_PRICE"])
        if it["total"]:
            suffix = "T" if it["flag"] else ""
            put(
                f"{it['total']}{suffix}",
                right=TOTAL_RIGHT,
                labels=["LINE_TOTAL"],
            )
        y -= pitch
    y -= pitch * 0.5
    for r in summary_rows:
        put_tokens(r["label"], LABEL_X)
        if r["amt"]:
            put(r["amt"], right=AMOUNT_RIGHT)
        y -= pitch
    for text in payment_rows:
        put_tokens(text, LABEL_X)
        y -= pitch
    y -= pitch * 0.5
    for text in footer_rows:
        put_tokens(text, 0.5 - len(text) * CHAR_W / 2)
        y -= pitch
    return words


def main(argv=None):
    argv = argv or sys.argv[1:]
    image_id, receipt_id, out_png = argv[0], int(argv[1]), argv[2]
    import render_synthetic_receipts as rsr

    from receipt_dynamo.data.dynamo_client import DynamoClient

    table = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    region = os.environ.get("AWS_REGION", "us-east-1")
    c = DynamoClient(table_name=table, region=region)
    d = c.get_image_details(image_id)

    # merchant guard: never canonicalize a non-Dollar-Tree receipt
    place = next(
        (
            p
            for p in getattr(d, "receipt_places", []) or []
            if p.receipt_id == receipt_id
        ),
        None,
    )
    merchant_name = getattr(place, "merchant_name", "") or ""
    canonical_key = rsr.get_merchant_profile_key(merchant_name)[0]
    if place is None or canonical_key != MERCHANT:
        raise SystemExit(
            f"refusing: receipt {image_id}#{receipt_id} has "
            f"{'no RECEIPT_PLACE' if place is None else f'merchant {merchant_name!r} (profile {canonical_key!r})'}"
            f" -- a verified Dollar Tree place is required"
        )

    rec = next(r for r in d.receipts if r.receipt_id == receipt_id)
    lbl = {
        (l.line_id, l.word_id): l.label
        for l in d.receipt_word_labels
        if l.receipt_id == receipt_id
        and str(getattr(l, "validation_status", "") or "").upper() != "INVALID"
    }
    raw = [
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
        if w.receipt_id == receipt_id
    ]
    receipt = {"words": raw, "merchant_name": MERCHANT}
    prof = rsr.cached_font_profile(
        table, MERCHANT, region=region, max_receipts=12
    )
    ss = rsr.section_scale_for_merchant(MERCHANT)
    typ = rsr.merchant_typography(MERCHANT)
    wt = 760
    ht = int(round(wt * rec.height / rec.width))
    # the production path composes via the profile "compose" key inside
    # _render_cached_hybrid; this CLI just renders the same raw input
    rsr._render_cached_hybrid(
        receipt,
        None,
        profile=prof,
        width=wt,
        height=ht,
        path=out_png,
        section_scale=ss,
        **typ,
    )
    print(f"-> {out_png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
