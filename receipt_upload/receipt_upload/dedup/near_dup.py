"""Transaction-identity duplicate detection for receipts with DIFFERENT pixels.

Byte-identical duplicates are caught by sha256 (see :mod:`detector`). Re-scans,
re-photos and **reprints** of the same receipt have different pixels (and a
different sha256) but are the *same transaction*.

This module produces CANDIDATE matches for confirmation — it is deliberately
conservative because the signals it uses are individually unreliable:
  * a shared numeric id is often a card number / terminal id (TID) / card AID /
    loyalty or batch number / UPC — NOT transaction-unique (naive shared-id
    matching produced ~45k false pairs on real data);
  * stored totals and OCR item-prices can be wrong;
  * two different visits can have the same items + total on the same card.
So a positive match here requires a shared id CORROBORATED by matching content
*and* merchant, with hard negative guards (different printed time or merchant =>
not the same transaction). Even then, callers should treat the result as a
candidate to confirm against the raw image, not as deletion authority.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

_LONG_ID = re.compile(r"\d{5,}")
# "Check #239" / "Trans 54226" / "Order 149590" — a labelled short receipt
# number.
_LABELLED_NUM = re.compile(
    r"(?:check|chk|trans|transaction|order|ticket|ref|receipt|tab)\s*#?\s*(\d{2,})",
    re.I,
)
# non-capturing group so findall returns the full "HH:MM", not just the hour
_TIME = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b")
_AMOUNT = re.compile(r"\d+\.\d{2}")
_STRONG_ID_MIN_LEN = (
    6  # >=6 digit run, before frequency/merchant corroboration
)


def _normalize_merchant(m: Optional[str]) -> Optional[str]:
    if not m or not str(m).strip():
        return None
    return "".join(str(m).lower().split())


@dataclass
class TxnFingerprint:
    strong_ids: Set[str] = field(
        default_factory=set
    )  # >=6-digit ids (contiguous only)
    labelled_nums: Set[str] = field(default_factory=set)  # check/order/trans #
    amounts: Set[str] = field(default_factory=set)  # all currency amounts
    times: Set[str] = field(default_factory=set)
    total: Optional[float] = None
    merchant: Optional[str] = None

    def is_empty(self) -> bool:
        return not (self.strong_ids or self.labelled_nums or self.amounts)


def transaction_fingerprint(
    words: List[str],
    total: Optional[float] = None,
    merchant: Optional[str] = None,
) -> TxnFingerprint:
    """Extract a transaction fingerprint from a receipt's OCR words.

    Strong ids are CONTIGUOUS >=6-digit runs only. We do NOT glue adjacent
    numeric tokens (a prior implementation space-stripped the text and so
    manufactured phantom ids by concatenating, e.g., a split date or a printed
    phone number — fabricating "transaction ids" that collide across distinct
    visits).
    """
    text = " ".join(w for w in words if w)
    return TxnFingerprint(
        strong_ids={
            m for m in _LONG_ID.findall(text) if len(m) >= _STRONG_ID_MIN_LEN
        },
        labelled_nums=set(_LABELLED_NUM.findall(text)),
        amounts=set(_AMOUNT.findall(text)),
        times=set(_TIME.findall(text)),
        total=round(float(total), 2) if total is not None else None,
        merchant=_normalize_merchant(merchant),
    )


def frequent_ids(
    fingerprints: Iterable[TxnFingerprint], *, max_count: int = 3
) -> Set[str]:
    """Corpus-frequency guard: ids appearing on more than ``max_count`` receipts
    are recurring codes (card/terminal/AID/loyalty/UPC), never
    transaction-unique.
    Pass the result as ``denylist`` to :func:`same_transaction`/:func:`find_duplicate`.
    """
    c: Counter = Counter()
    for fp in fingerprints:
        for i in fp.strong_ids:
            c[i] += 1
    return {i for i, n in c.items() if n > max_count}


def _amount_overlap(a: Set[str], b: Set[str]) -> float:
    if not (a or b):
        return 0.0
    return len(a & b) / len(a | b)


def same_transaction(
    a: TxnFingerprint,
    b: TxnFingerprint,
    *,
    amount_overlap_min: float = 0.8,
    denylist: Optional[Set[str]] = None,
) -> Tuple[bool, str]:
    """Decide whether two fingerprints are the SAME transaction. Returns
    ``(is_dup, reason)``.

    Conservative by construction:
      * **Negative guards (return False):** different merchant when both known;
        disjoint printed times when both have times (two scans of ONE receipt
        share the printed time, different visits don't).
      * **Positive match requires CORROBORATION** — a shared strong id (excluding
        ``denylist`` recurring codes) AND (identical total OR >=
        ``amount_overlap_min`` item-price overlap), or a shared labelled
        check/order # AND matching prices.
      * A shared id ALONE, a same total ALONE, or price overlap ALONE is NEVER
        sufficient.
    """
    denylist = denylist or set()

    # --- hard negative guards ---
    if a.merchant and b.merchant and a.merchant != b.merchant:
        return False, f"different merchant ({a.merchant} vs {b.merchant})"
    if a.times and b.times and not a.times & b.times:
        return False, (
            f"different printed time ({sorted(a.times)[:1]} vs {sorted(b.times)[:1]})"
        )

    merchant_ok = not a.merchant or not b.merchant or a.merchant == b.merchant
    a_ids = a.strong_ids - denylist
    b_ids = b.strong_ids - denylist
    shared_strong = a_ids & b_ids
    amt = _amount_overlap(a.amounts, b.amounts)
    same_total = (
        a.total is not None and b.total is not None and a.total == b.total
    )

    # shared transaction id corroborated by matching content
    if (
        shared_strong
        and merchant_ok
        and (same_total or amt >= amount_overlap_min)
    ):
        return True, (
            f"shared id {sorted(shared_strong)[:1]} + content "
            f"(total_match={same_total}, item-price {amt:.0%})"
        )
    # labelled check/order number corroborated by matching prices
    shared_label = a.labelled_nums & b.labelled_nums
    if shared_label and merchant_ok and amt >= amount_overlap_min:
        return True, (
            f"shared check/order # {sorted(shared_label)[:1]} + {amt:.0%} item-price"
        )

    return False, (
        f"insufficient corroboration (shared_id={bool(shared_strong)}, "
        f"same_total={same_total}, item-price {amt:.0%})"
    )


def find_duplicate(
    new_words: List[str],
    candidates: List[Tuple[object, List[str]]],
    *,
    new_total: Optional[float] = None,
    new_merchant: Optional[str] = None,
    candidate_totals: Optional[Dict] = None,
    candidate_merchants: Optional[Dict] = None,
    denylist: Optional[Set[str]] = None,
) -> Optional[Tuple[object, str]]:
    """Return ``(candidate_key, reason)`` of the first candidate that is the same
    transaction as ``new_words``, else ``None``. Used at upload time to flag a
    re-scan/reprint candidate. ``candidates`` is ``[(key, words), ...]``.
    """
    fp_new = transaction_fingerprint(new_words, new_total, new_merchant)
    if fp_new.is_empty():
        return None
    totals = candidate_totals or {}
    merchants = candidate_merchants or {}
    for key, words in candidates:
        fp = transaction_fingerprint(
            words, totals.get(key), merchants.get(key)
        )
        is_dup, reason = same_transaction(fp_new, fp, denylist=denylist)
        if is_dup:
            return key, reason
    return None
