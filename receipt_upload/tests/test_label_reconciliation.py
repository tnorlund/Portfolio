"""D3 deterministic post-LayoutLM reconciliation tests."""

import json
from pathlib import Path
from types import SimpleNamespace

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities import ReceiptWordLabel

from receipt_upload.label_reconciliation import (
    apply_reconciliation_plan,
    plan_label_reconciliation,
)

IMAGE_ID = "00000000-0000-4000-8000-0000000000d3"


def _word(line_id: int, word_id: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(line_id=line_id, word_id=word_id, text=text)


def _label(
    line_id: int,
    word_id: int,
    label: str,
    status: str = ValidationStatus.PENDING.value,
) -> ReceiptWordLabel:
    return ReceiptWordLabel(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        label=label,
        reasoning="LayoutLM proposal",
        timestamp_added="2026-07-14T00:00:00+00:00",
        validation_status=status,
        label_proposed_by="layoutlm",
    )


def _row(line_id: int) -> SimpleNamespace:
    return SimpleNamespace(row_id=line_id, line_ids=[line_id])


def _section(section_type: str, *line_ids: int) -> SimpleNamespace:
    return SimpleNamespace(
        section_type=section_type,
        line_ids=list(line_ids),
        confidence=0.95,
        model_source="upload-determinism-v1",
        validation_status=ValidationStatus.PENDING.value,
    )


def _events(plan, rule_id: str):
    return [event for event in plan.corrections if event["rule_id"] == rule_id]


def test_section_rules_route_unresolved_layoutlm_conflicts_to_review():
    words = [_word(1, 1, "TOTAL"), _word(1, 2, "9.99"), _word(2, 1, "MILK")]
    labels = [_label(1, 2, "GRAND_TOTAL"), _label(2, 1, "PRODUCT_NAME")]
    rows = [_row(1), _row(2)]
    sections = [_section("HEADER", 1), _section("SUMMARY", 2)]

    plan = plan_label_reconciliation(
        words, labels, rows, sections, "Long Tail"
    )

    events = _events(plan, "label-section-footprint")
    assert len(events) == 2
    assert all(event["conflict"] for event in events)
    assert all(
        event["original_new_status"] == "NEEDS_REVIEW" for event in events
    )


def test_payment_keyword_safely_corrects_line_total_without_deleting_original():
    words = [_word(1, 1, "CHANGE"), _word(1, 2, "2.00")]
    original = _label(1, 2, "LINE_TOTAL")
    plan = plan_label_reconciliation(
        words,
        [original],
        [_row(1)],
        [_section("PAYMENT", 1)],
        "Store",
    )

    event = _events(plan, "payment-line-total-correction")[0]
    assert event["corrected_label"] == "CHANGE"
    assert event["original_new_status"] == "INVALID"
    assert event["original_proposer"] == "layoutlm"


def test_arithmetic_adds_only_exact_unique_subtotal_and_total_candidates():
    words = [
        _word(1, 1, "5.00"),
        _word(2, 1, "3.00"),
        _word(3, 1, "8.00"),
        _word(4, 1, "1.00"),
        _word(5, 1, "9.00"),
    ]
    labels = [
        _label(1, 1, "LINE_TOTAL"),
        _label(2, 1, "LINE_TOTAL"),
        _label(4, 1, "TAX"),
    ]
    plan = plan_label_reconciliation(
        words,
        labels,
        [_row(i) for i in range(1, 6)],
        [_section("ITEMS", 1, 2), _section("SUMMARY", 3, 4, 5)],
        "Sprouts",
    )

    additions = {
        (event["line_id"], event["corrected_label"])
        for event in plan.corrections
        if event["action"] == "ADD"
    }
    assert (3, "SUBTOTAL") in additions
    assert (5, "GRAND_TOTAL") in additions


def test_tax_exempt_basket_is_confirmed_without_inventing_tax():
    words = [_word(1, 1, "8.00"), _word(2, 1, "8.00")]
    labels = [_label(1, 1, "SUBTOTAL"), _label(2, 1, "GRAND_TOTAL")]
    plan = plan_label_reconciliation(
        words,
        labels,
        [_row(1), _row(2)],
        [_section("SUMMARY", 1), _section("TOTAL_LINE", 2)],
        "Market",
    )

    assert any(check.get("status") == "TAX_EXEMPT" for check in plan.checks)
    assert not any(
        event.get("corrected_label") == "TAX" for event in plan.corrections
    )


def test_golden_merchants_exercise_templates_and_long_tail_fixture():
    fixture = (
        Path(__file__).parent / "fixtures" / "upload_determinism_golden.json"
    )
    receipts = json.loads(fixture.read_text(encoding="utf-8"))
    plans = {}
    for receipt in receipts:
        words = []
        rows = []
        sections = []
        labels = []
        for line_id, text in enumerate(receipt["rows"], start=1):
            parts = text.split()
            words.extend(
                _word(line_id, i, part)
                for i, part in enumerate(parts, start=1)
            )
            rows.append(_row(line_id))
            section = "ITEMS" if line_id == 3 else "SUMMARY"
            sections.append(_section(section, line_id))
            if receipt["merchant"] == "Smith's" and "FUEL POINTS" in text:
                labels.append(_label(line_id, len(parts), "LINE_TOTAL"))
        plans[receipt["merchant"]] = plan_label_reconciliation(
            words, labels, rows, sections, receipt["merchant"]
        )

    assert set(plans) == {
        "Sprouts Farmers Market",
        "Costco Wholesale",
        "Vons",
        "Smith's",
        "Italia Deli Bakery",
    }
    assert _events(plans["Costco Wholesale"], "costco-member-number")
    assert _events(plans["Vons"], "vons-member-savings")
    assert _events(plans["Smith's"], "smiths-fuel-points-exclusion")


class _Dynamo:
    def __init__(self):
        self.updated = []
        self.added = []
        self.artifact = None

    def update_receipt_word_label(self, label):
        self.updated.append(label)

    def add_receipt_word_label(self, label):
        self.added.append(label)

    def get_receipt_label_reconciliation(self, image_id, receipt_id):
        if self.artifact is None:
            raise EntityNotFoundError("missing")
        return self.artifact

    def put_receipt_label_reconciliation(self, artifact):
        self.artifact = artifact


def test_apply_is_additive_preserves_proposer_and_is_idempotent():
    original = _label(1, 2, "LINE_TOTAL")
    plan = plan_label_reconciliation(
        [_word(1, 1, "CHANGE"), _word(1, 2, "2.00")],
        [original],
        [_row(1)],
        [_section("PAYMENT", 1)],
        "Store",
    )
    dynamo = _Dynamo()

    first = apply_reconciliation_plan(
        dynamo,
        image_id=IMAGE_ID,
        receipt_id=1,
        labels=[original],
        plan=plan,
    )
    second = apply_reconciliation_plan(
        dynamo,
        image_id=IMAGE_ID,
        receipt_id=1,
        labels=[original, *dynamo.added],
        plan=plan,
    )

    assert original.label == "LINE_TOTAL"
    assert original.label_proposed_by == "layoutlm"
    assert original.validation_status == ValidationStatus.INVALID.value
    assert [label.label for label in dynamo.added] == ["CHANGE"]
    assert first.corrections == second.corrections
    assert second.model_source == "upload-determinism-v1"
