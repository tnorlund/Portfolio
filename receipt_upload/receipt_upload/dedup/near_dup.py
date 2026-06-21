"""Transaction-identity duplicate detection for receipts with DIFFERENT pixels.

Byte-identical duplicates are caught by sha256 (see :mod:`detector`). Re-scans,
re-photos and **reprints** of the same receipt have different pixels (and a
different sha256) but are the *same transaction* — and a receipt's transaction is
uniquely identified by fields two separate visits can't share: the
authorization / transaction / reference number, the exact total, and the exact
set of line-item prices.

This module extracts that fingerprint from OCR words and decides whether two
receipts are the same transaction. It is pure (no I/O) so it is reusable both in
a batch pass and at upload time to reject a duplicate before it is stored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

# A run of >=5 digits is an auth/transaction/reference/card number. Shorter runs
# are ambiguous (item counts, quantities), so they are not treated as strong IDs.
_LONG_ID = re.compile(r"\d{5,}")
# "Check #239" / "Trans 54226" / "Order 149590" — a labelled short receipt number.
_LABELLED_NUM = re.compile(
    r"(?:check|chk|trans|transaction|order|ticket|ref|receipt|tab)\s*#?\s*(\d{2,})",
    re.I,
)
_TIME = re.compile(r"\b([01]?\d|2[0-3]):[0-5]\d\b")
_AMOUNT = re.compile(r"\d+\.\d{2}")
_STRONG_ID_MIN_LEN = 6  # >=6 digits is a near-unique transaction/auth/card id


@dataclass
class TxnFingerprint:
    strong_ids: Set[str] = field(default_factory=set)   # >=6-digit ids
    labelled_nums: Set[str] = field(default_factory=set)  # check/order/trans #
    amounts: Set[str] = field(default_factory=set)      # all currency amounts
    times: Set[str] = field(default_factory=set)
    total: Optional[float] = None

    def is_empty(self) -> bool:
        return not (self.strong_ids or self.labelled_nums or self.amounts)


def transaction_fingerprint(
    words: List[str], total: Optional[float] = None
) -> TxnFingerprint:
    """Extract a transaction fingerprint from a receipt's OCR words."""
    text = " ".join(w for w in words if w)
    compact = text.replace(" ", "")
    ids = {m for m in _LONG_ID.findall(text) if len(m) >= _STRONG_ID_MIN_LEN}
    ids |= {m for m in _LONG_ID.findall(compact) if len(m) >= _STRONG_ID_MIN_LEN}
    labelled = {m for m in _LABELLED_NUM.findall(text)}
    return TxnFingerprint(
        strong_ids=ids,
        labelled_nums=labelled,
        amounts=set(_AMOUNT.findall(text)),
        times=set(_TIME.findall(text)),
        total=round(float(total), 2) if total is not None else None,
    )


def _amount_overlap(a: Set[str], b: Set[str]) -> float:
    if not (a or b):
        return 0.0
    return len(a & b) / len(a | b)


def same_transaction(
    a: TxnFingerprint, b: TxnFingerprint, *, amount_overlap_min: float = 0.8
) -> Tuple[bool, str]:
    """Decide whether two fingerprints are the SAME transaction (not just a
    similar receipt from the same merchant). Returns ``(is_dup, reason)``.

    A match requires transaction-unique evidence, in priority order:
      1. a shared strong (>=6-digit) id  — auth / transaction / card number
      2. a shared labelled number (Check/Order/Trans #) AND matching item prices
      3. identical total AND (matching item prices OR a shared time)
    Item-price overlap alone is intentionally NOT sufficient (two small identical
    orders could coincide), but combined with a total or labelled number it is.
    """
    shared_strong = a.strong_ids & b.strong_ids
    if shared_strong:
        return True, f"shared transaction/auth id {sorted(shared_strong)[:2]}"

    amt = _amount_overlap(a.amounts, b.amounts)
    shared_label = a.labelled_nums & b.labelled_nums
    if shared_label and amt >= amount_overlap_min:
        return True, (
            f"shared check/order # {sorted(shared_label)[:2]} + "
            f"{amt:.0%} item-price match"
        )

    same_total = (
        a.total is not None and b.total is not None and a.total == b.total
    )
    if same_total and amt >= amount_overlap_min:
        return True, f"same total ${a.total} + {amt:.0%} item-price match"
    if same_total and (a.times & b.times):
        return True, f"same total ${a.total} + same time {sorted(a.times & b.times)[:1]}"

    return False, (
        f"no shared id; total {'match' if same_total else 'differ'}; "
        f"item-price overlap {amt:.0%}"
    )


def find_duplicate(
    new_words: List[str],
    candidates: List[Tuple[object, List[str]]],
    *,
    new_total: Optional[float] = None,
    candidate_totals: Optional[dict] = None,
) -> Optional[Tuple[object, str]]:
    """Return ``(candidate_key, reason)`` of the first candidate that is the same
    transaction as ``new_words``, else ``None``. Used at upload time to reject a
    re-scan/reprint before it is stored. ``candidates`` is ``[(key, words), ...]``.
    """
    fp_new = transaction_fingerprint(new_words, new_total)
    if fp_new.is_empty():
        return None
    totals = candidate_totals or {}
    for key, words in candidates:
        fp = transaction_fingerprint(words, totals.get(key))
        is_dup, reason = same_transaction(fp_new, fp)
        if is_dup:
            return key, reason
    return None
