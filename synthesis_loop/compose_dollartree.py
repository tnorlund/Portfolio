#!/usr/bin/env python3.12
"""compose_dollartree.py -- canonical Dollar Tree composition from real content.

All 5 dev Dollar Tree receipts are curled PHOTOS whose OCR shears every item
row (amounts land on separate source lines ~2% of page height above their
descriptions, price tokens shatter into ".25"/"25T"). Rendering that OCR
faithfully reproduces the shear, and the grid renderer's source-conflict
guard rightly refuses to fuse cross-source-line words. So for Dollar Tree the
render is COMPOSED: the real receipt's CONTENT (items, prices, header, totals,
payment, footer) is re-laid onto the canonical column grid measured from the
one clean-OCR store-5304 receipt (4bdefa9e):

  DESCRIPTION left x=0.01, item text ends <= ~0.53 of paper
  QTY right-anchored at 0.655; PRICE right-anchored at 0.80
  TOTAL right-anchored at 0.995 with the T tax-flag suffix
  summary/payment labels left x=0.40, amounts right-anchored at 0.93
  footer narration centered

Row pitch, cap height and the wordmark/address blocks keep the measured
proportions. Damaged total tokens (".25T", "1.251") are re-composed from the
row's clean price when one exists -- composition, not faithful-OCR, per the
render-redo charter.

Usage:
  compose_dollartree.py <image_id> <receipt_id> <out.png>

Env: DYNAMODB_TABLE_NAME, AWS_REGION, BITMATRIX_DIR, PORTFOLIO_ENV.
"""

from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, os.path.join(os.path.dirname(HERE), "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

MERCHANT = "Dollar Tree"

# Canonical geometry (fractions of paper width / height units in the
# renderer's 0-1000 y-up coordinate space).
DESC_X = 0.01
QTY_RIGHT = 0.655
PRICE_RIGHT = 0.80
TOTAL_RIGHT = 0.995
LABEL_X = 0.40
AMOUNT_RIGHT = 0.93
ROW_PITCH = 38.0  # 1000-units per body row (measured ~0.038 of page)
CAP_H = 24.0  # body cap height in 1000-units (measured 23-24px / 586px)
CHAR_W = 0.0182  # per-char advance as paper-width fraction (pitch/cap 0.50)

_PRICE_RE = re.compile(r"^\$?\d*\.?\d+T?$")


def _norm_price(text: str) -> str | None:
    t = text.strip().lstrip("$")
    m = re.match(r"^(\d*)\.?(\d{2})(T|1)?$", t)
    if not m:
        return None
    whole = m.group(1) or "0"
    if len(whole) > 1 and "." not in t:
        # "1251" style shatter: assume cents split
        whole = whole
    return f"{whole}.{m.group(2)}"


def compose(image_id: str, receipt_id: int, out_png: str) -> int:
    import render_synthetic_receipts as rsr

    from receipt_dynamo.data.dynamo_client import DynamoClient

    table = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    region = os.environ.get("AWS_REGION", "us-east-1")
    c = DynamoClient(table_name=table, region=region)

    d = c.get_image_details(image_id)
    rec = next(r for r in d.receipts if r.receipt_id == receipt_id)
    lbl: dict[tuple, str] = {}
    for l in d.receipt_word_labels:
        if (
            l.receipt_id == receipt_id
            and str(getattr(l, "validation_status", "") or "").upper()
            != "INVALID"
        ):
            lbl[(l.line_id, l.word_id)] = l.label

    # ---- gather source lines (y-desc order) -------------------------------
    lines: dict[int, list] = {}
    for w in d.receipt_words:
        if w.receipt_id == receipt_id:
            lines.setdefault(w.line_id, []).append(w)
    ordered = []
    for lid, ws in lines.items():
        ws.sort(key=lambda w: w.top_left["x"])
        yc = sum(
            (w.top_left["y"] + w.bottom_right["y"]) / 2 for w in ws
        ) / len(ws)
        text = " ".join(w.text for w in ws)
        xc0 = min(w.top_left["x"] for w in ws)
        ordered.append((yc, xc0, lid, text, ws))
    ordered.sort(key=lambda t: -t[0])  # top of paper first (y-up)

    # ---- classify into logical rows ---------------------------------------
    UP = lambda s: s.upper()
    is_price = lambda t: bool(_PRICE_RE.match(t.strip()))

    items: list[dict] = []  # {desc, qty, price, total}
    header_rows: list[str] = []
    summary_rows: list[tuple[str, str]] = []
    summary_frags: list[tuple] = []  # (y, x0, text, words)
    payment_rows: list[list[str]] = []
    footer_rows: list[str] = []
    colhdr_seen = False
    wordmark_row = "DOLLAR TREE"
    price_frags: list[tuple[float, str, float]] = []  # (y, text, x0)

    for yc, xc0, lid, text, ws in ordered:
        T = UP(text).strip()
        if not T:
            continue
        if "DOLLAR" in T and "TREE" in T and yc > 0.85:
            wordmark_row = text
            continue  # re-emitted canonically; the logo overlay depicts it
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
            r"^SUB ?TOTAL|^SALES( TAX)?$|^TAX$|^TOTAL$|^TOTAL |"
            r"^AMERICAN|^EXPRES|^VISA|^MASTERCARD|^DEBIT|^CASH",
            T,
        ):
            summary_frags.append((yc, xc0, text, ws))
            continue
        if re.match(r"^\*{4,}|^APPROV|^PURCHASE|^CNTCTLESS|^AUTH|^CHANGE", T):
            payment_rows.append([text])
            continue
        if re.search(
            r"FEEDBACK|CAREERS|PLEASE|GROW YOUR|HTTPS|WWW\.|ASSOCIATE|"
            r"^\d{4} \d{5}|^SOLAS",
            T,
        ):
            footer_rows.append(text)
            continue
        # price-only fragments (right-column shear)
        if all(is_price(w.text) or w.text in ("1", "2", "3") for w in ws):
            for w in ws:
                price_frags.append((yc, w.text, w.top_left["x"]))
            continue
        # otherwise: an item description row -- require real words (letters
        # dominate) so shattered price/junk fragments never become items
        letters = sum(ch.isalpha() for ch in text)
        digits = sum(ch.isdigit() for ch in text)
        if (colhdr_seen or yc < 0.8) and letters >= 4 and letters > digits:
            items.append(
                {
                    "desc": text,
                    "y": yc,
                    "x0": xc0,
                    "qty": "1",
                    "price": None,
                    "total": None,
                }
            )

    # assemble summary rows: group label/amount fragments by y (the shear
    # splits "Sub Total" from its "$6.25" onto different OCR lines)
    summary_frags.sort(key=lambda f: -f[0])
    srows: list[dict] = []
    for yc, x0, text, ws in summary_frags:
        if srows and abs(srows[-1]["y"] - yc) < 0.014:
            srows[-1]["frags"].append((x0, text, ws))
            srows[-1]["y"] = (srows[-1]["y"] + yc) / 2
        else:
            srows.append({"y": yc, "frags": [(x0, text, ws)]})
    # amounts may still sit on their own price-fragment lines; matched below
    for r in srows:
        r["frags"].sort()
        label_parts, amt = [], ""
        for _, text, ws in r["frags"]:
            for w in ws:
                if is_price(w.text) and (w.top_left["x"] > 0.6):
                    amt = w.text
                else:
                    label_parts.append(w.text)
        summary_rows.append((" ".join(label_parts), amt, r["y"]))

    # merge description fragments that the photo shear split across OCR
    # lines at (nearly) the same y -- they are ONE printed item row
    items.sort(key=lambda it: -it["y"])
    merged: list[dict] = []
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

    # associate price fragments to the nearest item row by y
    for yc, t, x0 in price_frags:
        if not items:
            break
        row = min(items, key=lambda it: abs(it["y"] - yc))
        if abs(row["y"] - yc) > 0.02:
            continue
        p = _norm_price(t)
        clean = bool(re.match(r"^\d+\.\d{2}T?$", t.strip().lstrip("$")))
        if t in ("1", "2", "3") and x0 > 0.55:
            row["qty"] = t
        elif p and (x0 > 0.85 or t.upper().endswith("T")):
            # prefer an undamaged x.xx fragment over a shattered ".25"/"251"
            if row["total"] is None or (clean and not row.get("_tot_clean")):
                row["total"] = p
                row["_tot_clean"] = clean
        elif p:
            if row["price"] is None or (clean and not row.get("_pr_clean")):
                row["price"] = p
                row["_pr_clean"] = clean

    # a price fragment far from any item may be a SUMMARY amount: match to
    # the nearest summary row by y
    fixed_summary = []
    for label, amt, sy in summary_rows:
        if not amt:
            near = [
                (abs(sy - yc), t)
                for yc, t, x0 in price_frags
                if x0 > 0.55
                and abs(sy - yc) < 0.02
                and "." in t
                and len(t.lstrip("$")) >= 4
            ]
            if near:
                amt = min(near)[1]
        fixed_summary.append((label, amt))
    summary_rows = fixed_summary

    for it in items:
        if it["price"] is None and it["total"] is not None:
            it["price"] = it["total"]
        if it["total"] is None and it["price"] is not None:
            it["total"] = it["price"]
    # a "row" that never attracted any amount is a stray fragment, not an item
    items = [it for it in items if it["price"] or it["total"]]

    # ---- emit canonical words --------------------------------------------
    words: list[dict] = []
    y = 940.0  # leave the top band for the logo overlay

    def put(
        text, x_left=None, right=None, cap=CAP_H, labels=None, center=False
    ):
        nonlocal y
        if not text:
            return
        wpx = len(text) * CHAR_W * 1000
        if center:
            x0 = 500 - wpx / 2
        elif right is not None:
            x0 = right * 1000 - wpx
        else:
            x0 = (x_left or 0.0) * 1000
        words.append(
            {
                "text": text,
                "bbox": [x0, y, x0 + wpx, y + cap],
                "labels": labels or [],
                "line_id": len(words) + 1,
                "word_id": 1,
            }
        )

    def put_tokens(text, x_left, cap=CAP_H, labels=None):
        x = x_left
        for tok in str(text).split():
            put(tok, x_left=x, cap=cap, labels=labels)
            x += (len(tok) + 1) * CHAR_W

    def row(*cells):
        """cells: (text, kwargs) placed on ONE row, then advance."""
        nonlocal y
        y0 = y
        for text, kw in cells:
            y = y0
            put(text, **kw)
        y = y0 - ROW_PITCH

    # canonical wordmark line (logo overlay anchors on and depicts it)
    y0w = y
    xw = 0.5 - len(wordmark_row.replace(" ", " ")) * CHAR_W * 1.6 / 2
    for tok in wordmark_row.split():
        put(tok, x_left=xw, cap=CAP_H * 1.9, labels=["MERCHANT_NAME"])
        xw += (len(tok) + 1) * CHAR_W * 1.6
    y = y0w - ROW_PITCH * 2.2
    for text in header_rows:
        y0 = y
        put_tokens(text, 0.01, labels=["ADDRESS_LINE"])
        y = y0 - ROW_PITCH
    y -= ROW_PITCH * 0.5
    row(
        ("DESCRIPTION", {"x_left": 0.0}),
        ("QTY", {"right": QTY_RIGHT}),
        ("PRICE", {"right": PRICE_RIGHT}),
        ("TOTAL", {"right": TOTAL_RIGHT}),
    )
    for it in items:
        y0 = y
        put_tokens(it["desc"], DESC_X, labels=["PRODUCT_NAME"])
        y = y0
        cells = []
        cells.append((it["qty"], {"right": QTY_RIGHT, "labels": ["QUANTITY"]}))
        if it["price"]:
            cells.append(
                (it["price"], {"right": PRICE_RIGHT, "labels": ["UNIT_PRICE"]})
            )
        if it["total"]:
            cells.append(
                (
                    f"{it['total']}T",
                    {"right": TOTAL_RIGHT, "labels": ["LINE_TOTAL"]},
                )
            )
        row(*cells)
    y -= ROW_PITCH * 0.5
    for label, amt in summary_rows:
        y0 = y
        put_tokens(label, LABEL_X)
        y = y0
        if amt:
            put(amt, right=AMOUNT_RIGHT)
        y = y0 - ROW_PITCH
    for cells_text in payment_rows:
        row((cells_text[0], {"x_left": LABEL_X}))
    y -= ROW_PITCH * 0.5
    for text in footer_rows:
        y0 = y
        wtx = len(text) * CHAR_W
        put_tokens(text, 0.5 - wtx / 2)
        y = y0 - ROW_PITCH

    receipt = {"words": words, "merchant_name": MERCHANT}

    prof = rsr.cached_font_profile(
        table, MERCHANT, region=region, max_receipts=12
    )
    ss = rsr.section_scale_for_merchant(MERCHANT)
    typ = rsr.merchant_typography(MERCHANT)
    atlas = None
    if "bitmap_font" not in typ:
        atlas = rsr.cached_glyph_atlas(
            table, MERCHANT, region=region, max_receipts=8
        )
    wt = 760
    ht = int(round(wt * rec.height / rec.width))
    rsr._render_cached_hybrid(
        receipt,
        atlas,
        profile=prof,
        width=wt,
        height=ht,
        path=out_png,
        section_scale=ss,
        **typ,
    )
    print(
        f"composed {len(items)} items, {len(summary_rows)} summary rows "
        f"-> {out_png}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(compose(sys.argv[1], int(sys.argv[2]), sys.argv[3]))
