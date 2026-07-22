"""W-H eval consumer: profile_columns selects a §7.2 layout variant.

These tests drive ``full_fidelity_eval.profile_columns`` against committed
fixture templates carrying ``template.variants[]`` and assert the columns come
from the variant whose ``classifier_hint`` matches the receipt under eval
(its normalized OCR word set / canonical section sequence).

RED-ON-MAIN: ``test_profile_columns_selects_matching_variant`` and
``test_profile_columns_tie_break_prefers_highest_support`` FAIL on current
origin/main, where ``profile_columns`` reads only the top-level DEFAULT
columns and ignores ``variants`` entirely -- they return the DEFAULT lane
(x=0.86) instead of the variant lane. They pass only with the variant-aware
reader wired in this PR.

The empty-variants regression (``test_profile_columns_*_default*``) passes on
both main and this branch -- proving the DEFAULT path is byte-identical.
"""

import json
import os
import sys
from types import SimpleNamespace

# Resolve BOTH the eval package and this worktree's receipt_dynamo ahead of any
# editable install, so profile_columns imports the merchant_truth_variants
# module added in this PR.
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for _p in (
    os.path.join(REPO, "receipt_dynamo"),
    os.path.join(REPO, "synthesis_loop"),
    os.path.join(REPO, "scripts"),
    os.path.join(REPO, "receipt_agent"),
    os.path.join(REPO, "tools", "glyph-studio", "py"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import full_fidelity_eval as ffe  # noqa: E402

from receipt_dynamo.merchant_truth_variants import (  # noqa: E402
    normalize_word_set,
)

FIXTURE = os.path.join(HERE, "fixtures", "variant_layout_two_variants.json")


def _cases() -> dict:
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def _truth(template: dict) -> "ffe.TruthContext":
    artifact = SimpleNamespace(
        slug="costco",
        version=2,
        bundle_hash="deadbeef",
        components={"layout": {"template": template}},
    )
    return ffe.TruthContext(
        artifact=artifact,
        expected_version=2,
        expected_bundle_hash="deadbeef",
        mode="fixture",
    )


def _amount_x(columns: list[dict]) -> float:
    return next(c["x"] for c in columns if c["role"] == "amount")


def test_profile_columns_selects_matching_variant():
    """RED ON MAIN: profile_columns must return the matched variant's lane."""
    case = _cases()["text_marker"]
    truth = _truth(case["template"])
    words = normalize_word_set(case["matching_word_set"])
    cols = ffe.profile_columns(
        truth,
        "items",
        section_sequence=case["matching_section_sequence"],
        word_set=words,
    )
    assert _amount_x(cols) == case["variant_amount_x"]  # 0.70, not 0.86


def test_profile_columns_section_presence_variant():
    case = _cases()["section_presence"]
    truth = _truth(case["template"])
    cols = ffe.profile_columns(
        truth,
        "items",
        section_sequence=case["matching_section_sequence"],
    )
    assert _amount_x(cols) == case["variant_amount_x"]


def test_profile_columns_tie_break_prefers_highest_support():
    """RED ON MAIN: both variants match; highest support must win."""
    case = _cases()["tie_break"]
    truth = _truth(case["template"])
    words = normalize_word_set(case["matching_word_set"])
    cols = ffe.profile_columns(truth, "items", word_set=words)
    assert _amount_x(cols) == case["winner_amount_x"]


def test_profile_columns_no_match_uses_default():
    case = _cases()["text_marker"]
    truth = _truth(case["template"])
    cols = ffe.profile_columns(
        truth,
        "items",
        section_sequence=[],
        word_set=frozenset(),
    )
    assert _amount_x(cols) == case["default_amount_x"]


def test_profile_columns_malformed_hint_uses_default():
    case = _cases()["malformed"]
    truth = _truth(case["template"])
    words = normalize_word_set(case["word_set"])
    cols = ffe.profile_columns(
        truth,
        "items",
        section_sequence=case["section_sequence"],
        word_set=words,
    )
    assert _amount_x(cols) == case["default_amount_x"]


def test_profile_columns_empty_variants_byte_identical_to_default():
    """The Costco (REFUTE) shape: variants[] empty. profile_columns must
    return EXACTLY the template's top-level columns -- identical to a
    variant-blind read, on both main and this branch."""
    case = _cases()["empty_variants"]
    template = case["template"]
    truth = _truth(template)
    words = normalize_word_set(case["word_set"])
    for section in ("items", "summary"):
        cols = ffe.profile_columns(
            truth,
            section,
            section_sequence=case["section_sequence"],
            word_set=words,
        )
        assert cols == [dict(c) for c in template["columns"][section]]


def test_profile_columns_default_call_still_works():
    """Legacy call sites pass no variant inputs; a variant-blind template
    keeps returning its DEFAULT columns."""
    case = _cases()["empty_variants"]
    truth = _truth(case["template"])
    cols = ffe.profile_columns(truth, "items")
    assert _amount_x(cols) == case["default_amount_x"]
