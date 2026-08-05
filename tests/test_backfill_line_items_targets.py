"""The line-item recompute path must target what the WRITER targets.

``scripts/backfill_receipt_line_items.py`` is the only corpus-wide
recompute for RECEIPT_LINE_ITEM rows: the stream stage
(``infra/receipt_line_item_updater``) fires on a summary write, so a
decoder change leaves every untouched receipt carrying the verdict the
old code produced. The script used to select ``validation_status ==
"VALID"`` while the writer accepts any non-INVALID ITEMS section --
measured 2026-08-05 that was 1 reachable prod receipt out of 730 (729
PENDING sections), and 17 unreachable receipts in dev. These tests pin
the two selections to the same rule.
"""

from __future__ import annotations

# isort: off
# receipt_upload lands in a different import group depending on whether
# the venv has it installed, so the grouping here is not reproducible
# across environments. Pin it (same fence as
# tests/test_extend_items_zone_gaps.py).
from scripts.backfill_receipt_line_items import (
    _stored_status,
    select_items_section,
)

# isort: on


def section(
    status,
    model_source="upload-determinism-v1",
    section_type="ITEMS",
):
    return {
        "section_type": section_type,
        "validation_status": status,
        "model_source": model_source,
        "line_ids": [1, 2],
    }


def test_pending_items_section_is_a_target():
    """The worker ships PENDING sections; prod is almost entirely PENDING."""
    assert select_items_section([section("PENDING")]) is not None


def test_valid_items_section_is_a_target():
    assert select_items_section([section("VALID")]) is not None


def test_invalid_items_section_is_refused():
    assert select_items_section([section("INVALID")]) is None


def test_valid_wins_over_pending():
    picked = select_items_section([section("PENDING"), section("VALID")])
    assert picked["validation_status"] == "VALID"
    picked = select_items_section([section("VALID"), section("PENDING")])
    assert picked["validation_status"] == "VALID"


def test_partial_legacy_zones_are_not_items_sections():
    """ITEMS_VALUE / ITEMS_DESCRIPTION are prices-only / names-only."""
    assert (
        select_items_section(
            [
                section("VALID", section_type="ITEMS_VALUE"),
                section("VALID", section_type="ITEMS_DESCRIPTION"),
            ]
        )
        is None
    )


def test_missing_validation_status_is_still_a_target():
    """Absent status is not INVALID; the writer extracts from it."""
    assert select_items_section([section(None)]) is not None


def test_stored_status_reports_a_split_verdict():
    assert _stored_status([]) is None
    assert _stored_status([{"reconciliation_status": "match"}] * 3) == "match"
    assert (
        _stored_status(
            [
                {"reconciliation_status": "match"},
                {"reconciliation_status": "near"},
            ]
        )
        == "match|near"
    )
