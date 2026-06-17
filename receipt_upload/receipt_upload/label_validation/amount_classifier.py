"""Conservative upload-time classification for generic AMOUNT labels."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from receipt_dynamo.amounts import parse_receipt_amount


_TOTAL_RE = re.compile(r"\b(total|amount\s+due|balance|authorized)\b", re.I)
_SUBTOTAL_RE = re.compile(r"\bsub[-\s]?total\b", re.I)
_TAX_RE = re.compile(r"\b(tax|vat)\b", re.I)
_SUMMARY_RE = re.compile(
    r"\b(total|sub[-\s]?total|tax|vat|amount\s+due|balance|authorized)\b",
    re.I,
)


@dataclass(frozen=True)
class AmountClassification:
    """A deterministic label decision for a generic amount word."""

    label: str
    reason: str


@dataclass(frozen=True)
class _AmountCandidate:
    line_id: int
    word_id: int
    value: float
    text: str
    line_text: str


def classify_amount_labels(
    words: Iterable[object],
    word_labels: Iterable[object],
) -> dict[tuple[int, int], AmountClassification]:
    """Classify safe generic ``AMOUNT`` labels into core financial labels.

    The classifier intentionally leaves ambiguous item prices alone. It only
    returns decisions when nearby keywords or exact receipt math make the label
    clear.
    """
    candidates = _amount_candidates(words, word_labels)
    if not candidates:
        return {}

    decisions: dict[tuple[int, int], AmountClassification] = {}

    # Keyword-adjacent summary labels are the strongest cheap signal.
    for candidate in candidates:
        key = (candidate.line_id, candidate.word_id)
        line_text = candidate.line_text
        if _SUBTOTAL_RE.search(line_text):
            decisions[key] = AmountClassification(
                label="SUBTOTAL",
                reason="AMOUNT appeared on a subtotal line.",
            )
        elif _TAX_RE.search(line_text):
            decisions[key] = AmountClassification(
                label="TAX",
                reason="AMOUNT appeared on a tax line.",
            )
        elif _TOTAL_RE.search(line_text):
            decisions[key] = AmountClassification(
                label="GRAND_TOTAL",
                reason="AMOUNT appeared on a total/amount-due line.",
            )

    decisions.update(_classify_summary_equation(candidates, decisions))
    decisions.update(_classify_line_totals(candidates, decisions))

    return decisions


def _amount_candidates(
    words: Iterable[object],
    word_labels: Iterable[object],
) -> list[_AmountCandidate]:
    word_lookup = {
        (getattr(word, "line_id", None), getattr(word, "word_id", None)): word
        for word in words
    }
    line_words: dict[int, list[object]] = {}
    for word in words:
        line_id = getattr(word, "line_id", None)
        if line_id is None:
            continue
        line_words.setdefault(line_id, []).append(word)
    for line in line_words.values():
        line.sort(key=lambda word: getattr(word, "word_id", 0))

    candidates: list[_AmountCandidate] = []
    seen: set[tuple[int, int]] = set()
    for label in word_labels:
        if getattr(label, "label", None) != "AMOUNT":
            continue
        key = (getattr(label, "line_id", None), getattr(label, "word_id", None))
        if key in seen:
            continue
        word = word_lookup.get(key)
        if word is None:
            continue
        value = parse_receipt_amount(getattr(word, "text", ""))
        if value is None:
            continue
        line_id, word_id = key
        if line_id is None or word_id is None:
            continue
        line_text = " ".join(
            str(getattr(w, "text", "")) for w in line_words.get(line_id, [])
        )
        candidates.append(
            _AmountCandidate(
                line_id=int(line_id),
                word_id=int(word_id),
                value=value,
                text=str(getattr(word, "text", "")),
                line_text=line_text,
            )
        )
        seen.add(key)

    return sorted(candidates, key=lambda c: (c.line_id, c.word_id))


def _classify_summary_equation(
    candidates: list[_AmountCandidate],
    decisions: dict[tuple[int, int], AmountClassification],
) -> dict[tuple[int, int], AmountClassification]:
    by_label: dict[str, list[_AmountCandidate]] = {}
    for candidate in candidates:
        decision = decisions.get((candidate.line_id, candidate.word_id))
        if decision:
            by_label.setdefault(decision.label, []).append(candidate)

    additions: dict[tuple[int, int], AmountClassification] = {}
    subtotals = by_label.get("SUBTOTAL", [])
    taxes = by_label.get("TAX", [])
    totals = by_label.get("GRAND_TOTAL", [])

    for subtotal in subtotals:
        for tax in taxes:
            expected_total = subtotal.value + tax.value
            matches = [
                c
                for c in candidates
                if (c.line_id, c.word_id) not in decisions
                and _same_amount(c.value, expected_total)
                and c.line_id >= max(subtotal.line_id, tax.line_id)
            ]
            if len(matches) == 1:
                additions[(matches[0].line_id, matches[0].word_id)] = (
                    AmountClassification(
                        label="GRAND_TOTAL",
                        reason="SUBTOTAL + TAX matched this AMOUNT exactly.",
                    )
                )

    for subtotal in subtotals:
        for total in totals:
            expected_tax = total.value - subtotal.value
            matches = [
                c
                for c in candidates
                if (c.line_id, c.word_id) not in decisions
                and (c.line_id, c.word_id) not in additions
                and expected_tax >= 0
                and _same_amount(c.value, expected_tax)
                and subtotal.line_id <= c.line_id <= total.line_id
            ]
            if len(matches) == 1:
                additions[(matches[0].line_id, matches[0].word_id)] = (
                    AmountClassification(
                        label="TAX",
                        reason="GRAND_TOTAL - SUBTOTAL matched this AMOUNT exactly.",
                    )
                )

    return additions


def _classify_line_totals(
    candidates: list[_AmountCandidate],
    decisions: dict[tuple[int, int], AmountClassification],
) -> dict[tuple[int, int], AmountClassification]:
    subtotals = [
        c
        for c in candidates
        if decisions.get((c.line_id, c.word_id))
        and decisions[(c.line_id, c.word_id)].label == "SUBTOTAL"
    ]
    if len(subtotals) != 1:
        return {}

    subtotal = subtotals[0]
    line_total_candidates = [
        c
        for c in candidates
        if (c.line_id, c.word_id) not in decisions
        and c.line_id < subtotal.line_id
        and c.value > 0
        and not _SUMMARY_RE.search(c.line_text)
    ]
    if not line_total_candidates:
        return {}

    total = sum(c.value for c in line_total_candidates)
    if not _same_amount(total, subtotal.value):
        return {}

    return {
        (c.line_id, c.word_id): AmountClassification(
            label="LINE_TOTAL",
            reason="Item AMOUNTs before SUBTOTAL summed exactly to SUBTOTAL.",
        )
        for c in line_total_candidates
    }


def _same_amount(left: float, right: float) -> bool:
    return abs(left - right) <= 0.01
