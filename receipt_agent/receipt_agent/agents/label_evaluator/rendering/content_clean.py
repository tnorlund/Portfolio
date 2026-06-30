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
        elif t in ("DID:", "AND:", "AlD:", "A1D:", "AIO:") and _AID_HEX.match(nxt.upper()):
            new = "AID:"
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


def clean_for_render(receipt: Mapping[str, Any]) -> dict:
    """Apply all render-time content repairs to ``receipt`` (mutates word dicts).

    Returns a small report ``{auth_fixed, totals_fixed}`` for logging/tests.
    """
    words = list(_iter_word_dicts(receipt))
    auth_fixed = canonicalize_auth_tokens(words)
    return {"auth_fixed": auth_fixed, "totals_fixed": 0}
