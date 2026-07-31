"""Tender classification: cash vs card, network, last4, debit/credit.

Canonical port of the offline ``classify_tender.py`` analysis (see the
2026-07 tender/bank-match report). Operates on the receipt's payment
zone -- the PAYMENT + FOOTER sections when a PAYMENT section exists,
otherwise everything at or below the TOTAL_LINE (or the bottom 40% of
the receipt when no sections exist at all).

Two false-positive sources dominate a naive scan and are handled here:

- Footer boilerplate poisons a naive ``\\bCASH\\b`` scan ("No cash
  redemption unless required by law", "Sprouts cash-off offers"). A
  cash token only counts when it heads a tender line (``CASH``,
  ``CASH TENDER``, ...) or sits on a short line carrying an amount.
- OCR flattens the tender columns: ``CHANGE`` often prints on its own
  line with the card's own ``AMOUNT`` on the next line, so a
  multi-line window would read "CHANGE 93.41" and call a card receipt
  cash. A change amount only counts on the *same* line.

This module is deliberately stdlib-only: it is bundled into the
receipt summary updater Lambda zip as a FileAsset referencing this
file, so it must not import the rest of ``receipt_upload`` (or PIL).
Inputs are duck-typed: entities (``ReceiptLine``, ``ReceiptSection``,
``ReceiptWordLabel``, ``ReceiptWord``) or plain dicts both work.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

# Coarse tender classes persisted on ReceiptSummary.
TENDER_CASH = "cash"
TENDER_CARD = "card"
TENDER_UNKNOWN = "unknown"

# Fine-grained details (superset of the coarse classes).
DETAIL_CARD = "card"
DETAIL_CARD_GENERIC = "card_generic"
DETAIL_SPLIT = "split_or_ambiguous"
DETAIL_CASH = "cash"
DETAIL_UNKNOWN = "unknown"

_DETAIL_TO_CLASS = {
    DETAIL_CARD: TENDER_CARD,
    DETAIL_CARD_GENERIC: TENDER_CARD,
    DETAIL_SPLIT: TENDER_CARD,  # 51 of 54 observed carry a last4
    DETAIL_CASH: TENDER_CASH,
    DETAIL_UNKNOWN: TENDER_UNKNOWN,
}

# ------------------------------------------------------------------ regexes
NETWORKS = [
    ("AMEX", re.compile(r"\bAMERICAN\s+EXPRESS\b|\bAMEX\b|\bAMER\s*EXP\b")),
    ("DISCOVER", re.compile(r"\bDISCOVER\b|\bDISC\s*CARD\b")),
    (
        "MASTERCARD",
        re.compile(r"\bMASTER\s*CARD\b|\bMASTERCARD\b|\bMCARD\b"),
    ),
    ("VISA", re.compile(r"\bVISA\b")),
]
# bare "MC" only when it is clearly a tender token, never inside a
# merchant name
MC_BARE = re.compile(r"(?<![A-Z])MC(?![A-Z])")

DEBIT_RE = re.compile(
    r"\bUS\s*DEBIT\b|\bDEBIT\b|\bEFT\b|\bEFT/DEBIT\b|\bPIN\s*VERIFIED\b"
    r"|\bINTERAC\b"
)
CREDIT_RE = re.compile(
    r"\bCREDIT\b(?!\s*CARD\s*(?:PAYMENT\s*)?DUE)|\bCREDIT\s*CARD\b"
)

# last4: masked-PAN forms, then explicit "ending in"
LAST4_MASK = re.compile(r"(?:[X\*#•]\s?){3,}\s*(\d{4})(?!\d)")
LAST4_ENDING = re.compile(r"ENDING\s*(?:IN)?\s*[:#]?\s*(\d{4})(?!\d)")
LAST4_CARDNUM = re.compile(
    r"CARD\s*(?:#|NO|NUM(?:BER)?)?\s*[:.#]?\s*[X\*#]*\s*(\d{4})(?!\d)"
)

# CASH: \b keeps CASHIER out. Prose contexts are stripped before
# matching, and a hit must additionally look like a tender line.
CASH_RE = re.compile(r"\bCASH\b")
CASH_BACK_RE = re.compile(
    r"\bCASH\s*BACK\b|\bCASHBACK\b|\bCASH\s*REWARDS?\b"
    r"|\bNO\s+CASH\b|\bCASH[\s-]*OFF\b|\bFOR\s+CASH\b|\bCASH\s+VALUE\b"
    r"|\bCASH\s+OR\s+CHECKS?\b|\bCASH\s+REDEMPTION\b|\bCASH\s+ADVANCE\b"
    r"|\bREDEEMABLE\b|\bCASH\s+DISCOUNT\b"
)
CASH_TENDER_RE = re.compile(
    r"^\s*(?:CASH|CASH\s+TENDER(?:ED)?|CASH\s+PAYMENT|CASH\s+TOTAL"
    r"|TOTAL\s+CASH|CASH\s+SALE|CASH\s+AMOUNT|CASH\s+PAID)\b"
)
CHANGE_RE = re.compile(r"\bCHANGE\b(?!\s*(?:DUE\s*)?RETURN)")
MONEY = re.compile(r"-?\$?\s*(\d+\.\d{2})")

APPLE_PAY = re.compile(r"\bAPPLE\s*PAY\b|\bCONTACTLESS\b|\bTAP\b")

# card-ish tender words that mean "card" without naming the network
GENERIC_CARD = re.compile(
    r"\bCARD\b|\bCHIP\b|\bAID:\s*A\d|\bAPPROVED\b"
    r"|\bAUTH(?:ORIZATION)?\s*(?:#|CODE|NO)"
    r"|\bREF\s*#|\bTERMINAL\b|\bMERCHANT\s*ID\b|\bSWIPED?\b"
    r"|\bCONTACTLESS\b|\bTRACE\b"
)

# labels whose words are a strong independent tender signal
TENDER_LABELS = ("PAYMENT_METHOD", "TENDER", "CHANGE")


@dataclass(frozen=True)
class TenderClassification:
    """Result of classifying a receipt's tender.

    Attributes:
        tender_class: Coarse class -- ``cash``, ``card`` or ``unknown``.
        tender_detail: Fine class -- ``card``, ``card_generic``,
            ``split_or_ambiguous``, ``cash`` or ``unknown``.
        card_network: ``AMEX``/``DISCOVER``/``MASTERCARD``/``VISA`` or
            None.
        card_last4: Last four PAN digits when printed, else None.
        card_kind: ``debit``/``credit`` or None when unstated.
    """

    tender_class: str
    tender_detail: str
    card_network: str | None = None
    card_last4: str | None = None
    card_kind: str | None = None


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from an entity attribute or a dict key."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _line_y(line: Any) -> float | None:
    """Normalized bottom-origin y of a line (bounding-box origin)."""
    y = _get(line, "y")
    if y is not None:
        return float(y)
    bbox = _get(line, "bounding_box") or {}
    y = bbox.get("y") if isinstance(bbox, dict) else None
    return float(y) if y is not None else None


def payment_zone_texts(
    lines: Iterable[Any],
    sections: Iterable[Any],
) -> tuple[list[str], bool]:
    """Select the payment-zone line texts for a receipt.

    PAYMENT + FOOTER sections; if no PAYMENT section, everything at or
    below the TOTAL_LINE; failing that, the bottom 40% of the receipt.

    Args:
        lines: ReceiptLine entities or dicts with ``line_id``, ``text``
            and ``bounding_box``/``y``.
        sections: ReceiptSection entities or dicts with
            ``section_type`` and ``line_ids``.

    Returns:
        Tuple of (zone texts in line order, has_payment_section).
    """
    by_id = {int(_get(l, "line_id")): l for l in lines}
    ids: set[int] = set()
    has_payment = False
    total_min_y: float | None = None
    for section in sections:
        stype = str(_get(section, "section_type", "") or "").upper()
        line_ids = [int(i) for i in (_get(section, "line_ids") or [])]
        if stype == "PAYMENT":
            has_payment = True
            ids |= set(line_ids)
        elif stype == "FOOTER":
            ids |= set(line_ids)
        elif stype == "TOTAL_LINE":
            ys = [
                _line_y(by_id[i])
                for i in line_ids
                if i in by_id and _line_y(by_id[i]) is not None
            ]
            if ys:
                low = min(ys)
                total_min_y = (
                    low if total_min_y is None else min(total_min_y, low)
                )
    if not has_payment:
        if total_min_y is not None:
            for line_id, line in by_id.items():
                y = _line_y(line)
                if y is not None and y <= total_min_y:
                    ids.add(line_id)
        elif not ids:
            # no PAYMENT, no FOOTER, no TOTAL_LINE -> bottom 40%
            for line_id, line in by_id.items():
                y = _line_y(line)
                if y is not None and y <= 0.40:
                    ids.add(line_id)
    texts = [
        str(_get(by_id[i], "text") or "") for i in sorted(ids) if i in by_id
    ]
    return texts, has_payment


def classify_tender(
    zone_texts: Sequence[str],
    labeled_words: Sequence[tuple[str, str]] = (),
) -> TenderClassification:
    """Classify tender from payment-zone line texts.

    Args:
        zone_texts: Payment-zone line texts, in reading order.
        labeled_words: Optional ``(label, word_text)`` pairs for words
            labelled PAYMENT_METHOD / TENDER / CHANGE -- a strong
            independent signal.

    Returns:
        A TenderClassification.
    """
    texts = [str(t) for t in zone_texts]
    zone = "\n".join(texts).upper()
    lab_text = " ".join(t for _, t in labeled_words).upper()
    hay = zone + "\n" + lab_text

    # --- last4
    last4 = None
    for regex in (LAST4_MASK, LAST4_CARDNUM, LAST4_ENDING):
        match = regex.search(hay)
        if match:
            last4 = match.group(1)
            break

    # --- network
    network = None
    for name, regex in NETWORKS:
        if regex.search(hay):
            network = name
            break
    if (
        network is None
        and MC_BARE.search(hay)
        and (last4 or GENERIC_CARD.search(hay))
    ):
        network = "MASTERCARD"

    # --- cash signals: only count CASH that reads as a tender line
    has_cash_word = False
    for i, text in enumerate(texts):
        upper = CASH_BACK_RE.sub(" ", text.upper())
        if not CASH_RE.search(upper):
            continue
        window = " ".join(texts[i : i + 2])
        if CASH_TENDER_RE.search(upper) or (
            len(upper.split()) <= 4 and MONEY.search(window)
        ):
            has_cash_word = True
    for label, text in labeled_words:
        if label in ("PAYMENT_METHOD", "TENDER") and CASH_RE.search(
            CASH_BACK_RE.sub(" ", text.upper())
        ):
            has_cash_word = True

    # CHANGE with a nonzero amount on the SAME line => cash tender.
    change_nonzero = False
    for text in texts:
        upper = text.upper()
        if CHANGE_RE.search(upper):
            amounts = [float(a) for a in MONEY.findall(upper)]
            if any(a > 0 for a in amounts):
                change_nonzero = True
    for label, text in labeled_words:
        if label == "CHANGE":
            amounts = MONEY.findall(text)
            if amounts and float(amounts[0]) > 0:
                change_nonzero = True

    debit = bool(DEBIT_RE.search(hay))
    credit = bool(CREDIT_RE.search(hay)) and not debit
    card_evidence = bool(network or last4 or debit or APPLE_PAY.search(hay))
    generic_card = bool(GENERIC_CARD.search(hay))

    # --- decide
    if card_evidence and (has_cash_word or change_nonzero):
        detail = DETAIL_SPLIT
    elif card_evidence:
        detail = DETAIL_CARD
    elif has_cash_word or change_nonzero:
        detail = DETAIL_CASH
    elif generic_card:
        detail = DETAIL_CARD_GENERIC
    else:
        detail = DETAIL_UNKNOWN

    kind = "debit" if debit else ("credit" if credit else None)
    return TenderClassification(
        tender_class=_DETAIL_TO_CLASS[detail],
        tender_detail=detail,
        card_network=network,
        card_last4=last4,
        card_kind=kind,
    )


def classify_tender_for_receipt(
    lines: Iterable[Any],
    sections: Iterable[Any],
    word_labels: Iterable[Any] = (),
    words: Iterable[Any] = (),
) -> TenderClassification:
    """Classify tender for a receipt from its entities.

    Args:
        lines: ReceiptLine entities (or dicts).
        sections: ReceiptSection entities (or dicts).
        word_labels: ReceiptWordLabel entities (or dicts) -- only
            PAYMENT_METHOD / TENDER / CHANGE labels are used.
        words: ReceiptWord entities (or dicts), used to resolve the
            labelled words' text.

    Returns:
        A TenderClassification.
    """
    zone_texts, _ = payment_zone_texts(lines, sections)
    word_text: dict[tuple[int, int], str] = {
        (int(_get(w, "line_id")), int(_get(w, "word_id"))): str(
            _get(w, "text") or ""
        )
        for w in words
    }
    labeled: list[tuple[str, str]] = []
    for label in word_labels:
        name = str(_get(label, "label") or "")
        if name not in TENDER_LABELS:
            continue
        key = (int(_get(label, "line_id")), int(_get(label, "word_id")))
        text = word_text.get(key)
        if text:
            labeled.append((name, text))
    return classify_tender(zone_texts, labeled)
