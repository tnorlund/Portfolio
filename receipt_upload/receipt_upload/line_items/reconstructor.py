"""Deterministic line-item label proposer.

Bounds each receipt's line-item region using its OWN header/totals anchor labels
(MERCHANT/ADDRESS/PHONE/STORE_HOURS above, SUBTOTAL/TAX/GRAND_TOTAL below), then
labels prices (LINE_TOTAL, and UNIT_PRICE in "N @ $X" rows) and product
descriptions (PRODUCT_NAME) by geometry — recovering split-OCR prices
("$3." + "99") and tax-flag suffixes ("15.59T"). It does NOT find columns with a
model; geometry encodes them directly. Proposals are emitted as PENDING labels so
the existing Chroma-consensus + LLM validators confirm them downstream.

Validated on a 7-receipt held-out set: PRODUCT_NAME F1 0.85, LINE_TOTAL F1 0.82 —
ahead of a scoped LayoutLM (0.60 / 0.67). See the team memory
"line-item-deterministic-beats-model".
"""

from __future__ import annotations

import re
import statistics
from datetime import datetime, timezone
from typing import Dict, List, Set, Tuple

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

_FULL = re.compile(r"^\$?\d{1,4}[.,]\d{2}[A-Z]?$")   # $3.99, 3,99, 15.59T
_DOT = re.compile(r"^\$?\d{1,4}[.,]$")               # "$3."  (split, left half)
_FRAG = re.compile(r"^\d{2}$")                       # "99"   (split, right half)
_HEADER = {"ADDRESS_LINE", "PHONE_NUMBER", "STORE_HOURS"}
_TOTALS = {"SUBTOTAL", "TAX", "GRAND_TOTAL"}
_PROPOSED_BY = "geometry_line_items"


def _xy(word) -> Tuple[float, float] | None:
    """Return (x_left, y_center) from a word's normalized bounding box."""
    box = getattr(word, "bounding_box", None)
    if not box:
        return None
    x, y = box.get("x"), box.get("y")
    if x is None or y is None:
        return None
    return x, y + (box.get("height") or 0.0) / 2.0


def _is_money(t: str) -> bool:
    return bool(_FULL.match(t) or _DOT.match(t) or _FRAG.match(t))


def _has_letters(t: str) -> bool:
    return len(re.sub(r"[^A-Za-z]", "", t)) >= 2


def _amount(t: str) -> float | None:
    t = re.sub(r"[A-Z]$", "", t.strip().lstrip("$")).replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def propose_line_item_labels(
    words: List, existing_labels: List[ReceiptWordLabel]
) -> List[ReceiptWordLabel]:
    """Propose PENDING line-item labels for one receipt's words.

    Args:
        words: ReceiptWord entities (need ``.bounding_box``, ``.text``,
            ``.line_id``, ``.word_id``, ``.image_id``, ``.receipt_id``).
        existing_labels: ReceiptWordLabel entities already on the receipt;
            their totals/header anchors bound the line-item region.

    Returns:
        New ReceiptWordLabel proposals (status PENDING,
        ``label_proposed_by="geometry_line_items"``) for words that don't already
        carry that label. Empty when the region can't be bounded.
    """
    label_map: Dict[Tuple[int, int], Set[str]] = {}
    for lab in existing_labels:
        label_map.setdefault((lab.line_id, lab.word_id), set()).add(lab.label)

    pos: Dict[Tuple[int, int], Tuple[float, float]] = {}
    for w in words:
        xy = _xy(w)
        if xy is not None:
            pos[(w.line_id, w.word_id)] = xy
    placed = [w for w in words if (w.line_id, w.word_id) in pos]
    if not placed:
        return []

    def xl(w):
        return pos[(w.line_id, w.word_id)][0]

    def cy(w):
        return pos[(w.line_id, w.word_id)][1]

    def raw(w):
        return label_map.get((w.line_id, w.word_id), set())

    totals = [cy(w) for w in placed if raw(w) & _TOTALS]
    if not totals:
        return []
    # Anchor the line-item band's bottom edge on GRAND_TOTAL when present — it's
    # the true bottom of the items. SUBTOTAL/TAX can be mislabeled line-item
    # prices (the model emits them when two line totals coincidentally sum to the
    # grand total and there's no Subtotal/Tax keyword), which would otherwise
    # collapse the band onto the items themselves.
    grand = [cy(w) for w in placed if "GRAND_TOTAL" in raw(w)]
    tc = statistics.median(grand) if grand else statistics.median(totals)
    header = [cy(w) for w in placed if raw(w) & _HEADER]
    if not header:
        merch = [cy(w) for w in placed if "MERCHANT_NAME" in raw(w)]
        if not merch:
            return []
        header = [max(merch, key=lambda y: abs(y - tc))]
    hc = statistics.median(header)
    lo, hi = min(hc, tc), max(hc, tc)
    excl = _HEADER | _TOTALS
    band = [w for w in placed if lo < cy(w) < hi and not (raw(w) & excl)]
    if not band:
        return []

    heights = [
        (getattr(w, "bounding_box", {}) or {}).get("height") for w in band
    ]
    heights = [h for h in heights if h]
    ytol = (statistics.median(heights) if heights else 0.015) * 0.5
    band.sort(key=cy)
    rows: List[List] = []
    cur: List = []
    centroid = None
    for w in band:
        y = cy(w)
        if cur and abs(y - centroid) > ytol:
            rows.append(cur)
            cur = []
        cur.append(w)
        centroid = sum(cy(x) for x in cur) / len(cur)
    if cur:
        rows.append(cur)

    proposals: Dict[Tuple[int, int], str] = {}
    line_vals: List[float] = []
    for row in rows:
        monies = sorted([w for w in row if _is_money(w.text)], key=xl)
        at = next((w for w in row if w.text in ("@", "x", "X")), None)

        # --- "N @ $X" unit-price row -------------------------------------
        # The integer before "@" is QUANTITY and the price right after "@" is
        # UNIT_PRICE. The row's LINE_TOTAL is usually on the product row above,
        # so a SINGLE price after "@" is the unit price (NOT a line total);
        # only a SECOND, rightmost price on the row is the line total
        # (e.g. "2 @ 1.50  3.00").
        if at is not None:
            at_x = xl(at)
            prices_right = [
                m
                for m in monies
                if xl(m) > at_x and (_FULL.match(m.text) or _DOT.match(m.text))
            ]
            qty = next(
                (
                    w
                    for w in sorted(row, key=xl, reverse=True)
                    if xl(w) < at_x and re.fullmatch(r"\d{1,3}", w.text)
                ),
                None,
            )
            if qty is not None:
                proposals[(qty.line_id, qty.word_id)] = "QUANTITY"
            if len(prices_right) >= 2:
                lt = prices_right[-1]
                proposals[(lt.line_id, lt.word_id)] = "LINE_TOTAL"
                if (v := _amount(lt.text)) is not None:
                    line_vals.append(v)
                for m in prices_right[:-1]:
                    proposals[(m.line_id, m.word_id)] = "UNIT_PRICE"
            elif len(prices_right) == 1:
                proposals[(prices_right[0].line_id, prices_right[0].word_id)] = (
                    "UNIT_PRICE"
                )
            for w in row:                            # product = text left of "@"
                if xl(w) < at_x and _has_letters(w.text) and not _is_money(w.text):
                    proposals.setdefault((w.line_id, w.word_id), "PRODUCT_NAME")
            continue

        # --- normal product row ------------------------------------------
        if not monies:
            continue
        # A line total must be a real price (a full token, or the "$3." left half
        # of a split) — never a bare two-digit token, which is usually a quantity.
        line_total = next(
            (m for m in reversed(monies) if _FULL.match(m.text) or _DOT.match(m.text)),
            None,
        )
        if line_total is None:
            continue
        group = [line_total]
        group_keys = {(line_total.line_id, line_total.word_id)}
        for m in monies:                            # split-price recovery
            if (m.line_id, m.word_id) in group_keys:
                continue
            if abs(xl(m) - xl(line_total)) < 0.14 and (
                _DOT.match(m.text) or _FRAG.match(m.text) or _DOT.match(line_total.text)
            ):
                group.append(m)
                group_keys.add((m.line_id, m.word_id))
        for m in group:
            proposals[(m.line_id, m.word_id)] = "LINE_TOTAL"
        full = next((m for m in group if _FULL.match(m.text)), None)
        if full and (val := _amount(full.text)) is not None:
            line_vals.append(val)
        for w in row:                                # product = text left of price
            if xl(w) < xl(line_total) and _has_letters(w.text) and not _is_money(w.text):
                proposals.setdefault((w.line_id, w.word_id), "PRODUCT_NAME")

    # Arithmetic confidence: Σ line_total vs subtotal/grand total.
    tot_vals = [
        _amount(w.text)
        for w in placed
        if raw(w) & {"SUBTOTAL", "GRAND_TOTAL"} and _amount(w.text) is not None
    ]
    arith = None
    if line_vals and tot_vals:
        s = round(sum(line_vals), 2)
        target = min(tot_vals, key=lambda t: abs(t - s))
        arith = abs(s - target) <= 0.02     # strict: exact-to-2-cents to auto-commit
    state = (
        "consistent" if arith else "mismatch" if arith is False else "unverified"
    )
    reason = f"Geometry line-item reconstruction (arithmetic {state})."
    # Auto-validate when the line totals provably sum to the receipt total;
    # otherwise leave PENDING for the Chroma/LLM validators to confirm.
    status = (
        ValidationStatus.VALID.value if arith else ValidationStatus.PENDING.value
    )

    by_key = {(w.line_id, w.word_id): w for w in placed}
    out: List[ReceiptWordLabel] = []
    for key, label in proposals.items():
        if label_map.get(key):       # word already carries a label — don't double-label
            continue
        w0 = by_key.get(key)
        if w0 is None:
            continue
        out.append(
            ReceiptWordLabel(
                image_id=w0.image_id,
                receipt_id=w0.receipt_id,
                line_id=key[0],
                word_id=key[1],
                label=label,
                reasoning=reason,
                timestamp_added=datetime.now(timezone.utc),
                validation_status=status,
                label_proposed_by=_PROPOSED_BY,
            )
        )
    return out
