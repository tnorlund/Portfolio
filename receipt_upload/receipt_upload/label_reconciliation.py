"""Deterministic post-LayoutLM label reconciliation (D3)."""

# The planner intentionally keeps the ordered audit rules together so their
# deterministic projection is reviewable as one pass.
# pylint: disable=too-many-statements,too-many-nested-blocks
# pylint: disable=too-many-boolean-expressions

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Protocol, Sequence

from receipt_dynamo.amounts import (
    looks_like_receipt_amount,
    parse_receipt_amount,
)
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.shared_exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
)
from receipt_dynamo.entities import (
    ReceiptLabelReconciliation,
    ReceiptRow,
    ReceiptSection,
    ReceiptWord,
    ReceiptWordLabel,
)

MODEL_SOURCE = "upload-determinism-v1"
_FINANCIAL = {
    "GRAND_TOTAL",
    "SUBTOTAL",
    "TAX",
    "TIP",
    "LINE_TOTAL",
    "UNIT_PRICE",
    "DISCOUNT",
    "CHANGE",
    "CASH_BACK",
    "REFUND",
}
_SECTION_FOOTPRINT = {
    "PRODUCT_NAME": {"ITEMS"},
    "QUANTITY": {"ITEMS"},
    "UNIT_PRICE": {"ITEMS"},
    "LINE_TOTAL": {"ITEMS"},
    "SUBTOTAL": {"SUMMARY"},
    "TAX": {"SUMMARY"},
    "GRAND_TOTAL": {"SUMMARY", "TOTAL_LINE"},
    "DISCOUNT": {"ITEMS", "SUMMARY"},
    "CHANGE": {"PAYMENT"},
    "CASH_BACK": {"PAYMENT"},
    "REFUND": {"PAYMENT"},
    "PAYMENT_METHOD": {"PAYMENT"},
}


class ReconciliationDynamo(Protocol):
    """Narrow DynamoDB interface used by the reconciler."""

    def list_receipt_words_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptWord]:
        """Return receipt words."""
        raise NotImplementedError

    def list_receipt_word_labels_for_receipt(
        self, image_id: str, receipt_id: int
    ) -> tuple[list[ReceiptWordLabel], dict[str, Any] | None]:
        """Return receipt word labels."""
        raise NotImplementedError

    def get_receipt_rows_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptRow]:
        """Return materialized visual rows."""
        raise NotImplementedError

    def get_receipt_sections_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptSection]:
        """Return deterministic receipt sections."""
        raise NotImplementedError

    def add_receipt_word_label(self, label: ReceiptWordLabel) -> None:
        """Add one corrected label."""
        raise NotImplementedError

    def update_receipt_word_label(self, label: ReceiptWordLabel) -> None:
        """Update one original label annotation."""
        raise NotImplementedError

    def put_receipt_label_reconciliation(
        self, reconciliation: ReceiptLabelReconciliation
    ) -> None:
        """Persist one reconciliation artifact."""
        raise NotImplementedError

    def get_receipt_label_reconciliation(
        self, image_id: str, receipt_id: int
    ) -> ReceiptLabelReconciliation:
        """Return a prior reconciliation artifact."""
        raise NotImplementedError


@dataclass(frozen=True)
class ReconciliationPlan:
    """Pure output of one deterministic reconciliation pass."""

    corrections: list[dict[str, Any]]
    checks: list[dict[str, Any]]

    @property
    def conflict_count(self) -> int:
        """Count corrections intentionally routed to review."""

        return sum(bool(item.get("conflict")) for item in self.corrections)


@dataclass(frozen=True)
class _RowContext:
    row_id: int
    line_ids: tuple[int, ...]
    words: tuple[ReceiptWord, ...]
    text: str
    section: str | None
    section_evidence: dict[str, Any]


def _status(label: ReceiptWordLabel) -> str:
    return str(label.validation_status or ValidationStatus.NONE.value).upper()


def _active(label: ReceiptWordLabel) -> bool:
    return _status(label) != ValidationStatus.INVALID.value


def _cents(text: str) -> int | None:
    value = parse_receipt_amount(text)
    return None if value is None else int(round(value * 100))


def _merchant_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def _row_contexts(
    words: Sequence[ReceiptWord],
    rows: Sequence[ReceiptRow],
    sections: Sequence[ReceiptSection],
) -> list[_RowContext]:
    words_by_line: dict[int, list[ReceiptWord]] = {}
    for word in words:
        words_by_line.setdefault(word.line_id, []).append(word)
    for line_words in words_by_line.values():
        line_words.sort(key=lambda word: word.word_id)

    section_by_line: dict[int, ReceiptSection] = {}
    for section in sections:
        for line_id in section.line_ids:
            section_by_line[line_id] = section

    contexts = []
    covered: set[int] = set()
    row_specs = [
        (row.row_id, tuple(row.line_ids))
        for row in sorted(rows, key=lambda r: r.row_id)
    ]
    covered_members = {
        member for _, members in row_specs for member in members
    }
    row_specs.extend(
        (line_id, (line_id,))
        for line_id in sorted(words_by_line)
        if line_id not in covered_members
    )
    for row_id, line_ids in row_specs:
        if any(line_id in covered for line_id in line_ids):
            continue
        covered.update(line_ids)
        row_words = tuple(
            word
            for line_id in line_ids
            for word in words_by_line.get(line_id, [])
        )
        if not row_words:
            continue
        section = next(
            (
                section_by_line[line_id]
                for line_id in line_ids
                if line_id in section_by_line
            ),
            None,
        )
        evidence: dict[str, Any] = {}
        if section is not None:
            evidence = {
                "section_type": section.section_type,
                "section_confidence": section.confidence,
                "section_model_source": section.model_source,
                "section_validation_status": section.validation_status,
            }
        contexts.append(
            _RowContext(
                row_id=row_id,
                line_ids=line_ids,
                words=row_words,
                text=" ".join(word.text for word in row_words),
                section=section.section_type if section else None,
                section_evidence=evidence,
            )
        )
    return contexts


def _event_id(event: dict[str, Any]) -> str:
    stable = {
        name: event.get(name)
        for name in (
            "line_id",
            "word_id",
            "original_label",
            "corrected_label",
            "action",
            "rule_id",
            "provenance",
            "conflict",
        )
    }
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True).encode("utf-8")
    ).hexdigest()[:20]


def _record(
    *,
    key: tuple[int, int],
    original: ReceiptWordLabel | None,
    corrected_label: str | None,
    action: str,
    provenance: str,
    rule_id: str,
    confidence: float,
    reason: str,
    evidence: dict[str, Any],
    conflict: bool,
    original_new_status: str | None = None,
    corrected_status: str | None = None,
) -> dict[str, Any]:
    event = {
        "line_id": key[0],
        "word_id": key[1],
        "original_label": original.label if original else None,
        "original_status": _status(original) if original else None,
        "original_reasoning": original.reasoning if original else None,
        "original_proposer": original.label_proposed_by if original else None,
        "original_new_status": original_new_status,
        "corrected_label": corrected_label,
        "corrected_status": corrected_status,
        "action": action,
        "provenance": provenance,
        "rule_id": rule_id,
        "confidence": float(confidence),
        "reason": reason,
        "evidence": evidence,
        "conflict": conflict,
        "model_source": MODEL_SOURCE,
    }
    event["event_id"] = _event_id(event)
    return event


def plan_label_reconciliation(
    words: Sequence[ReceiptWord],
    labels: Sequence[ReceiptWordLabel],
    rows: Sequence[ReceiptRow],
    sections: Sequence[ReceiptSection],
    merchant_name: str | None,
) -> ReconciliationPlan:
    """Plan section, arithmetic, and merchant-template corrections."""

    labels_by_key: dict[tuple[int, int], list[ReceiptWordLabel]] = {}
    words_by_key = {(word.line_id, word.word_id): word for word in words}
    for label in labels:
        labels_by_key.setdefault((label.line_id, label.word_id), []).append(
            label
        )
    contexts = _row_contexts(words, rows, sections)
    row_by_line = {
        line_id: context
        for context in contexts
        for line_id in context.line_ids
    }
    corrections: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    handled: set[tuple[int, int, str]] = set()

    def propose(
        key: tuple[int, int],
        desired: str,
        *,
        provenance: str,
        rule_id: str,
        reason: str,
        evidence: dict[str, Any],
        confidence: float = 1.0,
    ) -> None:
        active = [
            label for label in labels_by_key.get(key, []) if _active(label)
        ]
        if any(label.label == desired for label in active):
            checks.append(
                {
                    "check_id": rule_id,
                    "status": "CONFIRMED",
                    "line_id": key[0],
                    "word_id": key[1],
                    "label": desired,
                    "provenance": provenance,
                }
            )
            handled.add((key[0], key[1], desired))
            return
        differing = [label for label in active if label.label != desired]
        human_conflict = [
            label
            for label in differing
            if _status(label) == ValidationStatus.VALID.value
        ]
        if human_conflict:
            for label in human_conflict:
                corrections.append(
                    _record(
                        key=key,
                        original=label,
                        corrected_label=desired,
                        action="CONFLICT",
                        provenance=provenance,
                        rule_id=rule_id,
                        confidence=confidence,
                        reason=reason,
                        evidence=evidence,
                        conflict=True,
                        original_new_status=(
                            ValidationStatus.NEEDS_REVIEW.value
                        ),
                    )
                )
                handled.add((key[0], key[1], label.label))
            return
        first = True
        for label in differing:
            corrections.append(
                _record(
                    key=key,
                    original=label,
                    corrected_label=desired if first else None,
                    action="CORRECT" if first else "DEMOTE",
                    provenance=provenance,
                    rule_id=rule_id,
                    confidence=confidence,
                    reason=reason,
                    evidence=evidence,
                    conflict=False,
                    original_new_status=ValidationStatus.INVALID.value,
                    corrected_status=(
                        ValidationStatus.VALID.value if first else None
                    ),
                )
            )
            handled.add((key[0], key[1], label.label))
            first = False
        if not differing:
            corrections.append(
                _record(
                    key=key,
                    original=None,
                    corrected_label=desired,
                    action="ADD",
                    provenance=provenance,
                    rule_id=rule_id,
                    confidence=confidence,
                    reason=reason,
                    evidence=evidence,
                    conflict=False,
                    corrected_status=ValidationStatus.VALID.value,
                )
            )
        handled.add((key[0], key[1], desired))

    def flag(
        label: ReceiptWordLabel,
        *,
        provenance: str,
        rule_id: str,
        reason: str,
        evidence: dict[str, Any],
        corrected_label: str | None = None,
        confidence: float = 1.0,
    ) -> None:
        marker = (label.line_id, label.word_id, label.label)
        if marker in handled:
            return
        corrections.append(
            _record(
                key=(label.line_id, label.word_id),
                original=label,
                corrected_label=corrected_label,
                action="CONFLICT",
                provenance=provenance,
                rule_id=rule_id,
                confidence=confidence,
                reason=reason,
                evidence=evidence,
                conflict=True,
                original_new_status=ValidationStatus.NEEDS_REVIEW.value,
            )
        )
        handled.add(marker)

    merchant = _merchant_key(merchant_name)
    excluded_financial_keys: set[tuple[int, int]] = set()
    for context in contexts:
        text = context.text.casefold()
        numeric = [
            word for word in context.words if _cents(word.text) is not None
        ]
        if "costco" in merchant and re.search(r"\bmember\s*#?", text):
            target = next(
                (
                    word
                    for word in reversed(numeric)
                    if re.fullmatch(r"\d{6,16}", re.sub(r"\D", "", word.text))
                ),
                None,
            )
            if target:
                propose(
                    (target.line_id, target.word_id),
                    "LOYALTY_ID",
                    provenance="template",
                    rule_id="costco-member-number",
                    reason=(
                        "Costco MEMBER# template identifies the membership "
                        "number."
                    ),
                    evidence={
                        "merchant": merchant_name,
                        "row_text": context.text,
                    },
                )
        if "vons" in merchant and re.search(r"\bmember\s+savings?\b", text):
            target = next(
                (
                    word
                    for word in reversed(context.words)
                    if looks_like_receipt_amount(word.text)
                ),
                None,
            )
            if target:
                propose(
                    (target.line_id, target.word_id),
                    "DISCOUNT",
                    provenance="template",
                    rule_id="vons-member-savings",
                    reason=(
                        "Vons MEMBER SAVINGS template identifies a discount."
                    ),
                    evidence={
                        "merchant": merchant_name,
                        "row_text": context.text,
                    },
                )
        if "smith" in merchant and re.search(r"\bfuel\s+points?\b", text):
            for word in numeric:
                key = (word.line_id, word.word_id)
                excluded_financial_keys.add(key)
                active_financial = [
                    label
                    for label in labels_by_key.get(key, [])
                    if _active(label) and label.label in _FINANCIAL
                ]
                for label in active_financial:
                    flag(
                        label,
                        provenance="template",
                        rule_id="smiths-fuel-points-exclusion",
                        reason=(
                            "Smith's FUEL POINTS count is loyalty metadata, "
                            "not money."
                        ),
                        evidence={
                            "merchant": merchant_name,
                            "row_text": context.text,
                        },
                    )
            checks.append(
                {
                    "check_id": "smiths-fuel-points-exclusion",
                    "status": "EXCLUDED_FROM_ARITHMETIC",
                    "row_text": context.text,
                    "provenance": "template",
                }
            )

    # Safe #1066 correction: payment keywords can disambiguate LINE_TOTAL.
    for label in labels:
        if not _active(label) or label.label != "LINE_TOTAL":
            continue
        context = row_by_line.get(label.line_id)
        if not context or context.section != "PAYMENT":
            continue
        text = context.text.casefold()
        desired = None
        if re.search(r"\bcash\s*back\b", text):
            desired = "CASH_BACK"
        elif re.search(r"\bchange\b", text):
            desired = "CHANGE"
        if desired:
            propose(
                (label.line_id, label.word_id),
                desired,
                provenance="section-rule",
                rule_id="payment-line-total-correction",
                reason=(
                    f"PAYMENT row keyword identifies {desired}, not "
                    "LINE_TOTAL."
                ),
                evidence={
                    "row_text": context.text,
                    **context.section_evidence,
                },
                confidence=float(
                    context.section_evidence.get("section_confidence") or 1.0
                ),
            )

    def effective_labels() -> dict[tuple[int, int], set[str]]:
        result: dict[tuple[int, int], set[str]] = {}
        for label in labels:
            if _active(label):
                result.setdefault((label.line_id, label.word_id), set()).add(
                    label.label
                )
        for event in corrections:
            key = (int(event["line_id"]), int(event["word_id"]))
            if event.get("conflict"):
                continue
            original = event.get("original_label")
            if original:
                result.setdefault(key, set()).discard(str(original))
            corrected = event.get("corrected_label")
            if corrected:
                result.setdefault(key, set()).add(str(corrected))
        return result

    def role_values() -> dict[str, list[tuple[tuple[int, int], int]]]:
        result: dict[str, list[tuple[tuple[int, int], int]]] = {}
        for key, effective in effective_labels().items():
            word = words_by_key.get(key)
            value = _cents(word.text) if word else None
            if value is None or key in excluded_financial_keys:
                continue
            for label in effective & _FINANCIAL:
                context = row_by_line.get(key[0])
                if label in {"LINE_TOTAL", "UNIT_PRICE"} and (
                    context and context.section != "ITEMS"
                ):
                    continue
                result.setdefault(label, []).append((key, value))
        return result

    def candidates(
        value: int, sections_allowed: set[str]
    ) -> list[tuple[int, int]]:
        effective = effective_labels()
        found = []
        for key, word in words_by_key.items():
            context = row_by_line.get(key[0])
            if (
                key in excluded_financial_keys
                or not looks_like_receipt_amount(word.text)
                or _cents(word.text) != value
                or (context and context.section not in sections_allowed)
                or bool(effective.get(key, set()) & _FINANCIAL)
            ):
                continue
            found.append(key)
        return sorted(found)

    def propose_exact(
        keys: list[tuple[int, int]], desired: str, rule_id: str, reason: str
    ) -> None:
        if len(keys) == 1:
            propose(
                keys[0],
                desired,
                provenance="arithmetic",
                rule_id=rule_id,
                reason=reason,
                evidence={"candidate_count": 1},
            )
        elif len(keys) > 1:
            confidence = 1.0 / len(keys)
            for key in keys:
                corrections.append(
                    _record(
                        key=key,
                        original=None,
                        corrected_label=desired,
                        action="CONFLICT",
                        provenance="arithmetic",
                        rule_id=rule_id,
                        confidence=confidence,
                        reason=f"{reason} Multiple exact candidates remain.",
                        evidence={"candidate_count": len(keys)},
                        conflict=True,
                        corrected_status=ValidationStatus.NEEDS_REVIEW.value,
                    )
                )

    roles = role_values()
    for role in ("SUBTOTAL", "TAX", "GRAND_TOTAL"):
        if len(roles.get(role, [])) <= 1:
            continue
        checks.append(
            {
                "check_id": "arithmetic-role-cardinality",
                "status": "CONFLICT",
                "label": role,
                "candidate_count": len(roles[role]),
                "provenance": "arithmetic",
            }
        )
        for key, _ in roles[role]:
            for label in labels_by_key.get(key, []):
                if _active(label) and label.label == role:
                    flag(
                        label,
                        provenance="arithmetic",
                        rule_id="arithmetic-role-cardinality",
                        reason=f"Multiple active {role} candidates remain.",
                        evidence={"candidate_count": len(roles[role])},
                    )

    line_totals = roles.get("LINE_TOTAL", [])
    discounts = roles.get("DISCOUNT", [])
    subtotals = roles.get("SUBTOTAL", [])
    if line_totals:
        expected_subtotal = sum(value for _, value in line_totals) - sum(
            abs(value) for _, value in discounts
        )
        if not subtotals:
            propose_exact(
                candidates(expected_subtotal, {"SUMMARY"}),
                "SUBTOTAL",
                "line-totals-to-subtotal",
                "Line totals minus discounts identify the subtotal exactly.",
            )
        elif len(subtotals) == 1:
            balanced = subtotals[0][1] == expected_subtotal
            checks.append(
                {
                    "check_id": "line-totals-to-subtotal",
                    "status": "BALANCED" if balanced else "CONFLICT",
                    "actual_cents": subtotals[0][1],
                    "expected_cents": expected_subtotal,
                    "provenance": "arithmetic",
                }
            )
            if not balanced:
                for key, _ in line_totals + subtotals:
                    for label in labels_by_key.get(key, []):
                        if _active(label) and label.label in {
                            "LINE_TOTAL",
                            "SUBTOTAL",
                        }:
                            flag(
                                label,
                                provenance="arithmetic",
                                rule_id="line-totals-to-subtotal",
                                reason=(
                                    "Line totals do not reconcile to "
                                    "subtotal."
                                ),
                                evidence={
                                    "actual_cents": subtotals[0][1],
                                    "expected_cents": expected_subtotal,
                                },
                            )

    roles = role_values()
    subtotals = roles.get("SUBTOTAL", [])
    taxes = roles.get("TAX", [])
    totals = roles.get("GRAND_TOTAL", [])
    if len(subtotals) == 1 and len(totals) == 1 and not taxes:
        tax_gap = totals[0][1] - subtotals[0][1]
        if tax_gap == 0:
            checks.append(
                {
                    "check_id": "subtotal-tax-total",
                    "status": "TAX_EXEMPT",
                    "subtotal_cents": subtotals[0][1],
                    "total_cents": totals[0][1],
                    "provenance": "arithmetic",
                }
            )
        elif tax_gap > 0:
            propose_exact(
                candidates(tax_gap, {"SUMMARY"}),
                "TAX",
                "subtotal-tax-total",
                "Grand total minus subtotal identifies tax exactly.",
            )

    roles = role_values()
    subtotals = roles.get("SUBTOTAL", [])
    taxes = roles.get("TAX", [])
    totals = roles.get("GRAND_TOTAL", [])
    if len(subtotals) == 1:
        expected_total = subtotals[0][1] + sum(value for _, value in taxes)
        if not totals:
            propose_exact(
                candidates(expected_total, {"SUMMARY", "TOTAL_LINE"}),
                "GRAND_TOTAL",
                "subtotal-tax-total",
                "Subtotal plus tax identifies the grand total exactly.",
            )
        elif len(totals) == 1:
            balanced = totals[0][1] == expected_total
            checks.append(
                {
                    "check_id": "subtotal-tax-total",
                    "status": "BALANCED" if balanced else "CONFLICT",
                    "actual_cents": totals[0][1],
                    "expected_cents": expected_total,
                    "provenance": "arithmetic",
                }
            )
            if not balanced:
                for key, _ in subtotals + taxes + totals:
                    for label in labels_by_key.get(key, []):
                        if _active(label) and label.label in {
                            "SUBTOTAL",
                            "TAX",
                            "GRAND_TOTAL",
                        }:
                            flag(
                                label,
                                provenance="arithmetic",
                                rule_id="subtotal-tax-total",
                                reason=(
                                    "Subtotal, tax, and grand total "
                                    "conflict."
                                ),
                                evidence={
                                    "actual_cents": totals[0][1],
                                    "expected_cents": expected_total,
                                },
                            )

    roles = role_values()
    if (
        not roles.get("SUBTOTAL")
        and len(roles.get("GRAND_TOTAL", [])) == 1
        and roles.get("LINE_TOTAL")
    ):
        expected = (
            sum(value for _, value in roles["LINE_TOTAL"])
            - sum(abs(value) for _, value in roles.get("DISCOUNT", []))
            + sum(value for _, value in roles.get("TAX", []))
        )
        actual = roles["GRAND_TOTAL"][0][1]
        checks.append(
            {
                "check_id": "line-totals-direct-total",
                "status": "BALANCED" if actual == expected else "CONFLICT",
                "actual_cents": actual,
                "expected_cents": expected,
                "provenance": "arithmetic",
            }
        )
        if actual != expected:
            for key, _ in roles["LINE_TOTAL"] + roles["GRAND_TOTAL"]:
                for label in labels_by_key.get(key, []):
                    if _active(label) and label.label in {
                        "LINE_TOTAL",
                        "GRAND_TOTAL",
                    }:
                        flag(
                            label,
                            provenance="arithmetic",
                            rule_id="line-totals-direct-total",
                            reason=(
                                "Line totals do not reconcile to grand "
                                "total."
                            ),
                            evidence={
                                "actual_cents": actual,
                                "expected_cents": expected,
                            },
                        )

    # Section consistency runs after exact corrections have projected an
    # effective label set. Unresolved violations are never guessed.
    effective = effective_labels()
    for label in labels:
        marker = (label.line_id, label.word_id, label.label)
        if not _active(label) or marker in handled:
            continue
        if label.label not in effective.get(
            (label.line_id, label.word_id), set()
        ):
            continue
        allowed = _SECTION_FOOTPRINT.get(label.label)
        context = row_by_line.get(label.line_id)
        if not allowed or not context or context.section in allowed:
            continue
        confidence = float(
            context.section_evidence.get("section_confidence") or 1.0
        )
        flag(
            label,
            provenance="section-rule",
            rule_id="label-section-footprint",
            reason=(
                f"{label.label} is inconsistent with {context.section}; "
                f"expected one of {sorted(allowed)}."
            ),
            evidence={"row_text": context.text, **context.section_evidence},
            confidence=confidence,
        )

    unique = {event["event_id"]: event for event in corrections}
    return ReconciliationPlan(
        corrections=[unique[event_id] for event_id in sorted(unique)],
        checks=checks,
    )


def _append_reason(original: str | None, event: dict[str, Any]) -> str:
    note = f"D3[{event['rule_id']}]: {event['reason']}"
    if original and note in original:
        return original
    return f"{original} | {note}" if original else note


def _merge_history(
    prior: Iterable[dict[str, Any]], current: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged = {str(event["event_id"]): event for event in prior}
    merged.update({str(event["event_id"]): event for event in current})
    return [merged[event_id] for event_id in sorted(merged)]


def apply_reconciliation_plan(
    dynamo: ReconciliationDynamo,
    *,
    image_id: str,
    receipt_id: int,
    labels: Sequence[ReceiptWordLabel],
    plan: ReconciliationPlan,
) -> ReceiptLabelReconciliation:
    """Apply a pure plan additively and persist its provenance artifact."""

    label_index = {
        (label.line_id, label.word_id, label.label): label for label in labels
    }
    for event in plan.corrections:
        key = (int(event["line_id"]), int(event["word_id"]))
        original_label = event.get("original_label")
        original = (
            label_index.get((key[0], key[1], str(original_label)))
            if original_label
            else None
        )
        if original is not None and event.get("original_new_status"):
            original.validation_status = str(event["original_new_status"])
            original.reasoning = _append_reason(original.reasoning, event)
            dynamo.update_receipt_word_label(original)

        corrected = event.get("corrected_label")
        corrected_status = event.get("corrected_status")
        if corrected and corrected_status:
            existing = label_index.get((key[0], key[1], str(corrected)))
            if existing is None:
                existing = ReceiptWordLabel(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    line_id=key[0],
                    word_id=key[1],
                    label=str(corrected),
                    reasoning=str(event["reason"]),
                    timestamp_added=datetime.now(timezone.utc),
                    validation_status=str(corrected_status),
                    label_proposed_by=(
                        f"{MODEL_SOURCE}:{event['provenance']}"
                    ),
                    label_consolidated_from=(
                        str(original_label) if original_label else None
                    ),
                )
                try:
                    dynamo.add_receipt_word_label(existing)
                except EntityAlreadyExistsError:
                    # A concurrent/redelivered pass may have created the same
                    # deterministic correction after this label snapshot.
                    pass
                label_index[(key[0], key[1], str(corrected))] = existing
            elif _status(existing) != str(corrected_status):
                existing.validation_status = str(corrected_status)
                existing.reasoning = _append_reason(existing.reasoning, event)
                dynamo.update_receipt_word_label(existing)

    prior_corrections: list[dict[str, Any]] = []
    try:
        prior = dynamo.get_receipt_label_reconciliation(image_id, receipt_id)
        prior_corrections = prior.corrections
    except EntityNotFoundError:
        pass
    merged_corrections = _merge_history(prior_corrections, plan.corrections)
    artifact = ReceiptLabelReconciliation(
        image_id=image_id,
        receipt_id=receipt_id,
        corrections=merged_corrections,
        checks=plan.checks,
        validation_status=(
            ValidationStatus.NEEDS_REVIEW.value
            if any(event.get("conflict") for event in merged_corrections)
            else ValidationStatus.VALID.value
        ),
        model_source=MODEL_SOURCE,
        created_at=datetime.now(timezone.utc),
    )
    dynamo.put_receipt_label_reconciliation(artifact)
    return artifact


def reconcile_receipt_labels(
    dynamo: ReconciliationDynamo,
    image_id: str,
    receipt_id: int,
    merchant_name: str | None = None,
) -> ReceiptLabelReconciliation:
    """Fetch, plan, apply, and persist D3 reconciliation for one receipt."""

    words = dynamo.list_receipt_words_from_receipt(image_id, receipt_id)
    labels, _ = dynamo.list_receipt_word_labels_for_receipt(
        image_id, receipt_id
    )
    rows = dynamo.get_receipt_rows_from_receipt(image_id, receipt_id)
    sections = dynamo.get_receipt_sections_from_receipt(image_id, receipt_id)
    plan = plan_label_reconciliation(
        words, labels, rows, sections, merchant_name
    )
    return apply_reconciliation_plan(
        dynamo,
        image_id=image_id,
        receipt_id=receipt_id,
        labels=labels,
        plan=plan,
    )


__all__ = [
    "MODEL_SOURCE",
    "ReconciliationPlan",
    "apply_reconciliation_plan",
    "plan_label_reconciliation",
    "reconcile_receipt_labels",
]
