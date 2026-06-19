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
_RECLASS_BY = "arithmetic_totals_reclass"


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


def reclassify_mislabeled_totals(
    words: List, existing_labels: List[ReceiptWordLabel]
) -> Tuple[
    List[Tuple[ReceiptWordLabel, ReceiptWordLabel]], List[ReceiptWordLabel]
]:
    """Reclassify PENDING ``SUBTOTAL``/``TAX`` labels that are actually line totals.

    The first-pass model emits ``SUBTOTAL``/``TAX`` when two (or more) line totals
    coincidentally sum to the grand total and there is no Subtotal/Tax keyword to
    anchor a real totals block (e.g. the Trader Joe's IMG_2826 case: ``$4.29`` and
    ``$1.38`` for milk + bananas sum to the ``$5.67`` grand total). Those prices
    belong to product rows, not a totals block.

    We override **only when arithmetic proves it** — never on geometry alone — and
    only for ``PENDING`` labels, so deliberate human ``VALID``/``INVALID`` currency
    labels are left untouched. The gate:

    * a ``GRAND_TOTAL`` with a numeric value exists;
    * the candidate sits *above* the grand-total row (inside the item region);
    * Σ(line totals) reconciles to the grand total **only** when the candidates
      are counted as line totals — i.e. it does not already reconcile without
      them, and the receipt's existing line totals do not already sum to a
      labeled subtotal (the normal-receipt case, where SUBTOTAL == Σ items and
      SUBTOTAL + TAX == GRAND_TOTAL is the definition, not a mislabel).

    When the reconciliation fires, the arithmetic is the *authority* on the
    line-item totals, so it also reports the receipt's existing ``LINE_TOTAL``
    labels that participate in the sum. Callers must lock those VALID and pull
    them (along with the invalidated SUBTOTAL/TAX) out of the pending set, or the
    downstream Chroma/LLM validators will "correct" a real line total back to
    TAX using the very same coincidental arithmetic that caused the mislabel.

    Returns:
        ``(reclassifications, locked_line_totals)`` where:

        * ``reclassifications`` is a list of ``(old_label, new_label)`` pairs.
          Callers should **invalidate** ``old_label`` (mark it ``INVALID`` —
          keeping it as an audit trail, since deliberate INVALID currency labels
          are how we record "this isn't that total"), drop it from the pending
          set, and add ``new_label`` (a ``LINE_TOTAL``, status ``VALID`` since
          arithmetic confirms it, ``label_proposed_by="arithmetic_totals_reclass"``).
        * ``locked_line_totals`` is the list of existing ``LINE_TOTAL`` label
          objects that participate in the reconciled sum. Callers should mark
          them ``VALID`` and drop them from the pending set so the validators
          cannot override them.

        Both lists are empty when no override is warranted.
    """
    label_objs: Dict[Tuple[int, int], List[ReceiptWordLabel]] = {}
    for lab in existing_labels:
        label_objs.setdefault((lab.line_id, lab.word_id), []).append(lab)

    pos: Dict[Tuple[int, int], Tuple[float, float]] = {}
    for w in words:
        xy = _xy(w)
        if xy is not None:
            pos[(w.line_id, w.word_id)] = xy
    by_key = {
        (w.line_id, w.word_id): w
        for w in words
        if (w.line_id, w.word_id) in pos
    }
    if not by_key:
        return [], []

    def cy(k: Tuple[int, int]) -> float:
        return pos[k][1]

    grand_keys = [
        k
        for k, labs in label_objs.items()
        if k in by_key
        and any(
            lab.label == "GRAND_TOTAL"
            and lab.validation_status != ValidationStatus.INVALID.value
            for lab in labs
        )
    ]
    grand_vals = [
        v for v in (_amount(by_key[k].text) for k in grand_keys) if v is not None
    ]
    if not grand_vals:
        return [], []
    grand = max(grand_vals)
    tc = statistics.median([cy(k) for k in grand_keys])

    # Candidate mislabels: PENDING SUBTOTAL money words sitting above the
    # grand-total row (header is high-y, totals low-y; "above" means larger cy).
    #
    # We deliberately consider ONLY SUBTOTAL, never TAX. A real receipt's
    # Σ(items) + TAX == GRAND_TOTAL is its *definition*, so a TAX candidate would
    # "reconcile" on every normal receipt and we'd corrupt real tax labels. By
    # excluding TAX from the candidate sum, a receipt that has a genuine tax can
    # never reconcile (the sum falls short by exactly the tax), so we abstain —
    # arithmetic alone cannot tell a mislabeled line item from a real tax.
    candidates: List[Tuple[Tuple[int, int], ReceiptWordLabel, float]] = []
    for k, labs in label_objs.items():
        if k not in by_key or cy(k) <= tc:
            continue
        word = by_key[k]
        if not _is_money(word.text):
            continue
        val = _amount(word.text)
        if val is None:
            continue
        for lab in labs:
            if (
                lab.label == "SUBTOTAL"
                and lab.validation_status == ValidationStatus.PENDING.value
            ):
                candidates.append((k, lab, val))
                break
    if not candidates:
        return [], []

    # Clean line-total mass: existing (non-INVALID) LINE_TOTAL labels + what
    # geometry recovers with the current (SUBTOTAL/TAX-excluding) band. INVALID
    # line totals are deliberate rejections — they must not feed the arithmetic
    # nor be resurrected to VALID by the locking step below.
    lt_keys = {
        k
        for k, labs in label_objs.items()
        if any(
            lab.label == "LINE_TOTAL"
            and lab.validation_status != ValidationStatus.INVALID.value
            for lab in labs
        )
    }
    lt_keys |= {
        (p.line_id, p.word_id)
        for p in propose_line_item_labels(words, existing_labels)
        if p.label == "LINE_TOTAL"
    }
    l_clean = round(
        sum((_amount(by_key[k].text) or 0.0) for k in lt_keys if k in by_key), 2
    )
    cand_sum = round(sum(v for _, _, v in candidates), 2)

    tol = 0.02
    reconciled_as_is = abs(l_clean - grand) <= tol
    reconciled_with_candidates = abs(l_clean + cand_sum - grand) <= tol
    # The reconciled line-item set must contain at least TWO line totals. A single
    # price that equals the whole grand total (with no other line totals) is far
    # more likely a mislabeled *total* than a lone line item — e.g. a one-item
    # cafe receipt where the model tags "Total 10.83" as SUBTOTAL. Requiring >=2
    # keeps us from "reconciling" such a receipt into a phantom line item.
    line_total_count = len({k for k in lt_keys if k in by_key}) + len(candidates)
    # Normal receipt: the real line items already sum to a labeled SUBTOTAL value.
    subtotal_vals = [v for _, lab, v in candidates if lab.label == "SUBTOTAL"]
    is_normal_receipt = l_clean > 0 and any(
        abs(l_clean - s) <= tol for s in subtotal_vals
    )
    if (
        reconciled_as_is
        or is_normal_receipt
        or not reconciled_with_candidates
        or line_total_count < 2
    ):
        return [], []

    total_lt = round(l_clean + cand_sum, 2)
    reclassifications: List[Tuple[ReceiptWordLabel, ReceiptWordLabel]] = []
    for k, old, _val in candidates:
        w0 = by_key[k]
        reclassifications.append(
            (
                old,
                ReceiptWordLabel(
                    image_id=w0.image_id,
                    receipt_id=w0.receipt_id,
                    line_id=k[0],
                    word_id=k[1],
                    label="LINE_TOTAL",
                    reasoning=(
                        f"Arithmetic reclassification: model-proposed {old.label} "
                        f"is a line-item total — Σ line totals ({total_lt:.2f}) "
                        f"equals GRAND_TOTAL ({grand:.2f}) only when this price is "
                        f"counted as a line item."
                    ),
                    timestamp_added=datetime.now(timezone.utc),
                    validation_status=ValidationStatus.VALID.value,
                    label_proposed_by=_RECLASS_BY,
                ),
            )
        )

    # The arithmetic is the authority: lock the receipt's existing LINE_TOTAL
    # labels that participate in the reconciled sum so the Chroma/LLM validators
    # can't "correct" a real line total back to TAX via the same coincidental
    # arithmetic that caused the mislabel.
    cand_keys = {k for k, _, _ in candidates}
    locked_line_totals = [
        lab
        for k, labs in label_objs.items()
        if k in lt_keys and k not in cand_keys
        for lab in labs
        if lab.label == "LINE_TOTAL"
        and lab.validation_status != ValidationStatus.INVALID.value
    ]
    return reclassifications, locked_line_totals


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
    # ``label_map`` holds only ACTIVE (non-INVALID) labels: they drive the band,
    # totals anchors, and exclusions. A word whose SUBTOTAL was invalidated and
    # replaced by a VALID LINE_TOTAL (arithmetic reclassification) must read as a
    # line item here — not be excluded by its stale SUBTOTAL — so its product row
    # can still yield PRODUCT_NAME. ``labeled_any`` tracks EVERY labeled word
    # (incl. INVALID) and gates output so we never resurrect a deliberately
    # rejected label as a fresh PENDING proposal.
    label_map: Dict[Tuple[int, int], Set[str]] = {}
    labeled_any: Set[Tuple[int, int]] = set()
    for lab in existing_labels:
        labeled_any.add((lab.line_id, lab.word_id))
        if lab.validation_status == ValidationStatus.INVALID.value:
            continue
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
        # --- "N @ $X" unit-price row -------------------------------------
        # A unit-price row's marker (@, x, X) must sit DIRECTLY between an integer
        # quantity (immediate left) and a price (immediate right). Requiring that
        # adjacency stops product/pack tokens like "VITAMIN X $9.99" or
        # "12 X 355ML $6.99" from being mis-read as unit-price rows (where the
        # lone price is the LINE_TOTAL, not a unit price). The integer before the
        # marker is QUANTITY and the price right after is UNIT_PRICE; the row's
        # LINE_TOTAL is usually on the product row above, so a SINGLE price after
        # the marker is the unit price (NOT a line total) — only a SECOND,
        # rightmost price on the row is the line total (e.g. "2 @ 1.50  3.00").
        row_x = sorted(row, key=xl)
        at = None
        qty = None
        for i, w in enumerate(row_x):
            if w.text in ("@", "x", "X"):
                left = row_x[i - 1] if i > 0 else None
                right = row_x[i + 1] if i + 1 < len(row_x) else None
                if (
                    left is not None
                    and re.fullmatch(r"\d{1,3}", left.text)
                    and right is not None
                    and (_FULL.match(right.text) or _DOT.match(right.text))
                ):
                    at, qty = w, left
                    break

        if at is not None:
            at_x = xl(at)
            prices_right = [
                m
                for m in monies
                if xl(m) > at_x and (_FULL.match(m.text) or _DOT.match(m.text))
            ]
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
        if key in labeled_any:       # word already carries a label (incl. INVALID)
            continue                  # — don't double-label or resurrect a rejection
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
