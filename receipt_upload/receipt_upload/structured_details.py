"""Build the additive D4 structured receipt-details artifact."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, Sequence

from receipt_dynamo.amounts import parse_receipt_amount
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities import (
    ReceiptLabelReconciliation,
    ReceiptResolvedDetails,
    ReceiptRow,
    ReceiptSection,
    ReceiptTypefaceFingerprint,
    ReceiptWord,
    ReceiptWordLabel,
)

MODEL_SOURCE = "upload-determinism-v1"
_PROVENANCE = {
    "layoutlm",
    "section-rule",
    "arithmetic",
    "template",
    "fingerprint",
}
_PROVENANCE_PRIORITY = {
    "layoutlm": 0,
    "template": 1,
    "section-rule": 2,
    "arithmetic": 3,
    "fingerprint": 4,
}
_MONEY_LABELS = {
    "UNIT_PRICE",
    "LINE_TOTAL",
    "DISCOUNT",
    "SUBTOTAL",
    "TAX",
    "GRAND_TOTAL",
    "CHANGE",
    "CASH_BACK",
    "REFUND",
}


class StructuredDetailsDynamo(Protocol):
    """Narrow persistence API used by D4."""

    def list_receipt_words_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptWord]: ...

    def list_receipt_word_labels_for_receipt(
        self, image_id: str, receipt_id: int
    ) -> tuple[list[ReceiptWordLabel], dict[str, Any] | None]: ...

    def get_receipt_rows_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptRow]: ...

    def get_receipt_sections_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptSection]: ...

    def get_receipt_typeface_fingerprint(
        self, image_id: str, receipt_id: int
    ) -> ReceiptTypefaceFingerprint: ...

    def put_receipt_resolved_details(
        self, details: ReceiptResolvedDetails
    ) -> None: ...


@dataclass(frozen=True)
class _Group:
    """One label role assembled from words in a materialized row."""

    row_id: int
    label: str
    words: tuple[ReceiptWord, ...]
    labels: tuple[ReceiptWordLabel, ...]


def _status(label: ReceiptWordLabel) -> str:
    return str(label.validation_status or ValidationStatus.NONE.value).upper()


def _merchant_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def _amount_cents(value: str) -> int | None:
    amount = parse_receipt_amount(value)
    return None if amount is None else int(round(amount * 100))


def _correction_index(
    reconciliation: ReceiptLabelReconciliation,
) -> dict[tuple[int, int, str], dict[str, Any]]:
    index = {}
    for correction in reconciliation.corrections:
        corrected = correction.get("corrected_label")
        if not corrected or correction.get("conflict"):
            continue
        index[
            (
                int(correction["line_id"]),
                int(correction["word_id"]),
                str(corrected),
            )
        ] = correction
    return index


def _label_provenance(
    label: ReceiptWordLabel,
    word: ReceiptWord,
    corrections: dict[tuple[int, int, str], dict[str, Any]],
) -> tuple[str, float, str]:
    correction = corrections.get((label.line_id, label.word_id, label.label))
    if correction:
        provenance = str(correction.get("provenance") or "layoutlm")
        confidence = float(correction.get("confidence") or 0.0)
        return provenance, confidence, "d3-rule"
    word_confidence = float(getattr(word, "confidence", 0.0) or 0.0)
    proposed_by = label.label_proposed_by or ""
    for provenance in _PROVENANCE - {"fingerprint"}:
        if provenance in proposed_by:
            return provenance, word_confidence, "ocr-word"
    return "layoutlm", word_confidence, "ocr-word"


def _groups(
    words: Sequence[ReceiptWord],
    labels: Sequence[ReceiptWordLabel],
    rows: Sequence[ReceiptRow],
) -> dict[str, list[_Group]]:
    words_by_key = {(word.line_id, word.word_id): word for word in words}
    row_by_line = {
        line_id: row.row_id for row in rows for line_id in row.line_ids
    }
    bucket: dict[tuple[str, int], list[ReceiptWordLabel]] = {}
    for label in labels:
        key = (label.line_id, label.word_id)
        if (
            _status(label) != ValidationStatus.VALID.value
            or key not in words_by_key
        ):
            continue
        row_id = row_by_line.get(label.line_id, label.line_id)
        bucket.setdefault((label.label, row_id), []).append(label)

    result: dict[str, list[_Group]] = {}
    for (label_name, row_id), group_labels in bucket.items():
        group_labels.sort(key=lambda label: (label.line_id, label.word_id))
        group_words = tuple(
            words_by_key[(label.line_id, label.word_id)]
            for label in group_labels
        )
        result.setdefault(label_name, []).append(
            _Group(
                row_id=row_id,
                label=label_name,
                words=group_words,
                labels=tuple(group_labels),
            )
        )
    for label_groups in result.values():
        label_groups.sort(key=lambda group: group.row_id)
    return result


def _group_field(
    group: _Group,
    corrections: dict[tuple[int, int, str], dict[str, Any]],
) -> dict[str, Any]:
    evidence = [
        _label_provenance(label, word, corrections)
        for label, word in zip(group.labels, group.words, strict=True)
    ]
    provenance = max(evidence, key=lambda item: _PROVENANCE_PRIORITY[item[0]])[
        0
    ]
    confidence = min(value for _, value, _ in evidence)
    confidence_basis = "+".join(sorted({basis for _, _, basis in evidence}))
    statuses = {_status(label) for label in group.labels}
    validation_status = (
        ValidationStatus.VALID.value
        if statuses == {ValidationStatus.VALID.value}
        else ValidationStatus.NEEDS_REVIEW.value
    )
    value = " ".join(word.text for word in group.words).strip()
    field: dict[str, Any] = {
        "value": value,
        "provenance": provenance,
        "confidence": confidence,
        "confidence_basis": confidence_basis,
        "validation_status": validation_status,
        "source_words": [
            {
                "line_id": word.line_id,
                "word_id": word.word_id,
                "label": group.label,
            }
            for word in group.words
        ],
    }
    if group.label in _MONEY_LABELS:
        field["amount_cents"] = _amount_cents(value)
    return field


def _conflicted_field(
    label: str,
    candidates: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    conflicts.append(
        {
            "field": label.lower(),
            "rule_id": "d4-role-cardinality",
            "reason": f"Multiple active {label} candidates remain.",
            "candidate_count": len(candidates),
        }
    )
    return {
        "value": None,
        "provenance": "layoutlm",
        "confidence": 0.0,
        "validation_status": ValidationStatus.NEEDS_REVIEW.value,
        "candidates": candidates,
    }


def _scalar_field(
    label: str,
    grouped: dict[str, list[_Group]],
    corrections: dict[tuple[int, int, str], dict[str, Any]],
    conflicts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    candidates = [
        _group_field(group, corrections) for group in grouped.get(label, [])
    ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    return _conflicted_field(label, candidates, conflicts)


def _address_field(
    grouped: dict[str, list[_Group]],
    corrections: dict[tuple[int, int, str], dict[str, Any]],
) -> dict[str, Any] | None:
    lines = [
        _group_field(group, corrections)
        for group in grouped.get("ADDRESS_LINE", [])
    ]
    if not lines:
        return None
    provenance = max(
        (line["provenance"] for line in lines),
        key=lambda value: _PROVENANCE_PRIORITY[value],
    )
    return {
        "value": ", ".join(str(line["value"]) for line in lines),
        "provenance": provenance,
        "confidence": min(float(line["confidence"]) for line in lines),
        "validation_status": (
            ValidationStatus.VALID.value
            if all(line["validation_status"] == "VALID" for line in lines)
            else ValidationStatus.NEEDS_REVIEW.value
        ),
        "source_words": [
            word for line in lines for word in line["source_words"]
        ],
    }


def _merchant_field(
    merchant_name: str | None,
    grouped: dict[str, list[_Group]],
    corrections: dict[tuple[int, int, str], dict[str, Any]],
    fingerprint: ReceiptTypefaceFingerprint | None,
    conflicts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    labeled = _scalar_field("MERCHANT_NAME", grouped, corrections, conflicts)
    selected = merchant_name or (str(labeled["value"]) if labeled else None)
    if selected is None:
        return None
    if labeled and labeled.get("value") and merchant_name:
        if _merchant_key(str(labeled["value"])) != _merchant_key(
            merchant_name
        ):
            return _conflicted_field(
                "MERCHANT_NAME",
                [
                    labeled,
                    {
                        "value": merchant_name,
                        "provenance": "layoutlm",
                        "confidence": 0.0,
                        "confidence_basis": "unvalidated-merchant-resolution",
                        "validation_status": "NEEDS_REVIEW",
                        "source": "merchant-resolution",
                    },
                ],
                conflicts,
            )
    field = labeled or {
        "value": selected,
        "provenance": "layoutlm",
        "confidence": 0.0,
        "confidence_basis": "unvalidated-merchant-resolution",
        "validation_status": ValidationStatus.NEEDS_REVIEW.value,
        "source": "merchant-resolution",
    }
    field["value"] = selected
    if fingerprint:
        matches = any(
            _merchant_key(candidate) == _merchant_key(selected)
            for candidate in fingerprint.merchant_candidates
        )
        field["fingerprint"] = {
            "typeface": fingerprint.typeface,
            "merchant_candidates": fingerprint.merchant_candidates,
            "places_agreement": fingerprint.places_agreement,
        }
        if matches:
            field["provenance"] = "fingerprint"
            field["confidence"] = fingerprint.confidence
            field["confidence_basis"] = "typeface-fingerprint"
            field["validation_status"] = ValidationStatus.VALID.value
        elif fingerprint.merchant_candidates:
            conflicts.append(
                {
                    "field": "merchant",
                    "rule_id": "fingerprint-merchant-mismatch",
                    "reason": (
                        "Resolved merchant is not a fingerprint candidate."
                    ),
                    "merchant_candidates": fingerprint.merchant_candidates,
                    "resolved_merchant": selected,
                }
            )
            field["validation_status"] = ValidationStatus.NEEDS_REVIEW.value
        if fingerprint.places_agreement == "DISAGREEMENT":
            conflicts.append(
                {
                    "field": "merchant",
                    "rule_id": "fingerprint-places-disagreement",
                    "reason": "Typeface fingerprint disagrees with Places.",
                    "merchant_candidates": fingerprint.merchant_candidates,
                    "places_merchant_name": fingerprint.places_merchant_name,
                }
            )
            field["validation_status"] = ValidationStatus.NEEDS_REVIEW.value
    if labeled is None and (
        fingerprint is None or field["provenance"] != "fingerprint"
    ):
        conflicts.append(
            {
                "field": "merchant",
                "rule_id": "d4-merchant-unverified",
                "reason": (
                    "Merchant resolution lacks label or fingerprint evidence."
                ),
            }
        )
    return field


def _item_rows(
    grouped: dict[str, list[_Group]],
    sections: Sequence[ReceiptSection],
    corrections: dict[tuple[int, int, str], dict[str, Any]],
    conflicts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    section_by_line = {
        line_id: section.section_type
        for section in sections
        for line_id in section.line_ids
    }
    by_row: dict[int, dict[str, _Group]] = {}
    for label in ("PRODUCT_NAME", "QUANTITY", "UNIT_PRICE", "LINE_TOTAL"):
        for group in grouped.get(label, []):
            if not any(
                section_by_line.get(word.line_id) == "ITEMS"
                for word in group.words
            ):
                continue
            existing = by_row.setdefault(group.row_id, {}).get(label)
            if existing is not None:
                conflicts.append(
                    {
                        "field": f"items[{group.row_id}].{label.lower()}",
                        "rule_id": "d4-item-role-cardinality",
                        "reason": f"Multiple {label} groups share one row.",
                    }
                )
                continue
            by_row[group.row_id][label] = group

    items = []
    for row_id in sorted(by_row):
        roles = by_row[row_id]
        item: dict[str, Any] = {"row_id": row_id}
        for label, key in (
            ("PRODUCT_NAME", "name"),
            ("QUANTITY", "quantity"),
            ("UNIT_PRICE", "unit_price"),
            ("LINE_TOTAL", "line_total"),
        ):
            group = roles.get(label)
            item[key] = _group_field(group, corrections) if group else None
        if item["name"] is None or item["line_total"] is None:
            conflicts.append(
                {
                    "field": f"items[{row_id}]",
                    "rule_id": "d4-item-completeness",
                    "reason": "Item row lacks PRODUCT_NAME or LINE_TOTAL.",
                }
            )
        items.append(item)
    return items


def build_receipt_resolved_details(
    *,
    image_id: str,
    receipt_id: int,
    words: Sequence[ReceiptWord],
    labels: Sequence[ReceiptWordLabel],
    rows: Sequence[ReceiptRow],
    sections: Sequence[ReceiptSection],
    reconciliation: ReceiptLabelReconciliation,
    merchant_name: str | None = None,
    fingerprint: ReceiptTypefaceFingerprint | None = None,
) -> ReceiptResolvedDetails:
    """Build one deterministic, API-ready D4 artifact without writing it."""

    corrections = _correction_index(reconciliation)
    grouped = _groups(words, labels, rows)
    conflicts = [
        {
            "field": correction.get("corrected_label")
            or correction.get("original_label"),
            "rule_id": correction.get("rule_id"),
            "reason": correction.get("reason"),
            "event_id": correction.get("event_id"),
        }
        for correction in reconciliation.corrections
        if correction.get("conflict")
    ]

    merchant = _merchant_field(
        merchant_name, grouped, corrections, fingerprint, conflicts
    )
    transaction = {
        "date": _scalar_field("DATE", grouped, corrections, conflicts),
        "time": _scalar_field("TIME", grouped, corrections, conflicts),
        "address": _address_field(grouped, corrections),
        "phone": _scalar_field(
            "PHONE_NUMBER", grouped, corrections, conflicts
        ),
    }
    items = _item_rows(grouped, sections, corrections, conflicts)
    totals = {
        "subtotal": _scalar_field("SUBTOTAL", grouped, corrections, conflicts),
        "tax": _scalar_field("TAX", grouped, corrections, conflicts),
        "total": _scalar_field("GRAND_TOTAL", grouped, corrections, conflicts),
    }
    tender = {
        "method": _scalar_field(
            "PAYMENT_METHOD", grouped, corrections, conflicts
        ),
        "change": _scalar_field("CHANGE", grouped, corrections, conflicts),
        "cash_back": _scalar_field(
            "CASH_BACK", grouped, corrections, conflicts
        ),
        "refund": _scalar_field("REFUND", grouped, corrections, conflicts),
    }

    for field, value in (
        ("merchant", merchant),
        ("items", items),
        ("total", totals["total"]),
    ):
        if value:
            continue
        conflicts.append(
            {
                "field": field,
                "rule_id": "d4-required-field",
                "reason": f"Required structured field {field} is missing.",
            }
        )

    return ReceiptResolvedDetails(
        image_id=image_id,
        receipt_id=receipt_id,
        merchant=merchant,
        transaction=transaction,
        items=items,
        totals=totals,
        tender=tender,
        conflicts=conflicts,
        validation_status=(
            ValidationStatus.NEEDS_REVIEW.value
            if conflicts
            else ValidationStatus.VALID.value
        ),
        model_source=MODEL_SOURCE,
        created_at=datetime.now(timezone.utc),
        schema_version=1,
    )


def resolve_receipt_details(
    dynamo: StructuredDetailsDynamo,
    *,
    image_id: str,
    receipt_id: int,
    reconciliation: ReceiptLabelReconciliation,
    merchant_name: str | None = None,
) -> ReceiptResolvedDetails:
    """Fetch inputs, persist, and return one D4 structured artifact."""

    words = dynamo.list_receipt_words_from_receipt(image_id, receipt_id)
    labels, _ = dynamo.list_receipt_word_labels_for_receipt(
        image_id, receipt_id
    )
    rows = dynamo.get_receipt_rows_from_receipt(image_id, receipt_id)
    sections = dynamo.get_receipt_sections_from_receipt(image_id, receipt_id)
    try:
        fingerprint = dynamo.get_receipt_typeface_fingerprint(
            image_id, receipt_id
        )
    except EntityNotFoundError:
        fingerprint = None
    details = build_receipt_resolved_details(
        image_id=image_id,
        receipt_id=receipt_id,
        words=words,
        labels=labels,
        rows=rows,
        sections=sections,
        reconciliation=reconciliation,
        merchant_name=merchant_name,
        fingerprint=fingerprint,
    )
    dynamo.put_receipt_resolved_details(details)
    return details


__all__ = [
    "MODEL_SOURCE",
    "build_receipt_resolved_details",
    "resolve_receipt_details",
]
