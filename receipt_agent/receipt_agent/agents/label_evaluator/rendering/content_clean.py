"""Render-time content cleanup for synthetic receipts.

The renders inherit OCR-noisy tokens from the base receipts (the dominant
remaining realism tell after layout/font were fixed). This pass repairs the
deterministic, high-frequency ones just before drawing -- it does NOT re-run the
synthesis pipeline, so it only affects the rendered image (the demo), not the
stored training labels.

Repairs are low-risk and context-gated:

* :func:`canonicalize_auth_tokens` -- the EMV / payment-auth block: ``DID:``/``AND:``
  -> ``AID:`` (only before a hex AID), ``WIN`` -> ``PIN`` (only after ``BY``),
  ``Seg#`` -> ``Seq#``, and a stray trailing period on a price.
* :func:`fix_item_amount_ocr` -- amount-shaped OCR confusions are repaired only
  on rows carrying a product label.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from receipt_agent.agents.label_evaluator.rendering.number_format import (
    US as _NF,
)
from receipt_agent.agents.label_evaluator.rendering.number_format import (
    date_core as _date_core,
)
from receipt_agent.agents.label_evaluator.rendering.number_format import (
    fraction as _fraction,
)
from receipt_agent.agents.label_evaluator.rendering.number_format import (
    integer_part as _integer_part,
)
from receipt_agent.agents.label_evaluator.rendering.row_bands import (
    group_rows_quantized,
)

# A hex Application IDentifier as printed in the EMV block, e.g. A0000000980840.
_AID_HEX = re.compile(r"^A?[0-9A-F]{8,}$")
# A bare currency amount, optionally with a trailing stray period from OCR.
_PRICE_TRAILING_DOT = re.compile(
    f"^({_NF.currency}?{_integer_part(_NF)}{_fraction(_NF)})\\.$"
)

# A currency amount with exactly THREE decimals -- a near-certain OCR/synth error
# on a 2-decimal price (e.g. ``19.981``). FOUR+ decimals are left alone because
# those are legitimate printed tax RATES (``9.75000``, ``8.37500``). The 3 is the
# error signature, not the locale fraction width, so it stays literal.
_AMOUNT_3DEC = re.compile(
    f"^([-+]?{_NF.currency}?{_integer_part(_NF)}){_NF.decimal}(\\d{{3}})"
    f"([-+]?{_NF.tax_flag}?)$"
)

# Amount-shaped OCR token with a two-digit fractional part. The candidate may
# contain common digit confusions, but is never repaired without product-row
# evidence (see ``fix_item_amount_ocr``).
_ITEM_AMOUNT_OCR = re.compile(
    r"^([-+]?\$?)([0-9OILSBY]+)([.,])([0-9OILSBY]{2})([A-Z]?)([-+]?)$",
    re.I,
)
_DIGIT_OCR_TRANSLATION = str.maketrans(
    {
        "O": "0",
        "I": "1",
        "L": "1",
        "S": "5",
        "B": "8",
        "Y": "7",
    }
)

# Date / time tokens. A printed transaction line is ``<date> <time> ...`` so the
# token right before a HH:MM time is the date; a valid date is MM/DD/YYYY with a
# real month/day (``0/20/2025`` and the slashless ``1072072025`` are OCR garbles).
_TIME_TOKEN = re.compile(r"^\d{1,2}:\d{2}$")
_DATE_MDY = re.compile(f"^{_date_core(_NF, groups=True)}$")


def _valid_date(text: str) -> bool:
    m = _DATE_MDY.match(text)
    if not m:
        return False
    mo, da = int(m.group(1)), int(m.group(2))
    return 1 <= mo <= 12 and 1 <= da <= 31


# Context-free single-token repairs that are essentially never legitimate text.
_TOKEN_FIX = {
    "Seg#": "Seq#",
    "SEG#": "SEQ#",
}


def _iter_word_dicts(receipt: Mapping[str, Any]):
    flat = receipt.get("words")
    if isinstance(flat, list) and flat:
        for w in flat:
            if isinstance(w, dict):
                yield w
        return
    for line in receipt.get("lines", []) or []:
        for w in line.get("words", []) or []:
            if isinstance(w, dict):
                yield w


def canonicalize_auth_tokens(words: list[dict]) -> int:
    """Repair EMV/auth-block OCR substitutions in place. Returns #tokens changed."""
    n = 0
    for i, w in enumerate(words):
        t = str(w.get("text") or "")
        new = t
        prev = str(words[i - 1].get("text") or "") if i > 0 else ""
        nxt = str(words[i + 1].get("text") or "") if i + 1 < len(words) else ""
        if t in _TOKEN_FIX:
            new = _TOKEN_FIX[t]
        elif t.rstrip(":").upper() in (
            "DID",
            "AND",
            "ALD",
            "A1D",
            "AIO",
            "AlD",
        ) and _AID_HEX.match(nxt.upper()):
            # "AID" label before the hex AID, with/without the printed colon. The
            # hex-follow gate makes substituting the common word "AND" safe here.
            new = "AID:" if t.endswith(":") else "AID"
        elif t.upper() == "WIN" and prev.upper() == "BY":
            new = "PIN" if t.isupper() else "Pin"
        else:
            m = _PRICE_TRAILING_DOT.match(t)
            if m:
                new = m.group(1)
        if new != t:
            w["text"] = new
            n += 1
    return n


def fix_currency_decimals(words: list[dict]) -> int:
    """Repair 3-decimal currency amounts to 2 decimals in place (skips tax rates).

    A ``TOTAL 19.981`` is an impossible currency value; ``CA TAX 9.75000`` is a
    legitimate rate. We only touch amounts with EXACTLY three decimals, and never
    when the two preceding tokens mention TAX (a printed rate). Returns #changed.
    """
    n = 0
    for i, w in enumerate(words):
        t = str(w.get("text") or "")
        m = _AMOUNT_3DEC.match(t)
        if not m:
            continue
        ctx = " ".join(
            str(words[j].get("text") or "") for j in range(max(0, i - 2), i)
        ).upper()
        if "TAX" in ctx:
            continue  # printed tax rate, not a currency total
        w["text"] = f"{m.group(1)}.{m.group(2)[:2]}{m.group(3)}"
        n += 1
    return n


def fix_item_amount_ocr(words: list[dict]) -> int:
    """Repair amount-shaped digit confusions on labeled product rows.

    A product row can contain an unlabeled amount even when its description
    words carry ``PRODUCT_NAME``. Require that row evidence, a decimal-shaped
    token, and at least one actual OCR correction (letter substitution or
    decimal comma) before mutating anything. Product codes and prose therefore
    remain untouched.
    """
    positioned = [word for word in words if word.get("bbox")]
    rows = group_rows_quantized(
        positioned,
        lambda word: float(word["bbox"][1]) + float(word["bbox"][3]),
        step=16,
        descending=True,
    )

    changed = 0
    for row in rows:
        has_product = any(
            "PRODUCT_NAME" in (word.get("labels") or []) for word in row
        )
        if not has_product:
            continue
        for word in row:
            text = str(word.get("text") or "").strip()
            match = _ITEM_AMOUNT_OCR.fullmatch(text)
            if match is None:
                continue
            whole = match.group(2).upper().translate(_DIGIT_OCR_TRANSLATION)
            fraction = match.group(4).upper().translate(_DIGIT_OCR_TRANSLATION)
            repaired = (
                f"{match.group(1)}{whole}.{fraction}"
                f"{match.group(5)}{match.group(6)}"
            )
            if repaired == text:
                continue
            # A local OCR repair must not re-seed the whole page's stochastic
            # paper texture. Preserve the source token solely for deterministic
            # texture identity; rendering and evaluation still consume
            # ``repaired`` below.
            word.setdefault("_texture_seed_text", text)
            word["text"] = repaired
            changed += 1
    return changed


def canonicalize_dates(words: list[dict]) -> int:
    """Repair garbled transaction-date tokens to the receipt's canonical date.

    A base receipt often OCRs the same printed date cleanly in one spot
    (``10/20/2025``) but garbled in others (``1072072025``, ``0/20/2025``). Every
    printed occurrence is the SAME transaction date, so we pick the most common
    valid MM/DD/YYYY and replace the garbled ones. Context gate: only a date-shaped
    token (has a ``/`` or is a >=6-digit run) sitting immediately before a HH:MM
    time is touched, so store/register numbers are never rewritten. Returns #fixed.
    """
    from collections import Counter

    valid = [
        str(w.get("text") or "")
        for w in words
        if _valid_date(str(w.get("text") or ""))
    ]
    if not valid:
        return 0
    canonical = Counter(valid).most_common(1)[0][0]
    n = 0
    for i, w in enumerate(words):
        t = str(w.get("text") or "")
        nxt = str(words[i + 1].get("text") or "") if i + 1 < len(words) else ""
        if not _TIME_TOKEN.match(nxt):
            continue
        date_shaped = ("/" in t) or (t.isdigit() and len(t) >= 6)
        if date_shaped and not _valid_date(t) and t != canonical:
            w["text"] = canonical
            n += 1
    return n


def fix_items_sold_separator(words: list[dict]) -> int:
    """``... ITEMS SOLD * 4`` -> ``... ITEMS SOLD = 4`` (Costco prints ``=``).

    Only a lone ``*`` immediately after ``SOLD`` is converted, so the ``****``
    masking runs elsewhere are untouched. Returns #fixed.
    """
    n = 0
    for i, w in enumerate(words):
        t = str(w.get("text") or "")
        prev = str(words[i - 1].get("text") or "") if i > 0 else ""
        if t == "*" and prev.upper() == "SOLD":
            w["text"] = "="
            n += 1
    return n


def clean_for_render(receipt: Mapping[str, Any]) -> dict:
    """Apply all render-time content repairs to ``receipt`` (mutates word dicts).

    Returns a small report of repair counts.
    """
    words = list(_iter_word_dicts(receipt))
    auth_fixed = canonicalize_auth_tokens(words)
    totals_fixed = fix_currency_decimals(words)
    item_amounts_fixed = fix_item_amount_ocr(words)
    dates_fixed = canonicalize_dates(words)
    seps_fixed = fix_items_sold_separator(words)
    return {
        "auth_fixed": auth_fixed,
        "totals_fixed": totals_fixed,
        "item_amounts_fixed": item_amounts_fixed,
        "dates_fixed": dates_fixed,
        "seps_fixed": seps_fixed,
    }
