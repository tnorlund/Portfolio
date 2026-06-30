"""Render-time content cleanup for synthetic receipts.

The renders inherit OCR-noisy tokens from the base receipts (the dominant
remaining realism tell after layout/font were fixed). This pass repairs the
deterministic, high-frequency ones just before drawing -- it does NOT re-run the
synthesis pipeline, so it only affects the rendered image (the demo), not the
stored training labels.

Two repairs, both low-risk and context-gated:

* :func:`canonicalize_auth_tokens` -- the EMV / payment-auth block: ``DID:``/``AND:``
  -> ``AID:`` (only before a hex AID), ``WIN`` -> ``PIN`` (only after ``BY``),
  ``Seg#`` -> ``Seq#``, and a stray trailing period on a price.
* (future) totals reconciliation lives alongside this and is applied by the same
  :func:`clean_for_render` entry point.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

# A hex Application IDentifier as printed in the EMV block, e.g. A0000000980840.
_AID_HEX = re.compile(r"^A?[0-9A-F]{8,}$")
# A bare currency amount, optionally with a trailing stray period from OCR.
_PRICE_TRAILING_DOT = re.compile(r"^(\$?\d{1,3}(?:,\d{3})*\.\d{2})\.$")

# A currency amount with exactly THREE decimals -- a near-certain OCR/synth error
# on a 2-decimal price (e.g. ``19.981``). FOUR+ decimals are left alone because
# those are legitimate printed tax RATES (``9.75000``, ``8.37500``).
_AMOUNT_3DEC = re.compile(r"^([-+]?\$?\d{1,3}(?:,\d{3})*)\.(\d{3})([-+]?[A-Z]?)$")

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
        elif (
            t.rstrip(":").upper() in ("DID", "AND", "ALD", "A1D", "AIO", "AlD")
            and _AID_HEX.match(nxt.upper())
        ):
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


def clean_for_render(receipt: Mapping[str, Any]) -> dict:
    """Apply all render-time content repairs to ``receipt`` (mutates word dicts).

    Returns a small report ``{auth_fixed, totals_fixed}`` for logging/tests.
    """
    words = list(_iter_word_dicts(receipt))
    auth_fixed = canonicalize_auth_tokens(words)
    totals_fixed = fix_currency_decimals(words)
    return {"auth_fixed": auth_fixed, "totals_fixed": totals_fixed}
