#!/usr/bin/env python3.12
"""compose_dollartree.py -- canonical Dollar Tree composition from real content.

All 5 dev Dollar Tree receipts are curled PHOTOS whose OCR shears every item
row (amounts land on separate source lines ~2% of page height above their
descriptions, price tokens shatter into ".25"/"251"). Rendering that OCR
faithfully reproduces the shear. So Dollar Tree renders are COMPOSED: the
real receipt's CONTENT is re-laid onto the canonical column grid measured
from the one clean-OCR store-5304 receipt (4bdefa9e):

  DESCRIPTION left x=0.01, item text capped at the 0.58 column boundary
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
for _p in (
    HERE,
    os.path.join(os.path.dirname(HERE), "scripts"),
    os.path.join(os.path.dirname(HERE), "receipt_agent"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from receipt_agent.agents.label_evaluator.rendering.price_tokens import (  # noqa: E402
    DOLLARTREE_PRICE_TOKEN,
)

MERCHANT = "Dollar Tree"

DESC_X = 0.01
DESC_MAX_X = 0.58  # column boundary: template descs run 30 chars (QTY at 0.60)
QTY_RIGHT = 0.655
PRICE_RIGHT = 0.80
TOTAL_RIGHT = 0.995
LABEL_X = 0.40
AMOUNT_RIGHT = 0.93
ROW_PITCH = 38.0  # 1000-units per body row (measured ~0.038 of page)
CAP_H = 24.0
CHAR_W = 0.0182  # per-char advance, paper-width fraction (pitch/cap 0.50)

# Defined once in price_tokens (the shattered-photo-OCR variant).
_PRICE_RE = DOLLARTREE_PRICE_TOKEN
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
    from decimal import ROUND_HALF_UP, Decimal

    return str(
        Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )


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
    register_frags = []
    suffix_frags = []

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
        if (
            re.match(r"^\d{3,5} |^HENDERSON|^\d{3} STREET|SUITE|^\d{5}", T)
            and yc > 0.6
        ) or ("NV" in T.split() or "NU" in T.split()):
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
            r"FEEDBACK|CAREERS|PLEASE|GROW YOUR|HTTPS|WWW\.|SOCIATE|"
            r"^\d{4} \d{5}|^SOLAS",
            T,
        ):
            footer_rows.append(text)
            continue
        if all(is_price(w["text"]) or _QTY_RE.match(w["text"]) for w in ws):
            # BOTTOM-ZONE numeric lines are the register line's shattered
            # fields (store/reg/txn ids), not item amounts.
            if yc < 0.22:
                register_frags.append((yc, xc0, text))
            else:
                for w in ws:
                    price_frags.append((yc, w["text"], w["bbox"][0] / 1000.0))
            continue
        if yc < 0.22 and re.match(r"^[\d/: .]+$", T):
            # bottom-zone date/time fields join the register line too
            register_frags.append((yc, xc0, text))
            continue
        if yc < 0.15 and sum(ch.isdigit() for ch in T) >= sum(
            ch.isalpha() for ch in T
        ):
            # register ids can be ALNUM ('501454B6'); digit-majority
            # bottom-zone lines are register fields, not items
            register_frags.append((yc, xc0, text))
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
        if (
            (colhdr_seen or yc < 0.8)
            and desc_text
            and letters < 4
            and 0.2 < xc0 < 0.55
        ):
            # a size/variant SUFFIX shed from its item row ("11X10." at the
            # description-tail x) -- attach to the nearest row later
            suffix_frags.append((yc, xc0, desc_text))
            continue
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

    # merge description fragments split at (nearly) the same y. The two
    # fragments OVERLAP at the shear seam, so joining them naively repeats
    # tokens ("...GREY" + "GREY CLLPSB..."): drop a joined token that
    # repeats, or is a >=4-char prefix of, an already-kept token.
    def _merge_desc(left, right):
        """Join two overlapping fragments, deduping ONLY at the seam: the
        longest suffix of the left fragment that matches (or is a >=4-char
        prefix of) the right fragment's prefix is dropped once. Repeated
        words elsewhere ("DOG FOOD" + "DOG BOWL") are preserved."""
        lt, rt = left.split(), right.split()
        overlap = 0
        for k in range(min(len(lt), len(rt)), 0, -1):
            pairs = zip(lt[-k:], rt[:k])
            if all(
                a == b
                or (len(b) >= 4 and a.startswith(b))
                or (len(a) >= 4 and b.startswith(a))
                for a, b in pairs
            ):
                overlap = k
                break
        joined = lt + rt[overlap:]
        # OCR double-reads also leave a TRUNCATED copy away from the seam
        # ("CLLPSB" beside "CLLPSBLE"): drop a token that is a >=5-char
        # strict prefix of another kept token (5+ chars so real short words
        # like "DOG" are never touched).
        out = []
        for tok in joined:
            if len(tok) >= 5 and any(
                other != tok and other.startswith(tok) for other in joined
            ):
                continue
            out.append(tok)
        return " ".join(out)

    items.sort(key=lambda it: -it["y"])
    # the fragment-merge window is the MEASURED row pitch (photo shear can
    # push a fragment past a fixed 0.008), capped so distinct rows never fuse
    gaps = [
        abs(items[i]["y"] - items[i + 1]["y"]) for i in range(len(items) - 1)
    ]
    gaps = [g for g in gaps if g > 0.002]
    if gaps:
        gaps.sort()
        pitch_est = gaps[len(gaps) // 2]
        merge_win = max(0.008, min(0.018, 0.55 * pitch_est))
    else:
        merge_win = 0.008
    merged = []
    for it in items:
        if merged and abs(merged[-1]["y"] - it["y"]) < merge_win:
            frags = sorted(
                [
                    (merged[-1]["x0"], merged[-1]["desc"]),
                    (it["x0"], it["desc"]),
                ]
            )
            merged[-1]["desc"] = _merge_desc(frags[0][1], frags[1][1])
            merged[-1]["x0"] = frags[0][0]
            merged[-1]["y"] = (merged[-1]["y"] + it["y"]) / 2
        else:
            merged.append(it)
    items = merged

    # attach shed size suffixes to their rows (same window, x-affinity was
    # established at classification)
    for yc, x0, sfx in suffix_frags:
        if not items:
            break
        row = min(items, key=lambda it: abs(it["y"] - yc))
        if abs(row["y"] - yc) <= max(merge_win, 0.012):
            row["desc"] = _merge_desc(row["desc"], sfx)

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

    # Arithmetic summary reconciliation: Sub Total and the tender amount
    # anchor the block (both parse clean on every dev receipt). A damaged
    # summary amount that lost its leading digit keeps its cents, and the
    # identities TAX = tender - subtotal / Total = tender pin the true
    # value -- repair only when BOTH the cents match and the damaged value
    # is smaller (a lost leading digit can only shrink the number).
    def _cents(v):
        return v.split(".")[-1] if v and "." in v else None

    sub_amt = next(
        (
            r["amt"]
            for r in summary_rows
            if re.match(r"^SUB ?TOTAL", r["label"].upper()) and r["amt"]
        ),
        None,
    )
    tender_amt = next(
        (
            r["amt"]
            for r in summary_rows
            if re.match(
                r"^(AMERICAN|EXPRES|VISA|MASTERCARD|DEBIT|CASH)",
                r["label"].upper(),
            )
            and r["amt"]
        ),
        None,
    )
    if sub_amt and tender_amt:
        try:
            expected = {
                r"^(SALES( TAX)?|TAX)\b": _money(
                    float(tender_amt) - float(sub_amt)
                ),
                r"^TOTAL\b": tender_amt,
            }
            for r in summary_rows:
                for pat, want in expected.items():
                    if (
                        re.match(pat, r["label"].upper())
                        and r["amt"]
                        and _cents(r["amt"]) == _cents(want)
                        and float(r["amt"]) < float(want)
                    ):
                        r["amt"] = want
        except ValueError:
            pass

    # Modal-price reconciliation: a shattered fragment that LOST its leading
    # digit ("​.25" from "1.25") keeps the true cents. When the receipt has a
    # modal CLEAN price and a damaged amount is smaller but shares its cents,
    # the damaged value is that modal price (evidence-based repair, bounded
    # to non-clean fragments; distinct-cents amounts are never touched).
    clean_vals = [it["price"] for it in items if it["_pr_clean"]] + [
        it["total"] for it in items if it["_tot_clean"]
    ]
    if clean_vals:
        from collections import Counter

        modal = Counter(clean_vals).most_common(1)[0][0]
        modal_cents = modal.split(".")[1]
        for it in items:
            for key, clean_key in (
                ("price", "_pr_clean"),
                ("total", "_tot_clean"),
            ):
                val = it.get(key)
                if (
                    val
                    and not it.get(clean_key)
                    and val.split(".")[-1] == modal_cents
                    and float(val) < float(modal)
                ):
                    it[key] = modal

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

    # Amount fill for SAME-PRODUCT siblings: an item row whose amount
    # fragment strayed beyond the attachment window is still a real row --
    # when >=2 clean siblings of the SAME product (leading-token identity)
    # share one modal (price, total, flag), the missing amounts are that
    # modal (evidence-based; a lone unmatched fragment never invents one).
    def _prod_key(it):
        return " ".join(it["desc"].split()[:2]).upper()

    by_prod = {}
    for it in items:
        by_prod.setdefault(_prod_key(it), []).append(it)
    for group in by_prod.values():
        with_amounts = [
            (g["price"], g["total"], g["flag"])
            for g in group
            if g["price"] and g["total"]
        ]
        if len(with_amounts) < 2:
            continue
        modal_amt = (
            __import__("collections").Counter(with_amounts).most_common(1)[0]
        )
        if modal_amt[1] < 2:
            continue
        price, total, flag = modal_amt[0]
        for g in group:
            if not g["price"] and not g["total"]:
                g["price"], g["total"], g["flag"] = price, total, flag

    # a fragment with no amounts continues the item above it
    kept = []
    for it in items:
        if it["price"] or it["total"]:
            kept.append(it)
        elif kept and abs(kept[-1]["y"] - it["y"]) < 0.06:
            kept[-1]["desc"] += " " + it["desc"]
        # else: isolated junk fragment, dropped
    items = kept

    # Duplicate-item TEXT canonicalization (extends the modal-cents price
    # reconciler to text): rows already matched as the SAME product -- same
    # amounts and shared leading tokens -- vote per token position, so a
    # clipped "11X10." inherits "11X10.5" from the clean instances.
    # Receipt-derived truth only; ties prefer the longer token (clipping
    # loses characters, never adds them).
    from collections import Counter as _Counter

    groups = {}
    for it in items:
        toks = it["desc"].split()
        key = (
            it["price"],
            it["total"],
            " ".join(toks[:2]).upper(),
        )
        groups.setdefault(key, []).append(it)
    for group in groups.values():
        if len(group) < 2:
            continue
        maxlen = max(len(g["desc"].split()) for g in group)
        for g in group:
            toks = g["desc"].split()
            out_toks = list(toks)
            for pos in range(min(len(toks), maxlen)):
                candidates = [
                    o["desc"].split()[pos]
                    for o in group
                    if len(o["desc"].split()) > pos
                ]
                cur = toks[pos]
                # override ONLY within the clip-variant family (prefix
                # relation): clipping can only LOSE characters, so the
                # LONGEST variant is the least-clipped truth even when the
                # clipped form is the majority (7x "11X10." vs 3x
                # "11X10.5"). Genuinely different tokens ("SOAP" vs
                # "TOOTHBRUSH") keep their own row identity.
                family = [
                    t
                    for t in candidates
                    if t == cur or t.startswith(cur) or cur.startswith(t)
                ]
                if family:
                    best = max(family, key=len)
                    if best != cur:
                        out_toks[pos] = best
            # a shorter clip also inherits missing TRAILING tokens when
            # every longer sibling agrees on them
            if len(toks) < maxlen:
                tails = {
                    " ".join(o["desc"].split()[len(toks) :])
                    for o in group
                    if len(o["desc"].split()) == maxlen
                }
                if len(tails) == 1:
                    tail = tails.pop()
                    last = toks[-1] if toks else ""
                    first_tail = tail.split()[0] if tail else ""
                    if first_tail.startswith(last):
                        out_toks = out_toks[:-1] + tail.split()
            g["desc"] = " ".join(out_toks)

    # ---- emit (structure per the receiptfont canonical template) -----------
    # Split the footer: the register line (digits + date/time) and the Sales
    # Associate line print at the BOTTOM, after the policy box; everything
    # else is centered narration between the double rules.
    # reassemble the shattered register fields: group by y, join in x order
    register_frags.sort(key=lambda f: (-f[0], f[1]))
    reg_lines = []
    for yc, x0, text in register_frags:
        if reg_lines and abs(reg_lines[-1][0] - yc) < 0.014:
            reg_lines[-1] = (
                (reg_lines[-1][0] + yc) / 2,
                reg_lines[-1][1] + [(x0, text)],
            )
        else:
            reg_lines.append((yc, [(x0, text)]))
    register_rows = [
        " ".join(t for _, t in sorted(frags)) for _, frags in reg_lines
    ]
    register_rows += [
        t
        for t in footer_rows
        if re.match(r"^\d{2,4} \d{3,5}", t.strip()) or "SOCIATE" in t.upper()
    ]
    narration_rows = [t for t in footer_rows if t not in register_rows]
    return _emit_layout(
        wordmark_row,
        header_rows,
        items,
        summary_rows,
        payment_rows,
        narration_rows,
        register_rows,
    )


def _emit_layout(
    wordmark_row,
    header_rows,
    items,
    summary_rows,
    payment_rows,
    narration_rows,
    register_rows,
):
    """Emit the measured canonical Dollar Tree layout for given content.

    Shared by the OCR-repair path (canonical_words) and the GENERATIVE
    path (generate_receipt): every layout rule lives here once.
    """
    POLICY_LINES = (
        "We will gladly exchange any unopened item",
        "with original receipt. We do not offer refunds.",
    )
    n_rows = (
        2
        + len(header_rows)
        + 1
        + len(items)
        + len(summary_rows)
        + len(payment_rows)
        + len(narration_rows)
        + len(register_rows)
        + len(POLICY_LINES)
        + 9  # separator rules + policy borders + block gaps
    )
    # FIXED pitch: real receipts keep constant line height and grow the
    # paper. Content is later mapped linearly into the canvas, preserving
    # proportions; the CALLER picks a canvas aspect that matches.
    pitch = ROW_PITCH
    cap = CAP_H
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

    rule_len = int(0.96 / CHAR_W)

    def rule(ch):
        nonlocal y
        put(ch * rule_len, x_left=0.02, cap_h=cap * 0.6)
        y -= pitch * 0.8

    # Full-width wordmark band (template: tree emblem + DOLLAR TREE(R)
    # spanning the paper). The phrase logo anchor sizes the pasted logo to
    # this band and depicts these tokens.
    n_wm = len(wordmark_row.replace(" ", ""))
    wm_char = 0.92 / max(1, n_wm + wordmark_row.count(" "))
    xw = 0.03
    for tok in wordmark_row.split():
        wpx = len(tok) * wm_char * 1000
        words.append(
            {
                "text": tok,
                "bbox": [xw * 1000, y, xw * 1000 + wpx, y + cap * 2.6],
                "labels": ["MERCHANT_NAME"],
                "line_id": len(words) + 1,
                "word_id": 1,
            }
        )
        xw += (len(tok) + 1) * wm_char
    y -= pitch * 2.8
    for text in header_rows:
        put_tokens(text, 0.01, labels=["ADDRESS_LINE"])
        y -= pitch
    rule("=")
    rule("-")
    put("DESCRIPTION", x_left=0.0)
    put("QTY", right=QTY_RIGHT)
    put("PRICE", right=PRICE_RIGHT)
    put("TOTAL", right=TOTAL_RIGHT)
    y -= pitch
    rule("-")

    def _word_trunc(desc):
        """Truncate at a WORD boundary so a trailing token is kept whole or
        dropped whole ("GRHMT 9X8" never becomes "GRHMT 9")."""
        out, used = [], 0
        for tok in desc.split():
            need = len(tok) + (1 if out else 0)
            if used + need > max_desc_chars:
                break
            out.append(tok)
            used += need
        return " ".join(out) if out else desc[:max_desc_chars]

    for it in items:
        put_tokens(_word_trunc(it["desc"]), DESC_X, labels=["PRODUCT_NAME"])
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
            put(f"${r['amt']}", right=AMOUNT_RIGHT)
        y -= pitch
    for text in payment_rows:
        put_tokens(text, LABEL_X)
        y -= pitch
    rule("=")
    for text in narration_rows:
        put_tokens(text, 0.5 - len(text) * CHAR_W / 2)
        y -= pitch
    # asterisk-bordered exchange-policy box (canonical DT boilerplate; the
    # photo OCR loses it, the template pins it)
    rule("*")
    for line in POLICY_LINES:
        put("*", x_left=0.02)
        put_tokens(line, 0.5 - len(line) * CHAR_W / 2)
        put("*", right=0.98)
        y -= pitch
    rule("*")
    for text in register_rows:
        put_tokens(text, 0.01)
        y -= pitch
    rule("=")
    # Post-emit guarantee: whatever the row accounting, nothing may land
    # below the canvas. If the layout overran, compress all y linearly into
    # the paper band (heights scale with it, keeping proportions).
    ys = [w["bbox"][1] for w in words] + [w["bbox"][3] for w in words]
    ymin, ymax = min(ys), max(ys)
    # ALWAYS map content linearly into the canvas band: proportions are
    # invariant, and the canvas aspect (chosen by the caller) sets the
    # physical pitch -- exactly how a fixed-pitch printer + variable paper
    # length behave.
    scale_y = (985.0 - 15.0) / max(1.0, ymax - ymin)
    for w in words:
        b = w["bbox"]
        b[1] = 15.0 + (b[1] - ymin) * scale_y
        b[3] = 15.0 + (b[3] - ymin) * scale_y
    return words


FIXTURE_PATH = os.path.join(HERE, "fixtures", "dollartree_template.json")


def load_fixture():
    import json

    with open(FIXTURE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def generate_receipt(
    items,
    *,
    store_lines=None,
    tax_rate=0.06,
    tender_label="FIFTHTHIRD DEBIT",
    payment_lines=None,
    narration_lines=None,
    register_line=None,
    associate_line=None,
):
    """GENERATIVE path: canonical DT receipt words for an arbitrary item list.

    ``items``: [{"name", "qty", "price", "taxable"}...]. Each unit prints its
    own qty-1 line (the template convention); arithmetic is correct by
    construction (subtotal = sum, tax = rate x taxable subtotal, total =
    tender). Layout comes from the SAME measured _emit_layout the OCR-repair
    path uses, so every rule is shared.
    """
    fx = load_fixture()
    if store_lines is None:
        store_lines = fx["store"]["lines"]
    if payment_lines is None:
        payment_lines = fx["payment_lines"]
    if narration_lines is None:
        narration_lines = fx["narration_lines"]
    if register_line is None:
        register_line = fx["register_line"]
    if associate_line is None:
        associate_line = fx["associate_line"]

    emit_items = []
    subtotal = 0.0
    taxable_subtotal = 0.0
    for spec in items:
        qty = int(spec.get("qty", 1))
        price = float(spec["price"])
        taxable = bool(spec.get("taxable", True))
        for _ in range(qty):
            emit_items.append(
                {
                    "desc": str(spec["name"]),
                    "qty": "1",
                    "price": _money(price),
                    "total": _money(price),
                    "flag": taxable,
                }
            )
            subtotal += price
            if taxable:
                taxable_subtotal += price
    from decimal import ROUND_HALF_UP, Decimal

    tax = float(
        (Decimal(str(taxable_subtotal)) * Decimal(str(tax_rate))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )
    total = float(
        (Decimal(str(subtotal)) + Decimal(str(tax))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )
    summary_rows = [
        {"label": "Sub Total", "amt": _money(subtotal), "y": 0.0},
        {"label": "SALES TAX", "amt": _money(tax), "y": 0.0},
        {"label": "Total", "amt": _money(total), "y": 0.0},
        {"label": tender_label, "amt": _money(total), "y": 0.0},
    ]
    words = _emit_layout(
        "DOLLAR TREE",
        store_lines,
        emit_items,
        summary_rows,
        payment_lines,
        narration_lines,
        [register_line, associate_line],
    )
    return words


def render_generated(items, out_png, **kwargs):
    """Render a generated DT receipt through the production hybrid path."""
    import render_synthetic_receipts as rsr

    table = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    region = os.environ.get("AWS_REGION", "us-east-1")
    words = generate_receipt(items, **kwargs)
    receipt = {
        "words": words,
        "merchant_name": MERCHANT,
        "_composed": True,  # already canonical; the render hook must not re-parse
    }
    prof = rsr.cached_font_profile(
        table, MERCHANT, region=region, max_receipts=12
    )
    ss = rsr.section_scale_for_merchant(MERCHANT)
    typ = rsr.merchant_typography(MERCHANT)
    wt = 576
    # normalized pitch between consecutive item rows -> canvas height that
    # reproduces the template's measured pitch (17.4px/row at 426px wide)
    row_ys = sorted({round(w["bbox"][1], 1) for w in words}, reverse=True)
    gaps = [row_ys[i] - row_ys[i + 1] for i in range(len(row_ys) - 1)]
    gaps = sorted(g for g in gaps if g > 4)
    d_norm = gaps[len(gaps) // 2] if gaps else 25.0
    ht = int(round(0.0408 * wt * 1000.0 / d_norm))
    ht = max(700, min(3200, ht))
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
    return out_png


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
