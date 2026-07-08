"""Contract tests for the canonical section vocabulary + seed projections.

These are pure-function tests (no Dynamo, no pixels). They pin the two seed
sources against drift: a new CORE label or a new stylescan rule name should
force an explicit decision here rather than silently produce no section.
"""

import os
import sys

import pytest

from glyphstudio import sections

# receipt_dynamo lives as a sibling package, not pip-installed; add it so we can
# assert the projection against the real CORE_LABELS (single source of truth).
_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
_RD = os.path.join(_ROOT, "receipt_dynamo")
if _RD not in sys.path:
    sys.path.insert(0, _RD)


def test_canonical_section_set_is_ten():
    assert len(sections.CANONICAL_SECTIONS) == 10
    assert len(sections.CANONICAL_SECTION_SET) == 10


def test_section_label_roundtrip():
    for s in sections.CANONICAL_SECTIONS:
        lbl = sections.section_label(s)
        assert lbl == "SECTION_" + s.upper()
        assert sections.parse_section_label(lbl) == s


def test_parse_section_label_rejects_non_sections():
    assert sections.parse_section_label("GRAND_TOTAL") is None
    assert sections.parse_section_label("SECTION_BOGUS") is None
    assert sections.parse_section_label("") is None
    assert sections.parse_section_label(None) is None
    # case-insensitive prefix
    assert sections.parse_section_label("section_items") == "items"


def test_section_label_rejects_unknown_section():
    with pytest.raises(ValueError):
        sections.section_label("nonsense")


def test_core_label_map_targets_are_canonical():
    for label, (section, conf) in sections.CORE_LABEL_SECTION.items():
        assert label == label.upper()
        assert sections.is_canonical_section(section), (label, section)
        assert conf in ("high", "medium"), (label, conf)


def test_stylescan_map_targets_are_canonical_or_none():
    for raw, section in sections.STYLESCAN_TO_SECTION.items():
        assert raw == raw.lower()
        assert section is None or sections.is_canonical_section(section), (
            raw,
            section,
        )


def test_every_core_label_is_decided():
    """Each CORE label is either projected or on the explicit no-seed list."""
    from receipt_dynamo.constants import CORE_LABELS

    no_seed = {"DATE", "TIME"}  # too positionally scattered to seed
    decided = set(sections.CORE_LABEL_SECTION) | no_seed
    missing = set(CORE_LABELS) - decided
    assert not missing, f"CORE labels with no section decision: {missing}"
    # and no stale entries referencing labels that no longer exist
    stale = set(sections.CORE_LABEL_SECTION) - set(CORE_LABELS)
    assert not stale, f"projection references unknown labels: {stale}"


def test_every_stylescan_rule_name_is_mapped():
    """Every section name any merchant's rules (or the generic classifier) can
    emit must have an entry in STYLESCAN_TO_SECTION."""
    from glyphstudio import stylescan

    emitted = set()
    for rules in stylescan._MERCHANT_RULES.values():
        for name, _rx in rules:
            emitted.add(name)
    # generic hardcoded returns of _classify()
    emitted |= {
        "barcode_caption",
        "section_header",
        "item",
        "separator",
        "other",
    }
    missing = emitted - set(sections.STYLESCAN_TO_SECTION)
    assert not missing, f"unmapped stylescan section names: {missing}"


def test_projection_spot_checks():
    assert sections.section_for_core_label("GRAND_TOTAL") == "total_line"
    assert sections.section_for_core_label("product_name") == "items"
    assert sections.section_for_core_label("DATE") is None
    assert sections.core_label_confidence("SUBTOTAL") == "high"
    assert sections.core_label_confidence("DISCOUNT") == "medium"

    assert sections.normalize_stylescan_section("warehouse_header") == (
        "storefront"
    )
    assert sections.normalize_stylescan_section("extracare") == "footer"
    assert sections.normalize_stylescan_section("separator") is None
    assert sections.normalize_stylescan_section("nope") is None
    assert sections.normalize_stylescan_section(None) is None
