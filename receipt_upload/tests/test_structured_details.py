"""D4 structured receipt-details tests."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities import ReceiptWordLabel

from receipt_upload.structured_details import (
    build_receipt_resolved_details,
    resolve_receipt_details,
)

IMAGE_ID = "00000000-0000-4000-8000-0000000000d4"


def _word(line_id: int, word_id: int, text: str):
    return SimpleNamespace(
        line_id=line_id,
        word_id=word_id,
        text=text,
        confidence=0.91,
    )


def _label(line_id: int, word_id: int, label: str):
    return ReceiptWordLabel(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        label=label,
        reasoning="LayoutLM proposal",
        timestamp_added="2026-07-14T00:00:00+00:00",
        validation_status=ValidationStatus.VALID.value,
        label_proposed_by="layoutlm",
    )


def _row(line_id: int):
    return SimpleNamespace(row_id=line_id, line_ids=[line_id])


def _section(section_type: str, *line_ids: int):
    return SimpleNamespace(section_type=section_type, line_ids=list(line_ids))


def _reconciliation(corrections=None):
    return SimpleNamespace(corrections=corrections or [])


def _receipt_from_rows(merchant: str, row_texts: list[str]):
    words = []
    labels = []
    rows = []
    sections = []
    for line_id, text in enumerate(row_texts, start=1):
        parts = text.split()
        line_words = [
            _word(line_id, word_id, part)
            for word_id, part in enumerate(parts, start=1)
        ]
        words.extend(line_words)
        rows.append(_row(line_id))
        section = "ITEMS" if line_id == 3 else "SUMMARY"
        sections.append(_section(section, line_id))
        if line_id == 1:
            labels.extend(
                _label(line_id, word.word_id, "MERCHANT_NAME")
                for word in line_words
            )
        if line_id == 3 and len(line_words) > 1:
            labels.extend(
                _label(line_id, word.word_id, "PRODUCT_NAME")
                for word in line_words[:-1]
            )
            labels.append(
                _label(line_id, line_words[-1].word_id, "LINE_TOTAL")
            )
        keyword = parts[0].casefold()
        role = {
            "subtotal": "SUBTOTAL",
            "tax": "TAX",
            "total": "GRAND_TOTAL",
        }.get(keyword)
        if role and len(parts) > 1:
            labels.append(_label(line_id, len(parts), role))
    return words, labels, rows, sections


@pytest.mark.parametrize(
    "merchant",
    [
        "Sprouts Farmers Market",
        "Costco Wholesale",
        "Vons",
        "Smith's",
        "Italia Deli Bakery",
    ],
)
def test_golden_merchants_produce_api_ready_fields(merchant):
    fixture = (
        Path(__file__).parent / "fixtures" / "upload_determinism_golden.json"
    )
    receipt = next(
        item
        for item in json.loads(fixture.read_text(encoding="utf-8"))
        if item["merchant"] == merchant
    )
    words, labels, rows, sections = _receipt_from_rows(
        merchant, receipt["rows"]
    )

    details = build_receipt_resolved_details(
        image_id=IMAGE_ID,
        receipt_id=1,
        words=words,
        labels=labels,
        rows=rows,
        sections=sections,
        reconciliation=_reconciliation(),
        merchant_name=merchant,
    )

    assert details.merchant["value"] == merchant
    assert details.merchant["provenance"] == "layoutlm"
    assert details.merchant["confidence_basis"] == "ocr-word"
    assert details.items[0]["name"]["value"]
    assert details.items[0]["line_total"]["amount_cents"] is not None
    assert details.totals["total"]["amount_cents"] is not None
    assert details.to_document()["schema_version"] == 1


def test_arithmetic_provenance_and_exact_cents_are_preserved():
    words, labels, rows, sections = _receipt_from_rows(
        "Store", ["STORE", "ADDRESS", "MILK 8.00", "TOTAL 8.00"]
    )
    total = next(label for label in labels if label.label == "GRAND_TOTAL")
    correction = {
        "line_id": total.line_id,
        "word_id": total.word_id,
        "corrected_label": "GRAND_TOTAL",
        "provenance": "arithmetic",
        "confidence": 1.0,
        "conflict": False,
    }

    details = build_receipt_resolved_details(
        image_id=IMAGE_ID,
        receipt_id=1,
        words=words,
        labels=labels,
        rows=rows,
        sections=sections,
        reconciliation=_reconciliation([correction]),
        merchant_name="Store",
    )

    assert details.totals["total"]["provenance"] == "arithmetic"
    assert details.totals["total"]["confidence_basis"] == "d3-rule"
    assert details.totals["total"]["amount_cents"] == 800


def test_ambiguous_total_keeps_candidates_and_routes_to_review():
    words, labels, rows, sections = _receipt_from_rows(
        "Store", ["STORE", "ADDRESS", "MILK 8.00", "TOTAL 8.00"]
    )
    words.append(_word(5, 1, "8.00"))
    labels.append(_label(5, 1, "GRAND_TOTAL"))
    rows.append(_row(5))
    sections.append(_section("TOTAL_LINE", 5))

    details = build_receipt_resolved_details(
        image_id=IMAGE_ID,
        receipt_id=1,
        words=words,
        labels=labels,
        rows=rows,
        sections=sections,
        reconciliation=_reconciliation(),
        merchant_name="Store",
    )

    assert details.validation_status == ValidationStatus.NEEDS_REVIEW.value
    assert details.totals["total"]["value"] is None
    assert len(details.totals["total"]["candidates"]) == 2
    assert any(
        conflict["rule_id"] == "d4-role-cardinality"
        for conflict in details.conflicts
    )


def test_fingerprint_disagreement_is_exposed_not_overridden():
    words, labels, rows, sections = _receipt_from_rows(
        "VONS", ["VONS", "ADDRESS", "MILK 8.00", "TOTAL 8.00"]
    )
    fingerprint = SimpleNamespace(
        merchant_candidates=["Costco"],
        typeface="bitMatrix-C1-heavy",
        confidence=0.88,
        places_agreement="DISAGREEMENT",
        places_merchant_name="Vons",
    )
    details = build_receipt_resolved_details(
        image_id=IMAGE_ID,
        receipt_id=1,
        words=words,
        labels=labels,
        rows=rows,
        sections=sections,
        reconciliation=_reconciliation(),
        merchant_name="VONS",
        fingerprint=fingerprint,
    )

    assert details.merchant["value"] == "VONS"
    assert details.merchant["validation_status"] == "NEEDS_REVIEW"
    assert any(
        conflict["rule_id"] == "fingerprint-places-disagreement"
        for conflict in details.conflicts
    )


class _Dynamo:
    def __init__(self, words, labels, rows, sections):
        self.words = words
        self.labels = labels
        self.rows = rows
        self.sections = sections
        self.persisted = None

    def list_receipt_words_from_receipt(self, *_args):
        return self.words

    def list_receipt_word_labels_for_receipt(self, *_args):
        return self.labels, None

    def get_receipt_rows_from_receipt(self, *_args):
        return self.rows

    def get_receipt_sections_from_receipt(self, *_args):
        return self.sections

    def get_receipt_typeface_fingerprint(self, *_args):
        raise EntityNotFoundError("missing")

    def put_receipt_resolved_details(self, details):
        self.persisted = details


def test_resolve_persists_artifact_without_mutating_labels():
    inputs = _receipt_from_rows(
        "Store", ["STORE", "ADDRESS", "MILK 8.00", "TOTAL 8.00"]
    )
    dynamo = _Dynamo(*inputs)
    before = [dict(label) for label in dynamo.labels]

    details = resolve_receipt_details(
        dynamo,
        image_id=IMAGE_ID,
        receipt_id=1,
        reconciliation=_reconciliation(),
        merchant_name="Store",
    )

    assert dynamo.persisted is details
    assert [dict(label) for label in dynamo.labels] == before
