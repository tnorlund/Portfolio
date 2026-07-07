#!/usr/bin/env python3
"""backfill_reverse_ocr.py — recover WHITE-ON-BLACK knockout text Vision OCR missed.

THE PROBLEM
-----------
Apple Vision OCR (like most text detectors) reads dark ink on a light
background. Costco receipts print two important fields in *reverse video*
(a.k.a. knockout): white glyphs on a solid black bar. Vision silently drops
these, so DynamoDB is missing:

  * the grand TOTAL amount — the black bar on the ``**** TOTAL`` row, and
  * (on some layouts) the date box printed below ``TOTAL NUMBER OF ITEMS SOLD``.

The fix is anchor-guided reverse OCR: find the surviving OCR words that bracket
the missing field, crop the field generously, invert it (``255 - gray``) so the
knockout becomes ordinary dark-on-light text, upscale 3x, and re-run Vision.
The recovered token is placed back into the receipt using the box Vision
returns for it (mapped from the crop into full-image normalized coordinates),
so geometry is correct even on rotated receipts.

VALIDATION GATES (a value is written only if it passes)
-------------------------------------------------------
  * Grand total: the existing OCR SUBTOTAL + TAX must equal the recovered
    amount within $0.01. If SUBTOTAL/TAX are unavailable, the recovered amount
    must equal the largest plausible payment AMOUNT already in the words.
  * Date: must parse as a real MM/DD/YYYY; if the receipt already has another
    date, they must agree.
Anything that does not pass is reported as ``recovered_unvalidated`` and is
NOT written.

COORDINATE SYSTEMS
------------------
Stored word coords are normalized 0-1, y-UP (top edge has the LARGER y):
    pixel_x = norm_x * W ;  pixel_y = (1 - norm_y) * H
Apple Vision crop coords are normalized 0-1, y-UP, origin bottom-left:
    crop_px_left = x * cw ; crop_px_top = (1-(y+h)) * ch (y-down within crop)

USAGE
-----
    python backfill_reverse_ocr.py --keys costco_keys.json --dry-run
    python backfill_reverse_ocr.py --keys costco_keys.json --apply

Env: DYNAMODB_TABLE_NAME (default ReceiptsTable-dc5be22), AWS_REGION
(default us-east-1). Requires a Mac (Apple Vision) and the receipt_dynamo /
receipt_upload packages on PYTHONPATH.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
import tempfile
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import numpy as np
from PIL import Image

# --- package path bootstrap (mirror receipt_line_scorecard) ---------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in ("receipt_dynamo", "receipt_upload", "receipt_agent", "scripts"):
    _full = os.path.join(_ROOT, _p)
    if _full not in sys.path:
        sys.path.insert(0, _full)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import boto3  # noqa: E402
from receipt_upload.ocr import apple_vision_ocr  # noqa: E402

from receipt_dynamo.data.dynamo_client import DynamoClient  # noqa: E402
from receipt_dynamo.entities.receipt_word import ReceiptWord  # noqa: E402
from receipt_dynamo.entities.receipt_word_label import (  # noqa: E402
    ReceiptWordLabel,
)

TABLE = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
REGION = os.environ.get("AWS_REGION", "us-east-1")
AMOUNT_RE = re.compile(r"^\$?(\d{1,4}\.\d{2})$")
DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")
PROPOSED_BY = "reverse-ocr-backfill"
UPSCALE = 3
DARK_THRESH_ABS = 150  # 0-255 gray upper bound for "ink" on a knockout bar
DARK_FRAC_MIN = 0.35  # column dark fraction to be considered a knockout bar


# =========================================================================
# geometry helpers (word = ReceiptWord entity; coords normalized y-up)
# =========================================================================
def _corners_px(w: ReceiptWord, W: int, H: int) -> list[tuple[float, float]]:
    return [
        (c["x"] * W, (1 - c["y"]) * H)
        for c in (w.top_left, w.top_right, w.bottom_left, w.bottom_right)
    ]


def _centroid_px(w: ReceiptWord, W: int, H: int) -> tuple[float, float]:
    pts = _corners_px(w, W, H)
    return (sum(p[0] for p in pts) / 4, sum(p[1] for p in pts) / 4)


def _right_px(w: ReceiptWord, W: int) -> float:
    return max(w.top_right["x"], w.bottom_right["x"]) * W


def _height_px(w: ReceiptWord, W: int, H: int) -> float:
    tl = (w.top_left["x"] * W, (1 - w.top_left["y"]) * H)
    bl = (w.bottom_left["x"] * W, (1 - w.bottom_left["y"]) * H)
    return math.hypot(tl[0] - bl[0], tl[1] - bl[1])


def _median_height(words: list[ReceiptWord], W: int, H: int) -> float:
    hs = [_height_px(w, W, H) for w in words]
    hs = [h for h in hs if h > 1]
    return statistics.median(hs) if hs else 20.0


def _amount(text: str) -> float | None:
    m = AMOUNT_RE.match(text.strip())
    return float(m.group(1)) if m else None


def _line_text(words: list[ReceiptWord], line_id: int) -> str:
    lw = sorted(
        (w for w in words if w.line_id == line_id),
        key=lambda w: w.top_left["x"],
    )
    return " ".join(w.text for w in lw)


# =========================================================================
# loading
# =========================================================================
def load_receipt(client, s3, image_id: str, receipt_id: int):
    details = client.get_image_details(image_id)
    receipt = next(
        (r for r in details.receipts if str(r.receipt_id) == str(receipt_id)),
        None,
    )
    if receipt is None:
        raise RuntimeError(f"receipt {receipt_id} not found for {image_id}")
    words = [
        w
        for w in details.receipt_words
        if str(w.receipt_id) == str(receipt_id)
    ]
    labels = [
        l
        for l in details.receipt_word_labels
        if str(l.receipt_id) == str(receipt_id)
    ]
    im = None
    for bucket, key in (
        (receipt.cdn_s3_bucket, receipt.cdn_s3_key),
        (receipt.raw_s3_bucket, receipt.raw_s3_key),
    ):
        if not bucket or not key:
            continue
        try:
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            im = Image.open(BytesIO(body)).convert("RGB")
            break
        except Exception:  # noqa: BLE001
            continue
    if im is None:
        raise RuntimeError(f"could not load image for {image_id}:{receipt_id}")
    return receipt, im, words, labels


# =========================================================================
# amount association: value to the right of a label word, same visual row
# =========================================================================
def _amount_right_of(
    label_word: ReceiptWord,
    words: list[ReceiptWord],
    W: int,
    H: int,
    mh: float,
) -> tuple[float, ReceiptWord] | None:
    """Amount token on the same visual row, to the right of the label.

    Row spacing is ~1 median glyph-height, so the vertical window is kept
    below that (0.7*mh) and the vertically-closest candidate wins — this
    rejects the amounts one row above/below even though the receipt is
    slightly rotated (the amount drifts only a fraction of a row off its
    label's baseline).
    """
    lx, ly = _centroid_px(label_word, W, H)
    best = None  # (val, word, abs_dy)
    for w in words:
        val = _amount(w.text)
        if val is None:
            continue
        wx, wy = _centroid_px(w, W, H)
        if wx <= lx:
            continue
        dy = abs(wy - ly)
        if dy <= 0.55 * mh and (best is None or dy < best[2]):
            best = (val, w, dy)
    return (best[0], best[1]) if best else None


def _find_labeled_amount(
    words: list[ReceiptWord], keyword: str, W: int, H: int, mh: float
) -> tuple[float, ReceiptWord] | None:
    for w in words:
        if w.text.strip().upper() == keyword:
            got = _amount_right_of(w, words, W, H, mh)
            if got:
                return got
    return None


# =========================================================================
# reverse-OCR of a dark (knockout) bar
# =========================================================================
def _ocr_crop(im: Image.Image, box, invert: bool):
    x0, y0, x1, y1 = (int(v) for v in box)
    crop = im.crop((x0, y0, x1, y1)).convert("L")
    cw, ch = crop.size
    if cw < 4 or ch < 4:
        return []
    up = crop.resize((cw * UPSCALE, ch * UPSCALE), Image.LANCZOS)
    if invert:
        up = Image.fromarray(255 - np.array(up))
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "crop.png")
        up.save(p)
        res = apple_vision_ocr([p])
    out = []
    for _uid, payload in res.items():
        for w in payload[1]:
            bb = w.bounding_box
            fl = x0 + bb["x"] * cw
            fr = x0 + (bb["x"] + bb["width"]) * cw
            ftop = y0 + (1 - (bb["y"] + bb["height"])) * ch
            fbot = y0 + (1 - bb["y"]) * ch
            out.append(
                {
                    "text": w.text,
                    "conf": float(w.confidence),
                    "box": (fl, ftop, fr, fbot),
                }
            )
    return out


def _darkbar_box(
    im: Image.Image, cx: float, cy: float, mh: float, x_start: float, W: int
):
    """Locate the contiguous knockout bar right of x_start on the row at cy.

    The bar is detected *relative* to the row's own paper background rather
    than by an absolute gray value, because faded / shadowed knockout bars
    print as medium gray, not pure black.
    """
    gray = np.array(im.convert("L"))
    band = int(0.8 * mh)
    y0 = max(0, int(cy - band))
    y1 = min(gray.shape[0], int(cy + band))
    xs = max(0, int(x_start))
    row = gray[y0:y1, xs:]
    if row.size == 0:
        return None
    bg = float(np.percentile(row, 90))  # paper background of this row
    thresh = min(DARK_THRESH_ABS, bg - 60)  # ink is well below paper
    coldark = (row < thresh).mean(axis=0)
    dark_cols = np.where(coldark > DARK_FRAC_MIN)[0]
    if len(dark_cols) < mh * 0.5:  # need a bar at least ~half a glyph wide
        return None
    pad = int(0.35 * mh)
    bx0 = xs + int(dark_cols.min()) - pad
    bx1 = xs + int(dark_cols.max()) + pad
    by0 = int(cy - 0.8 * mh)
    by1 = int(cy + 0.8 * mh)
    return (max(0, bx0), max(0, by0), min(W, bx1), by1)


# =========================================================================
# grand-total recovery
# =========================================================================
def _grand_total_anchor(words: list[ReceiptWord]) -> ReceiptWord | None:
    """The 'TOTAL' word on the grand-total row (``**** TOTAL``).

    The grand-total row's only alphabetic content is TOTAL (leading asterisks
    and the amount, if any, carry no letters). This rejects ``TOTAL NUMBER OF
    ITEMS SOLD``, ``SUBTOTAL``, ``TOTAL TAX``, ``TOTAL TENDER``, etc.
    """
    cands = []
    for w in words:
        if w.text.strip().upper() != "TOTAL":
            continue
        letters = re.sub(r"[^A-Z]", "", _line_text(words, w.line_id).upper())
        if letters != "TOTAL":
            continue
        cands.append(w)
    if not cands:
        return None
    # prefer the grand total sitting just below the summary TAX line
    tax_words = [w for w in words if w.text.strip().upper() == "TAX"]
    if not tax_words:
        return cands[0]
    return min(
        cands,
        key=lambda c: min(
            abs(c.top_left["y"] - t.top_left["y"]) for t in tax_words
        ),
    )


def _match_expected_digits(text: str, box, expected: float):
    """If the expected amount's digit sequence appears in ``text``, return the
    token box trimmed to that character span, else None.

    Reverse OCR of a knockout bar reliably recovers the *digits* but often
    mangles the decimal point (``.`` -> ``-``/``:``) and prepends/appends a
    stray digit from the bar's rounded ends. Matching the exact expected
    digit string (e.g. ``9341`` for ``93.41``) inside the recovered digits is
    both tolerant of that noise and specific enough that a coincidental match
    is implausible.
    """
    want = re.sub(r"\D", "", f"{expected:.2f}")  # e.g. "9341"
    if len(want) < 3:
        return None
    digits = [(i, c) for i, c in enumerate(text) if c.isdigit()]
    dstr = "".join(c for _, c in digits)
    pos = dstr.find(want)
    if pos < 0:
        return None
    start_char = digits[pos][0]
    end_char = digits[pos + len(want) - 1][0]
    n = len(text)
    fl, ftop, fr, fbot = box
    span = fr - fl
    tl = fl + span * (start_char / n)
    tr = fl + span * ((end_char + 1) / n)
    return (tl, ftop, tr, fbot)


def _row_amount_token(anchor, words, W, H, mh):
    """The word occupying the amount column on the grand-total row.

    This is the rightmost digit-bearing token on the anchor's visual row. It
    may be (a) a clean, correct amount (already present), (b) a clean amount
    that disagrees with SUBTOTAL+TAX, or (c) a garbled knockout read such as
    ``957944``. Cases (b) and (c) are the words this backfill should UPDATE in
    place rather than duplicate.
    """
    ax, ay = _centroid_px(anchor, W, H)
    cands = []
    for w in words:
        if w is anchor:
            continue
        wx, wy = _centroid_px(w, W, H)
        if wx <= ax or abs(wy - ay) > 0.55 * mh:
            continue
        ndig = sum(c.isdigit() for c in w.text)
        if ndig >= 3 or _amount(w.text) is not None:
            cands.append((wx, w))
    if not cands:
        return None
    return max(cands, key=lambda c: c[0])[1]


def recover_grand_total(im, words, W, H, mh):
    """Return dict describing the grand-total recovery attempt (or None)."""
    anchor = _grand_total_anchor(words)
    if anchor is None:
        return None
    cx, cy = _centroid_px(anchor, W, H)
    x_start = _right_px(anchor, W) + 3

    max_r = max(_right_px(w, W) for w in words)
    dark_box = _darkbar_box(im, cx, cy, mh, x_start, W)
    dark_present = dark_box is not None
    # The amount column sits at the right of the row. Try the tight knockout
    # bar (if detected) and always the full right-of-anchor band, both
    # inverted (knockout) and un-inverted (outline-box). Collect clean amount
    # tokens AND every raw token (for digit-sequence matching). Validation,
    # not detection, is the gate.
    right_band = (
        int(x_start),
        int(cy - 0.8 * mh),
        int(min(W, max_r + mh)),
        int(cy + 0.8 * mh),
    )
    attempts = []
    if dark_box is not None:
        attempts.append((dark_box, True))
    attempts.append((right_band, True))
    attempts.append((right_band, False))
    amounts = []  # (value, token) for clean X.XX tokens
    raw = []  # every recovered token
    for box, inv in attempts:
        for r in _ocr_crop(im, box, invert=inv):
            raw.append(r)
            m = AMOUNT_RE.match(r["text"].strip())
            if m:
                amounts.append((float(m.group(1)), r))

    # validation inputs
    sub = _find_labeled_amount(words, "SUBTOTAL", W, H, mh)
    tax = _find_labeled_amount(words, "TAX", W, H, mh)
    expected = None
    basis = None
    if sub and tax:
        expected = round(sub[0] + tax[0], 2)
        basis = f"SUBTOTAL {sub[0]:.2f} + TAX {tax[0]:.2f}"

    # The token currently occupying the amount column of the TOTAL row (if
    # any). If it is a clean amount that agrees with SUBTOTAL+TAX the total is
    # already correct; otherwise it is garbled/implausible and is the target
    # to UPDATE in place (rather than adding a duplicate word).
    token = _row_amount_token(anchor, words, W, H, mh)
    token_val = _amount(token.text) if token is not None else None

    result = {
        "field": "GRAND_TOTAL",
        "anchor_line": anchor.line_id,
        "dark_bar": dark_present,
        "existing": token.text if token is not None else None,
        "candidates": sorted({f"{v:.2f}" for v, _ in amounts}),
        "raw": sorted({r["text"] for r in raw}),
        "expected": f"{expected:.2f}" if expected is not None else None,
        "basis": basis,
    }

    token_ok = token_val is not None and (
        expected is None or abs(token_val - expected) <= 0.01
    )
    if token_ok:
        result["verdict"] = "skip_present"
        result["recovered"] = token.text
        return result

    # choose recovered value + box
    chosen = None  # (value, box, conf)
    if expected is not None:
        # 1) a clean token that equals the expected total (best box)
        for v, r in amounts:
            if abs(v - expected) <= 0.01:
                chosen = (v, r["box"], r["conf"])
                break
        # 2) else the expected digit sequence embedded in a noisy token
        if chosen is None:
            for r in raw:
                tb = _match_expected_digits(r["text"], r["box"], expected)
                if tb is not None:
                    chosen = (expected, tb, r["conf"])
                    result["basis"] = basis + " [digit-match]"
                    break
    elif amounts:
        # no SUBTOTAL/TAX available: largest clean amount, reported UNVALIDATED
        v, r = max(amounts, key=lambda a: a[0])
        chosen = (v, r["box"], r["conf"])
        result["basis"] = "largest recovered amount (no SUBTOTAL/TAX)"

    if chosen is None:
        result["verdict"] = "no_candidate"
        result["recovered"] = None
        return result

    v, box, conf = chosen
    result["recovered"] = f"{v:.2f}"
    result["box"] = box
    result["conf"] = conf
    if token is not None:
        # replace the garbled/implausible existing amount word in place
        result["action"] = "update"
        result["line_id"] = token.line_id
        result["word_id"] = token.word_id
        result["replaces"] = token.text
    else:
        result["action"] = "add"
        result["line_id"] = anchor.line_id
    if expected is not None and abs(v - expected) <= 0.01:
        result["verdict"] = "validated"
    else:
        result["verdict"] = "recovered_unvalidated"
    return result


# =========================================================================
# date recovery (row below TOTAL NUMBER OF ITEMS SOLD)
# =========================================================================
def _items_sold_anchors(words: list[ReceiptWord]) -> list[ReceiptWord]:
    out = []
    for w in words:
        if w.text.strip().upper() == "SOLD":
            lt = _line_text(words, w.line_id).upper()
            if "ITEMS" in lt:
                out.append(w)
    return out


def recover_date(im, words, W, H, mh):
    anchors = _items_sold_anchors(words)
    if not anchors:
        return None
    existing_dates = sorted(
        {m.group(1) for w in words if (m := DATE_RE.search(w.text))}
    )
    result = {
        "field": "DATE",
        "anchor_line": anchors[0].line_id,
        "existing_dates": existing_dates,
        "candidates": [],
    }

    for anchor in anchors:
        ax, ay = _centroid_px(anchor, W, H)
        row_cy = ay + 1.4 * mh  # the row directly below the anchor

        # already covered by existing OCR words on that row -> skip anchor
        row_words = [
            w
            for w in words
            if abs(_centroid_px(w, W, H)[1] - row_cy) <= 0.9 * mh
            and DATE_RE.search(w.text)
        ]
        if row_words:
            result["verdict"] = "skip_present"
            result["recovered"] = DATE_RE.search(row_words[0].text).group(1)
            return result

        box = (
            int(W * 0.03),
            int(row_cy - 0.8 * mh),
            int(W * 0.97),
            int(row_cy + 0.8 * mh),
        )
        # outline-box dates print dark-on-white (normal); knockout inverts
        found = []
        for inv in (False, True):
            for r in _ocr_crop(im, box, invert=inv):
                m = DATE_RE.search(r["text"])
                if m:
                    found.append((m.group(1), r))
        result["candidates"].extend(sorted({f for f, _ in found}))
        if not found:
            continue

        cand, r = found[0]
        try:
            datetime.strptime(cand, "%m/%d/%Y")
            valid_date = True
        except ValueError:
            valid_date = False
        result["recovered"] = cand
        result["box"] = r["box"]
        result["conf"] = r["conf"]
        if cand in existing_dates:
            # already captured elsewhere in the receipt -> not missing; do not
            # write a duplicate word for it.
            result["verdict"] = "skip_present"
            return result
        if not valid_date or existing_dates:
            # invalid, or the receipt already carries a *different* date it
            # would contradict -> hold for human review, do not write.
            result["verdict"] = "recovered_unvalidated"
            return result
        result["verdict"] = "validated"
        below = [w for w in words if _centroid_px(w, W, H)[1] > ay + 0.5 * mh]
        if below:
            result["line_id"] = min(
                below, key=lambda w: _centroid_px(w, W, H)[1]
            ).line_id
        else:
            result["line_id"] = max((w.line_id for w in words), default=0) + 1
        return result

    result.setdefault("verdict", "no_candidate")
    result.setdefault("recovered", None)
    return result


# =========================================================================
# entity construction + write
# =========================================================================
def _box_to_norm(box, W, H):
    fl, ftop, fr, fbot = box
    nx_l, nx_r = fl / W, fr / W
    ny_top, ny_bot = 1 - ftop / H, 1 - fbot / H  # top larger
    return {
        "top_left": {"x": nx_l, "y": ny_top},
        "top_right": {"x": nx_r, "y": ny_top},
        "bottom_left": {"x": nx_l, "y": ny_bot},
        "bottom_right": {"x": nx_r, "y": ny_bot},
        "bounding_box": {
            "x": nx_l,
            "y": ny_bot,
            "width": nx_r - nx_l,
            "height": ny_top - ny_bot,
        },
    }


def build_word(image_id, receipt_id, line_id, word_id, text, box, conf, W, H):
    geom = _box_to_norm(box, W, H)
    return ReceiptWord(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        text=text,
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=max(0.01, min(1.0, conf)),
        **geom,
    )


def _next_word_id(words, line_id):
    ids = [w.word_id for w in words if w.line_id == line_id]
    return (max(ids) + 1) if ids else 1


# =========================================================================
# driver
# =========================================================================
def process(client, s3, image_id, receipt_id, apply: bool):
    receipt, im, words, labels = load_receipt(client, s3, image_id, receipt_id)
    W, H = im.size
    mh = _median_height(words, W, H)
    rows = []
    for rec in (
        recover_grand_total(im, words, W, H, mh),
        recover_date(im, words, W, H, mh),
    ):
        if rec is None:
            continue
        rec["image_id"] = image_id
        rec["receipt_id"] = receipt_id
        rows.append(rec)

    if apply:
        existing_labels = {(l.line_id, l.word_id, l.label) for l in labels}
        for rec in rows:
            if rec.get("verdict") != "validated" or "box" not in rec:
                continue
            action = rec.get("action", "add")
            if action == "update":
                line_id, wid = rec["line_id"], rec["word_id"]
                target = next(
                    (
                        w
                        for w in words
                        if w.line_id == line_id and w.word_id == wid
                    ),
                    None,
                )
                if target is None:
                    rec["apply_error"] = "target word vanished"
                    continue
                # replace the garbled text in place; its geometry already sits
                # on the amount; mark embedding stale so it re-embeds.
                target.text = rec["recovered"]
                target.embedding_status = "NONE"
                client.update_receipt_word(target)
                rec["written_word"] = (line_id, wid)
            else:
                line_id = rec["line_id"]
                wid = _next_word_id(words, line_id)
                word = build_word(
                    image_id,
                    receipt_id,
                    line_id,
                    wid,
                    rec["recovered"],
                    rec["box"],
                    rec.get("conf", 0.9),
                    W,
                    H,
                )
                client.add_receipt_word(word)
                rec["written_word"] = (line_id, wid)

            if (
                rec["field"] == "GRAND_TOTAL"
                and (line_id, wid, "GRAND_TOTAL") not in existing_labels
            ):
                lbl = ReceiptWordLabel(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    line_id=line_id,
                    word_id=wid,
                    label="GRAND_TOTAL",
                    reasoning=(
                        "Recovered white-on-black knockout TOTAL via reverse "
                        f"OCR; validated {rec.get('basis')} == "
                        f"{rec['recovered']} ({action})."
                    ),
                    timestamp_added=datetime.now(timezone.utc).isoformat(),
                    validation_status="VALID",
                    label_proposed_by=PROPOSED_BY,
                )
                client.add_receipt_word_label(lbl)
                rec["written_label"] = "GRAND_TOTAL"
            # read-back verification
            back = client.list_receipt_words_from_line(
                image_id, receipt_id, line_id
            )
            rec["readback_ok"] = any(
                w.word_id == wid and w.text == rec["recovered"] for w in back
            )
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--keys", required=True, help="JSON list of {image_id, receipt_id}"
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=None, help="write JSON rows here")
    args = ap.parse_args(argv)
    if args.apply == args.dry_run:
        # exactly one of dry-run / apply
        if not args.apply and not args.dry_run:
            args.dry_run = True
        else:
            ap.error("choose exactly one of --dry-run / --apply")

    keys = json.load(open(args.keys))
    if args.limit:
        keys = keys[: args.limit]
    client = DynamoClient(table_name=TABLE, region=REGION)
    s3 = boto3.client("s3", region_name=REGION)

    all_rows = []
    hdr = (
        f"{'receipt':<10} {'field':<12} {'verdict':<22} "
        f"{'recovered':<12} {'expected':<10} basis/notes"
    )
    print(hdr)
    print("-" * len(hdr))
    for k in keys:
        iid, rid = k["image_id"], int(k["receipt_id"])
        try:
            rows = process(client, s3, iid, rid, apply=args.apply)
        except Exception as e:  # noqa: BLE001
            print(f"{iid[:8]:<10} ERROR {e}")
            continue
        for r in rows:
            note = r.get("basis") or ""
            if r.get("action") == "update":
                note = f"UPDATE replaces={r.get('replaces')!r} " + note
            elif r.get("action") == "add":
                note = "ADD " + note
            if r.get("existing_dates"):
                note = f"existing={r['existing_dates']} " + note
            if r.get("candidates"):
                note += f" cand={r['candidates']}"
            if "readback_ok" in r:
                note += f" readback={'OK' if r['readback_ok'] else 'FAIL'}"
            if r.get("apply_error"):
                note += f" APPLY_ERROR={r['apply_error']}"
            print(
                f"{iid[:8]:<10} {r['field']:<12} {r['verdict']:<22} "
                f"{str(r.get('recovered')):<12} "
                f"{str(r.get('expected') or ''):<10} {note}"
            )
        all_rows.extend(rows)

    # summary
    from collections import Counter

    verdicts = Counter((r["field"], r["verdict"]) for r in all_rows)
    print("\n=== summary ===")
    for (field, verdict), n in sorted(verdicts.items()):
        print(f"  {field:<12} {verdict:<24} {n}")
    if args.out:
        json.dump(all_rows, open(args.out, "w"), indent=2, default=str)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
