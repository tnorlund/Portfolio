"""Unit and golden-receipt tests for deterministic section assignment."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from receipt_dynamo.constants import SectionType, ValidationStatus
from receipt_dynamo.entities import ReceiptRow

from receipt_upload.section_assignment import (
    MODEL_SOURCE,
    assign_and_persist_sections,
    assign_row_sections,
    learn_prior,
    load_prior_model,
)

_IMAGE_ID = "00000000-0000-4000-8000-000000000001"
_FIXTURE = Path(__file__).parent / "fixtures/upload_determinism_golden.json"


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


def test_learned_prior_assigns_monotone_sections() -> None:
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


def test_golden_merchants_have_deterministic_learned_priors() -> None:
    fixtures = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    model = load_prior_model()

    for fixture in fixtures:
        rows, lines = _rows(fixture["rows"])
        first = assign_row_sections(rows, lines, model, fixture["merchant"])
        second = assign_row_sections(rows, lines, model, fixture["merchant"])
        assert first == second
        assert len(first) == len(rows)
        assert all(
            item.section_type in SectionType._value2member_map_
            for item in first
        )
