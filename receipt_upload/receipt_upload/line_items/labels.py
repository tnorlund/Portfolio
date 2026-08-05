"""Deterministic ReceiptWordLabel derivation from the decoded line items.

The band-block decoder already knows, word for word, which words are a
product's name, which word carries its extended price, and which words
carry the quantity and unit price -- ``parse_band`` returns
``name_word_ids`` / ``price_word_id`` / ``qty_word_ids`` alongside every
item it emits. Nothing wrote that knowledge down, so receipts ingested by
the Swift worker end up with header/footer metadata labels and zero
product or financial labels.

This module turns the decode into label proposals. It mints nothing on
its own judgement: every proposal points at a word the decoder already
identified, or at a printed summary figure that equals the receipt's
stored summary value to the cent.

The gate is arithmetic, not heuristic. Labels are derived only when the
decoded items reconcile to the receipt's printed baseline as a full
``match`` (``near`` never qualifies, however small the band), which means
the name-to-price pairing is arithmetically proven: change any pairing
and the sum stops landing on the printed figure. ``require_proven``
tightens the gate to the PROVEN tier (``geometry.is_proven``), where the
printed total also agrees with the settled bank amount to the cent.

Writing is deliberately not this module's job -- it returns proposals and
lets the caller decide, so the same derivation serves a backfill script,
an MCP tool and (later) the ingest path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from receipt_dynamo.amounts import (
    looks_like_receipt_amount,
    parse_receipt_amount,
)
from receipt_dynamo.entities.receipt_summary import (
    find_printed_grand_total_words,
    find_printed_subtotal_words,
    find_printed_tax_words,
)

from receipt_upload.line_items.geometry import (
    extract_items,
    is_proven,
    reconcile_detailed,
)

# The provenance marker for every label this module derives. Distinct from
# every LLM proposer ("llm_valid", "llm_needs_review", "*_analyzer_llm")
# so a later sweep can find, audit or retract exactly this population.
DECODER_PROPOSED_BY = "decoder_reconciled"

# Cent tolerance when matching a printed word's amount to the stored
# summary figure. Both sides are printed money, so this is exact-match
# slack for float representation only, not a comparison band.
_CENT = 0.005

# Gate verdicts (why a receipt did or did not produce labels).
GATE_OK = "ok"
GATE_NO_ITEMS_SECTION = "no-items-section"
GATE_NO_ITEMS = "no-items"
GATE_NOT_MATCHED = "not-matched"
GATE_COLLAPSED_BANDING = "collapsed-banding"
GATE_NOT_PROVEN = "not-proven"


@dataclass(frozen=True)
class DerivedLabel:
    """One label proposal: a word, a label, and why the decoder said so."""

    line_id: int
    word_id: int
    label: str
    reasoning: str
    text: str = ""

    @property
    def word_key(self) -> tuple[int, int]:
        """The (line_id, word_id) this proposal targets."""
        return (self.line_id, self.word_id)


@dataclass
class DerivationResult:
    """The outcome of deriving labels for one receipt."""

    gate: str
    labels: list[DerivedLabel] = field(default_factory=list)
    reconciliation_status: Optional[str] = None
    baseline_figures_agreeing: Optional[int] = None
    item_sum: Optional[float] = None
    baseline: Optional[float] = None
    item_count: int = 0

    @property
    def derived(self) -> bool:
        """Whether the receipt passed the gate and produced proposals."""
        return self.gate == GATE_OK


def _word_key(ref: Any) -> Optional[tuple[int, int]]:
    """Normalize a decoder word reference to (line_id, word_id)."""
    if not ref:
        return None
    try:
        return (int(ref["line_id"]), int(ref["word_id"]))
    except (KeyError, TypeError, ValueError):
        return None


def _to_geometry_word(word: Any) -> Optional[dict[str, Any]]:
    """Adapt a ReceiptWord entity to the decoder's word-dict shape."""
    box = getattr(word, "bounding_box", None) or {}
    try:
        height = float(box.get("height", 0.0))
        return {
            "line_id": int(word.line_id),
            "word_id": int(word.word_id),
            "text": str(word.text),
            "x": float(box.get("x", 0.0)),
            "y_mid": float(box.get("y", 0.0)) + height / 2.0,
            "h": height,
        }
    except (AttributeError, TypeError, ValueError):
        return None


def _has_letters(text: str) -> bool:
    """Whether a token carries alphabetic content."""
    return bool(re.search(r"[A-Za-z]", text or ""))


def _amount_of(text: str) -> Optional[float]:
    """Parse a word as a printed amount, or None."""
    if not looks_like_receipt_amount(text):
        return None
    return parse_receipt_amount(text)


def _numeric_of(text: str) -> Optional[float]:
    """Parse a word as a bare number ("2", "2x", "1.31"), or None."""
    match = re.match(r"^\s*(\d+(?:\.\d+)?)", text or "")
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _summary_value(summary: Optional[dict], key: str) -> Optional[float]:
    """Read one numeric figure off the stored receipt summary."""
    if not summary:
        return None
    value = summary.get(key)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _item_labels(
    item: dict, texts: dict[tuple[int, int], str]
) -> list[DerivedLabel]:
    """Label proposals for one decoded item."""
    out: list[DerivedLabel] = []
    name = item.get("name") or ""
    price = item.get("price")
    is_discount = bool(item.get("is_discount"))

    # The extended price word. A discount row's price IS the discount
    # amount, so it takes DISCOUNT rather than LINE_TOTAL -- labelling it
    # LINE_TOTAL would teach the model that a negative line total is
    # normal and would double-count in every downstream sum.
    price_key = _word_key(item.get("price_word_id"))
    if price_key is not None and price is not None:
        label = "DISCOUNT" if is_discount else "LINE_TOTAL"
        out.append(
            DerivedLabel(
                line_id=price_key[0],
                word_id=price_key[1],
                label=label,
                reasoning=(
                    f"Decoder paired this amount with item {name!r} at "
                    f"{price:.2f}; the receipt's items reconcile to its "
                    "printed baseline"
                ),
                text=texts.get(price_key, ""),
            )
        )

    # Product-name words. "low" name quality means the decoder recovered a
    # price with no readable name, so there is nothing to name. Words with
    # no letters are skipped: PRODUCT_NAME is descriptive text, and a bare
    # numeric token in a name span is a SKU echo or an unrecognized
    # quantity, never a product name.
    if not is_discount and item.get("name_quality") != "low" and name:
        priced = f" priced at {price:.2f}" if price is not None else ""
        reasoning = (
            f"Decoder read this word as part of the product name "
            f"{name!r}{priced}; the receipt's items reconcile to its "
            "printed baseline"
        )
        seen: set[tuple[int, int]] = set()
        for ref in item.get("name_word_ids") or []:
            key = _word_key(ref)
            if key is None or key in seen:
                continue
            seen.add(key)
            text = texts.get(key, "")
            if not _has_letters(text):
                continue
            out.append(
                DerivedLabel(
                    line_id=key[0],
                    word_id=key[1],
                    label="PRODUCT_NAME",
                    reasoning=reasoning,
                    text=text,
                )
            )

    out.extend(_quantity_labels(item, texts))
    return out


def _quantity_labels(
    item: dict, texts: dict[tuple[int, int], str]
) -> list[DerivedLabel]:
    """QUANTITY / UNIT_PRICE proposals from the decoder's quantity span.

    ``qty_word_ids`` covers the whole quantity expression ("2 @ 8.99",
    a bare leading "3"), so the two roles are separated by value: the word
    whose printed amount equals the parsed unit price is UNIT_PRICE, and
    the word whose number equals the parsed quantity is QUANTITY. When a
    role has no unambiguous single word, nothing is minted for it -- the
    span is evidence, not a guess.
    """
    quantity = item.get("quantity")
    unit_price = item.get("unit_price")
    keys = [
        key
        for key in (_word_key(ref) for ref in item.get("qty_word_ids") or [])
        if key is not None
    ]
    if not keys or quantity is None:
        return []

    unit_keys = (
        [
            key
            for key in keys
            if (amount := _amount_of(texts.get(key, ""))) is not None
            and abs(amount - unit_price) < _CENT
        ]
        if unit_price is not None
        else []
    )
    qty_keys = [
        key
        for key in keys
        if key not in unit_keys
        and (value := _numeric_of(texts.get(key, ""))) is not None
        and abs(value - quantity) < 1e-9
    ]

    out: list[DerivedLabel] = []
    if len(qty_keys) == 1:
        key = qty_keys[0]
        out.append(
            DerivedLabel(
                line_id=key[0],
                word_id=key[1],
                label="QUANTITY",
                reasoning=(
                    f"Decoder parsed quantity {quantity:g} for item "
                    f"{item.get('name') or ''!r} from this word"
                ),
                text=texts.get(key, ""),
            )
        )
    if len(unit_keys) == 1 and unit_price is not None:
        key = unit_keys[0]
        out.append(
            DerivedLabel(
                line_id=key[0],
                word_id=key[1],
                label="UNIT_PRICE",
                reasoning=(
                    f"Decoder parsed unit price {unit_price:.2f} x "
                    f"{quantity:g} for item {item.get('name') or ''!r}"
                ),
                text=texts.get(key, ""),
            )
        )
    return out


def _summary_labels(
    receipt_words: Sequence[Any], summary: Optional[dict]
) -> list[DerivedLabel]:
    """GRAND_TOTAL / SUBTOTAL / TAX proposals for printed summary words.

    A word is labelled only when it sits on a summary anchor row AND its
    printed amount equals the receipt's stored summary figure to the cent,
    and only when exactly one anchored word carries that value. Two words
    printing the same figure (a total echoed by a tender row) are
    ambiguous, and ambiguity mints nothing.
    """
    out: list[DerivedLabel] = []
    finders = (
        ("GRAND_TOTAL", "grand_total", find_printed_grand_total_words),
        ("SUBTOTAL", "subtotal", find_printed_subtotal_words),
        ("TAX", "tax", find_printed_tax_words),
    )
    for label, key, finder in finders:
        value = _summary_value(summary, key)
        if value is None:
            continue
        matches = [
            word
            for amount, word in finder(list(receipt_words))
            if abs(amount - value) < _CENT
        ]
        unique = {(int(w.line_id), int(w.word_id)): w for w in matches}
        if len(unique) != 1:
            continue
        (line_id, word_id), word = next(iter(unique.items()))
        out.append(
            DerivedLabel(
                line_id=line_id,
                word_id=word_id,
                label=label,
                reasoning=(
                    f"Printed {label.replace('_', ' ').lower()} row carries "
                    f"{value:.2f}, matching the receipt summary; the "
                    "decoded items reconcile to the printed baseline"
                ),
                text=str(word.text),
            )
        )
    return out


def derive_labels(
    receipt_words: Sequence[Any],
    items_line_ids: Iterable[int],
    summary: Optional[dict],
    *,
    require_proven: bool = False,
    printed_total: Optional[float] = None,
    bank_amount: Optional[float] = None,
) -> DerivationResult:
    """Derive word labels from the reconciled line-item decode.

    Args:
        receipt_words: The receipt's ``ReceiptWord`` entities.
        items_line_ids: Line ids of the receipt's ITEMS section.
        summary: The stored receipt summary ({subtotal, tax, grand_total,
            ...}), used both as the reconciliation baseline and as the
            cross-check for the printed summary figures.
        require_proven: Tighten the gate to the PROVEN tier -- the
            printed total must also agree with the settled bank amount to
            the cent (``geometry.is_proven``).
        printed_total: The printed grand total, for the PROVEN check.
            Defaults to the summary's grand total.
        bank_amount: The settled bank amount, for the PROVEN check.

    Returns:
        A :class:`DerivationResult`. ``labels`` is empty unless ``gate``
        is :data:`GATE_OK`.
    """
    line_ids = {int(x) for x in items_line_ids}
    if not line_ids:
        return DerivationResult(GATE_NO_ITEMS_SECTION)

    words = [w for w in (_to_geometry_word(w) for w in receipt_words) if w]
    texts = {(w["line_id"], w["word_id"]): w["text"] for w in words}

    items, collapsed = extract_items(words, line_ids, summary=summary)
    recon = reconcile_detailed(
        [item for item in items if not item.get("is_discount")], summary
    )
    result = DerivationResult(
        gate=GATE_OK,
        reconciliation_status=recon.status,
        baseline_figures_agreeing=recon.baseline_figures_agreeing,
        item_sum=recon.item_sum,
        baseline=recon.baseline,
        item_count=len(items),
    )

    if not items:
        result.gate = GATE_NO_ITEMS
        return result
    if recon.status != "match":
        result.gate = GATE_NOT_MATCHED
        return result
    # Degenerate banding merged several prices into one band, so the
    # word-to-item mapping is not trustworthy even when the sum lands.
    if collapsed:
        result.gate = GATE_COLLAPSED_BANDING
        return result
    if require_proven:
        total = (
            printed_total
            if printed_total is not None
            else _summary_value(summary, "grand_total")
        )
        if not is_proven(recon.status, total, bank_amount):
            result.gate = GATE_NOT_PROVEN
            return result

    labels: list[DerivedLabel] = []
    for item in items:
        labels.extend(_item_labels(item, texts))
    labels.extend(_summary_labels(receipt_words, summary))

    # One word, one derived label. A word the decoder claims twice (a
    # price that is also a summary figure) is ambiguous, and ambiguity
    # mints nothing.
    by_word: dict[tuple[int, int], list[DerivedLabel]] = {}
    for proposal in labels:
        by_word.setdefault(proposal.word_key, []).append(proposal)
    result.labels = [
        proposals[0]
        for _, proposals in sorted(by_word.items())
        if len({p.label for p in proposals}) == 1
    ]
    return result


__all__ = [
    "DECODER_PROPOSED_BY",
    "DerivationResult",
    "DerivedLabel",
    "GATE_COLLAPSED_BANDING",
    "GATE_NOT_MATCHED",
    "GATE_NOT_PROVEN",
    "GATE_NO_ITEMS",
    "GATE_NO_ITEMS_SECTION",
    "GATE_OK",
    "derive_labels",
]
