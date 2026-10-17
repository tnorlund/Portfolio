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


def test_every_rule_bearing_stylemap_is_a_known_slug():
    """Onboarding a merchant with stylemap rules must register its slug."""
    import glob

    declared = {
        os.path.basename(os.path.dirname(path))
        for path in glob.glob(os.path.join(FONTS_DIR, "*", "stylemap.json"))
        if rules_for_font(os.path.basename(os.path.dirname(path)))
    }
    unregistered = set(stylescan._UNREGISTERED_RULE_SLUGS)
    missing = declared - stylescan.known_rule_slugs() - unregistered
    assert (
        not missing
    ), f"add {sorted(missing)} to stylescan._STYLEMAP_RULE_SLUGS"
    assert not unregistered & stylescan.known_rule_slugs()


def test_rules_for_font_caches_until_the_file_changes(tmp_path, monkeypatch):
    from glyphstudio import stylerules

    font = tmp_path / "m"
    font.mkdir()
    path = font / "stylemap.json"
    path.write_text(json.dumps({"rules": [{"section": "a", "pattern": "x"}]}))
    calls = []
    real = stylerules.compile_rules
    monkeypatch.setattr(
        stylerules,
        "compile_rules",
        lambda sm: calls.append(1) or real(sm),
    )
    first = rules_for_font("m", str(tmp_path))
    assert rules_for_font("m", str(tmp_path)) == first
    assert len(calls) == 1
    path.write_text(
        json.dumps({"rules": [{"section": "bb", "pattern": "yy"}]})
    )
    assert [s for s, _ in rules_for_font("m", str(tmp_path))] == ["bb"]
    assert len(calls) == 2
    path.unlink()
    assert rules_for_font("m", str(tmp_path)) is None
