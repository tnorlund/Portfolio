"""Stylemap-declared classifier rules: one source for stylescan + renderer."""

from __future__ import annotations

import json
import os
import sys

import pytest
from glyphstudio import stylescan
from glyphstudio.stylerules import (
    FONTS_DIR,
    compile_rules,
    rule_sections,
    rules_for_font,
)

_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
)
sys.path.insert(0, os.path.join(_ROOT, "receipt_agent"))


def _speedway():
    with open(
        os.path.join(FONTS_DIR, "speedway", "stylemap.json"), encoding="utf-8"
    ) as fh:
        return json.load(fh)


def test_compile_rules_defaults_to_case_insensitive():
    rules = compile_rules({"rules": [{"section": "x", "pattern": "^abc$"}]})
    assert rules is not None and rules[0][0] == "x"
    assert rules[0][1].search("ABC")
    strict = compile_rules(
        {"rules": [{"section": "x", "pattern": "^abc$", "flags": ""}]}
    )
    assert strict is not None and not strict[0][1].search("ABC")


def test_absent_rules_return_none():
    assert compile_rules(None) is None
    assert compile_rules({"sections": {}}) is None
    assert compile_rules({"rules": []}) is None


def test_roastrice_stylemap_declares_restaurant_rules():
    assert rules_for_font("roastrice"), "roastrice stylemap must carry rules"
    assert (
        stylescan._classify("Roast and Rice Kitchen", False, "roastrice")
        == "store_header"
    )
    assert stylescan._classify("Table# A8 Guest: 2", False, "roastrice") == (
        "table"
    )
    assert stylescan._classify("Server: Jan", False, "roastrice") == "server"
    assert stylescan._classify("Gratuity Suggestion", False, "roastrice") == (
        "tip"
    )
    assert stylescan._classify("18.00% = $19.95", True, "roastrice") == "tip"
    assert stylescan._classify("Total Due $110.85", True, "roastrice") == (
        "total_line"
    )
    assert "roastrice" not in stylescan._MERCHANT_RULES


def test_speedway_stylemap_declares_rules_used_by_stylescan():
    assert rules_for_font("speedway"), "speedway stylemap must carry rules"
    assert (
        stylescan._classify("DEBIT $13.38", True, "speedway") == "tender_line"
    )
    assert stylescan._classify("SUBTOTAL", False, "speedway") == "summary"
    assert stylescan._classify("SPEEDWAY", False, "speedway") == "store_header"
    assert "speedway" not in stylescan._MERCHANT_RULES


def test_renderer_reads_the_same_rules():
    from receipt_agent.agents.label_evaluator.rendering import (
        receipt_stylemap as rs,
    )

    stylemap = _speedway()
    declared = rs.declared_rules(stylemap)
    assert declared is not None
    assert [s for s, _ in declared] == [
        s for s, _ in rules_for_font("speedway")
    ]
    assert rs.row_style(stylemap, "DEBIT $13.38")["bold"] is True
    assert rs.row_style(stylemap, "ACCT#: ************1454")["bold"] is False
    assert "speedway" not in rs._MERCHANT_RULES


def test_every_declared_rule_section_has_a_style_or_is_intentionally_body():
    stylemap = _speedway()
    styled = set(stylemap["sections"])
    for section in rule_sections(stylemap):
        # A rule may classify into a section without a style entry (falls
        # through to body), but a bold/scaled style must be reachable.
        if stylemap["sections"].get(section, {}).get("weight") == "bold":
            assert section in styled


@pytest.mark.parametrize("font", sorted(os.listdir(FONTS_DIR)))
def test_all_stylemap_rules_compile(font):
    path = os.path.join(FONTS_DIR, font, "stylemap.json")
    if not os.path.exists(path):
        pytest.skip("no stylemap")
    with open(path, encoding="utf-8") as fh:
        compile_rules(json.load(fh))
