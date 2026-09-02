"""Unit and golden-receipt tests for deterministic section assignment."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from receipt_upload.section_assignment import (
    MODEL_SOURCE,
    RowFeatures,
    assign_and_persist_sections,
    assign_feature_sections,
    assign_row_sections,
    learn_prior,
    load_prior_model,
)

from receipt_dynamo.constants import SectionType, ValidationStatus
from receipt_dynamo.entities import ReceiptRow, ReceiptSection

_IMAGE_ID = "00000000-0000-4000-8000-000000000001"
_FIXTURE = Path(__file__).parent / "fixtures/upload_determinism_golden.json"
_REAL_FIXTURE = (
    Path(__file__).parent / "fixtures/section_assignment_real_golden.json"
)


@dataclass
class FakeLine:
    line_id: int
    text: str


def _rows(texts: list[str]) -> tuple[list[ReceiptRow], list[FakeLine]]:
    rows = []
    lines = []
    for index, text in enumerate(texts):
        line_id = index + 1
        y = 1.0 - index / max(len(texts), 1)
        lines.append(FakeLine(line_id, text))
        rows.append(
            ReceiptRow(
                image_id=_IMAGE_ID,
                receipt_id=1,
                row_id=line_id,
                line_ids=[line_id],
                grouping_version="visual-rows-v1",
                y_min=y - 0.02,
                y_max=y,
                x_min=0.05,
                x_max=0.95,
                created_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
                amount_text=(text.rsplit(" ", 1)[-1] if "." in text else None),
                amount_line_id=(line_id if "." in text else None),
                amount_word_id=(1 if "." in text else None),
                price_column_x=(0.95 if "." in text else None),
            )
        )
    return rows, lines


class MemorySections:
    def __init__(self) -> None:
        self.sections = []

    def add_receipt_section(self, section):
        self.sections.append(section)

    def get_receipt_sections_from_receipt(self, _image_id, _receipt_id):
        return list(self.sections)

    def update_receipt_section(self, section):
        self.sections = [
            section if item.section_type == section.section_type else item
            for item in self.sections
        ]


def test_learned_prior_assigns_expected_segments() -> None:
    rows, lines = _rows(["STORE", "APPLE 1.00", "TOTAL 1.00"])
    from receipt_upload.section_assignment import extract_row_features

    features = extract_row_features(rows, lines)
    model = {
        "global": learn_prior(
            [
                list(
                    zip(
                        features,
                        [
                            SectionType.STOREFRONT.value,
                            SectionType.ITEMS.value,
                            SectionType.TOTAL_LINE.value,
                        ],
                        strict=True,
                    )
                )
            ]
        ),
        "merchants": {},
    }

    result = assign_row_sections(rows, lines, model)

    assert [item.section_type for item in result] == [
        "STOREFRONT",
        "ITEMS",
        "TOTAL_LINE",
    ]


def test_segment_decoder_allows_measured_repeated_sections() -> None:
    rows, lines = _rows(["SURVEY", "VISA 1.00", "FEEDBACK"])
    from receipt_upload.section_assignment import extract_row_features

    features = extract_row_features(rows, lines)
    expected = [
        SectionType.SURVEY.value,
        SectionType.PAYMENT.value,
        SectionType.SURVEY.value,
    ]
    model = {
        "schema_version": 2,
        "global": learn_prior([list(zip(features, expected, strict=True))]),
        "merchants": {},
    }

    result = assign_row_sections(rows, lines, model)

    assert [item.section_type for item in result] == expected


def test_amount_evidence_falls_back_to_legacy_row_text() -> None:
    rows, lines = _rows(["APPLE 4.99"])
    rows[0].amount_text = None

    from receipt_upload.section_assignment import extract_row_features

    [features] = extract_row_features(rows, lines)

    assert features.has_amount == 1.0
    assert features.amount_density == 1.0
    assert "__amount__" in features.tokens


def test_persistence_is_additive_and_pending() -> None:
    rows, lines = _rows(["STORE", "APPLE 1.00", "TOTAL 1.00"])
    store = MemorySections()

    created, section_by_line = assign_and_persist_sections(
        store, rows, lines, "Sprouts Farmers Market", load_prior_model()
    )

    assert created
    assert all(item.model_source == MODEL_SOURCE for item in created)
    assert all(
        item.validation_status == ValidationStatus.PENDING.value
        for item in created
    )
    assert set(section_by_line) == {1, 2, 3}


def test_persistence_line_map_prefers_human_valid_section() -> None:
    rows, lines = _rows(["APPLE 1.00"])
    now = datetime(2026, 7, 14, tzinfo=timezone.utc)
    store = MemorySections()
    store.sections = [
        ReceiptSection(
            image_id=_IMAGE_ID,
            receipt_id=1,
            section_type=SectionType.ITEMS.value,
            line_ids=[1],
            row_ids=[1],
            confidence=0.2,
            validation_status=ValidationStatus.VALID.value,
            model_source="human-qa",
            created_at=now,
        ),
        ReceiptSection(
            image_id=_IMAGE_ID,
            receipt_id=1,
            section_type=SectionType.PAYMENT.value,
            line_ids=[1],
            row_ids=[1],
            confidence=0.99,
            validation_status=ValidationStatus.PENDING.value,
            model_source=MODEL_SOURCE,
            created_at=now,
        ),
    ]

    with patch(
        "receipt_upload.section_assignment.assign_row_sections",
        return_value=[],
    ):
        created, section_by_line = assign_and_persist_sections(
            store, rows, lines, None, load_prior_model()
        )

    assert created == []
    assert section_by_line == {1: SectionType.ITEMS.value}


def test_golden_merchants_have_deterministic_learned_priors() -> None:
    fixtures = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    model = load_prior_model()

    for fixture in fixtures:
        rows, lines = _rows(fixture["rows"])
        first = assign_row_sections(rows, lines, model, fixture["merchant"])
        second = assign_row_sections(rows, lines, model, fixture["merchant"])
        assert first == second
        assert len(first) == len(rows)
        mismatches = {
            index: (expected, first[index].section_type)
            for index, expected in enumerate(fixture["expected"])
            if expected is not None and first[index].section_type != expected
        }
        assert not mismatches, f"{fixture['merchant']}: {mismatches}"


def test_real_collapse_cases_pin_qa_expected_rows() -> None:
    fixtures = json.loads(_REAL_FIXTURE.read_text(encoding="utf-8"))
    model = load_prior_model()

    for case_index, fixture in enumerate(fixtures, start=1):
        features = []
        expected = {}
        pinned = {}
        for item in fixture["rows"]:
            row_id = int(item["row_id"])
            row = ReceiptRow(
                image_id=_IMAGE_ID,
                receipt_id=case_index,
                row_id=row_id,
                line_ids=[row_id],
                grouping_version="visual-rows-v1",
                y_min=0.0,
                y_max=0.01,
                x_min=0.0,
                x_max=float(item["x_span"]),
                created_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
            )
            features.append(
                RowFeatures(
                    row=row,
                    position=float(item["position"]),
                    x_span=float(item["x_span"]),
                    alpha_ratio=float(item["alpha_ratio"]),
                    has_amount=float(item["has_amount"]),
                    amount_density=float(item["amount_density"]),
                    has_quantity=float(item["has_quantity"]),
                    tokens=tuple(item["tokens"]),
                    token_evidence=tuple(
                        (str(section), float(score))
                        for section, score in sorted(
                            item["token_evidence"].items()
                        )
                    ),
                )
            )
            if item["expected"] is not None:
                expected[row_id] = str(item["expected"])
            pinned[row_id] = str(item["predicted"])

        first = assign_feature_sections(features, model, fixture["merchant"])
        second = assign_feature_sections(features, model, fixture["merchant"])
        assert first == second
        predicted = {
            assignment.row.row_id: assignment.section_type
            for assignment in first
        }
        assert predicted == pinned, fixture["case"]
        for row_id, required_label in fixture["required_item_matches"].items():
            assert expected[int(row_id)] == required_label, fixture["case"]
            assert predicted[int(row_id)] == required_label, fixture["case"]

        matched = sum(
            predicted[row_id] == section_type
            for row_id, section_type in expected.items()
        )
        item_rows = {
            row_id
            for row_id, section_type in expected.items()
            if section_type == SectionType.ITEMS.value
        }
        matched_items = sum(
            predicted[row_id] == SectionType.ITEMS.value
            for row_id in item_rows
        )
        assert fixture["qa"] == {
            "matched": matched,
            "scored": len(expected),
            "items_matched": matched_items,
            "items_scored": len(item_rows),
        }
        assert item_rows, fixture["case"]


def _section_of(rows, lines, texts, wanted_text):
    """Section the SHIPPING prior assigns to the row carrying `wanted_text`."""
    assignments = assign_row_sections(rows, lines, load_prior_model())
    index = texts.index(wanted_text)
    return assignments[index].section_type


def test_two_column_tender_row_never_lands_in_items() -> None:
    """Regression for the Tropical Smoothie IMG_3426 defect.

    The row builder pairs a payment LABEL line with its AMOUNT line, so
    by the time the assigner sees "Cash | $10.00" it is one row carrying
    an amount -- and the learned PAYMENT prior was fit on a corpus whose
    payment rows are text-only (P(has_amount | PAYMENT) = 0.14 against
    0.76 for ITEMS). The semi-Markov decoder therefore re-entered ITEMS
    for that row AFTER the total, and the line-item decoder emitted a
    nameless $10.00 item on the bare-amount line -- which no text-based
    decoder guard can reject, because that line carries no text.
    """
    texts = [
        "TROPICAL SMOOTHIE",
        "PEANUT PARADISE 8.99",
        "PEA PROTEIN 0.00",
        "SUB TOTAL 8.99",
        "SALES TAX 0.75",
        "ORDER TOTAL 9.70",
        "CASH 10.00",
        "CHANGE DUE 0.30",
    ]
    rows, lines = _rows(texts)
    assert _section_of(rows, lines, texts, "CASH 10.00") != "ITEMS"
    assert _section_of(rows, lines, texts, "CHANGE DUE 0.30") != "ITEMS"
    assert _section_of(rows, lines, texts, "PEANUT PARADISE 8.99") == "ITEMS"
    assert _section_of(rows, lines, texts, "PEA PROTEIN 0.00") == "ITEMS"


def test_a_total_printed_inside_the_items_block_is_left_alone() -> None:
    """The guard is tender-only ON PURPOSE.

    In-N-Out and Trader Joe's print a TOTAL inside the items block. A
    blanket "any settlement row terminates ITEMS" rule would fight that
    format, so `is_tender_row` deliberately excludes SUB TOTAL / TOTAL /
    TAX -- those rows keep whatever the learned model gives them.
    """
    from receipt_upload.section_assignment import _forbidden_sections

    for text in ("SUB TOTAL 8.99", "TOTAL 9.70", "SALES TAX 0.75"):
        assert _forbidden_sections(text) == (), text
    for text in ("CASH 10.00", "CHANGE DUE 0.30", "Visa Debit $37.51"):
        assert _forbidden_sections(text) == ("ITEMS",), text


def test_forbidden_sections_defaults_to_empty_for_reconstructed_features() -> (
    None
):
    """RowFeatures rebuilt from stored features keep the old behavior."""
    feature = RowFeatures(
        row=_rows(["APPLE 1.00"])[0][0],
        position=0.5,
        x_span=0.9,
        alpha_ratio=1.0,
        has_amount=1.0,
        amount_density=1.0,
        has_quantity=0.0,
        tokens=("apple", "__amount__"),
    )
    assert feature.forbidden_sections == ()
    assert assign_feature_sections([feature], load_prior_model())
