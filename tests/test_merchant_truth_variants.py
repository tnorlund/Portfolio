"""Unit tests for the shared §7.2 layout-variant selector (W-H).

One implementation of the selection algorithm (§7.2 of
``docs/architecture/MERCHANT_TRUTH_DYNAMO.md``) backs both readers -- the eval
``profile_columns`` and ``merchant_truth_diff.diff_layout`` -- and the W-G
generator's hint emission. These tests pin every hint type, the multi-match
support tie-break, the equal-support lexical tie-break, malformed/unknown-type
fallback to DEFAULT, and the empty-variants no-op.

Mutation-honesty anchors (named so a reviewer can flip the code and watch the
right test go red):
  * ``test_tie_break_highest_support_wins`` fails if the tie-break selects the
    lowest support instead of the highest.
  * ``test_no_match_falls_back_to_default`` and
    ``test_unknown_type_falls_back_to_default`` fail if a non-matching /
    unknown-type hint is allowed to select a variant instead of DEFAULT.
"""

import json
import os
import sys

# Resolve receipt_dynamo from THIS worktree (ahead of any editable install).
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "receipt_dynamo"))

import pytest  # noqa: E402

from receipt_dynamo.merchant_truth_variants import (  # noqa: E402
    HINT_SECTION_PRESENCE,
    HINT_TEXT_MARKER,
    hint_matches,
    make_section_order_hint,
    make_section_presence_hint,
    make_text_marker_hint,
    marker_tokens,
    normalize_word_set,
    select_variant,
    variant_columns,
)

FIXTURE = os.path.join(HERE, "fixtures", "variant_layout_two_variants.json")


def _cases() -> dict:
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def _amount_x(columns: list[dict]) -> float:
    return next(c["x"] for c in columns if c["role"] == "amount")


# --- normalization ---------------------------------------------------------


def test_normalize_word_set_strips_punctuation_and_splits():
    assert normalize_word_set(["Self-Checkout", "Costco!"]) == frozenset(
        {"SELF", "CHECKOUT", "COSTCO"}
    )
    # word dicts (the eval's shape) are accepted directly
    assert normalize_word_set([{"text": "Member 12"}]) == frozenset(
        {"MEMBER", "12"}
    )


def test_marker_tokens_splits_underscore_marker():
    assert marker_tokens("self_checkout") == ["SELF", "CHECKOUT"]


# --- hint matching (each type) --------------------------------------------


def test_text_marker_matches_word_set():
    case = _cases()["text_marker"]
    template = case["template"]
    words = normalize_word_set(case["matching_word_set"])
    variant = select_variant(template, word_set=words)
    assert variant is not None
    assert variant["variant_id"] == "self-checkout"
    assert _amount_x(variant["columns"]["items"]) == case["variant_amount_x"]


def test_text_marker_missing_token_does_not_match():
    case = _cases()["text_marker"]
    words = normalize_word_set(case["nonmatching_word_set"])
    assert select_variant(case["template"], word_set=words) is None


def test_section_presence_matches_sequence():
    case = _cases()["section_presence"]
    variant = select_variant(
        case["template"], section_sequence=case["matching_section_sequence"]
    )
    assert variant is not None
    assert variant["variant_id"] == "survey-variant"
    assert _amount_x(variant["columns"]["items"]) == case["variant_amount_x"]


def test_section_presence_absent_section_does_not_match():
    case = _cases()["section_presence"]
    assert (
        select_variant(
            case["template"],
            section_sequence=case["nonmatching_section_sequence"],
        )
        is None
    )


def test_section_order_matches_relative_order():
    case = _cases()["section_order"]
    variant = select_variant(
        case["template"], section_sequence=case["matching_section_sequence"]
    )
    assert variant is not None
    assert variant["variant_id"] == "payment-before-total"


def test_section_order_wrong_order_does_not_match():
    case = _cases()["section_order"]
    assert (
        select_variant(
            case["template"],
            section_sequence=case["nonmatching_section_sequence"],
        )
        is None
    )


def test_section_order_is_subsequence_not_contiguous():
    hint = make_section_order_hint(["items", "total_line"])
    assert hint_matches(
        hint,
        section_sequence=["storefront", "items", "payment", "total_line"],
        word_set=frozenset(),
    )


# --- tie-break -------------------------------------------------------------


def test_tie_break_highest_support_wins():
    """Both hints match; the highest-support variant must win (§7.2)."""
    case = _cases()["tie_break"]
    words = normalize_word_set(case["matching_word_set"])
    variant = select_variant(case["template"], word_set=words)
    assert variant is not None
    assert variant["variant_id"] == case["winner_variant_id"]
    assert _amount_x(variant["columns"]["items"]) == case["winner_amount_x"]
    # explicitly: the low-support lane must NOT be the one selected
    assert _amount_x(variant["columns"]["items"]) != case["loser_amount_x"]


def test_tie_break_equal_support_lexical_smallest_variant_id():
    case = _cases()["tie_break_equal_support"]
    words = normalize_word_set(case["matching_word_set"])
    variant = select_variant(case["template"], word_set=words)
    assert variant is not None
    assert variant["variant_id"] == case["winner_variant_id"]
    assert _amount_x(variant["columns"]["items"]) == case["winner_amount_x"]


# --- fallback to DEFAULT ---------------------------------------------------


def test_no_match_falls_back_to_default():
    """No hint matches -> DEFAULT (select_variant returns None)."""
    case = _cases()["text_marker"]
    assert select_variant(case["template"], word_set=frozenset()) is None


def test_unknown_type_falls_back_to_default():
    case = _cases()["malformed"]
    words = normalize_word_set(case["word_set"])
    variant = select_variant(
        case["template"],
        section_sequence=case["section_sequence"],
        word_set=words,
    )
    assert variant is None
    # and variant_columns therefore reads the DEFAULT lane
    cols = variant_columns(
        case["template"],
        "items",
        section_sequence=case["section_sequence"],
        word_set=words,
    )
    assert _amount_x(cols) == case["default_amount_x"]


def test_malformed_hints_never_match():
    for hint in ({"type": "regex_match"}, "self-checkout", None, 42, {}):
        assert not hint_matches(
            hint, section_sequence=["items"], word_set=frozenset({"SELF"})
        )


def test_empty_args_hint_never_matches():
    assert not hint_matches(
        make_text_marker_hint([]),
        section_sequence=["items"],
        word_set=frozenset({"SELF"}),
    )
    assert not hint_matches(
        {"type": HINT_SECTION_PRESENCE, "sections": []},
        section_sequence=["items"],
        word_set=frozenset(),
    )


# --- empty-variants regression (DEFAULT byte-identical) --------------------


def test_empty_variants_selects_default():
    case = _cases()["empty_variants"]
    template = case["template"]
    words = normalize_word_set(case["word_set"])
    assert (
        select_variant(
            template,
            section_sequence=case["section_sequence"],
            word_set=words,
        )
        is None
    )


def test_variant_columns_empty_variants_equals_raw_default():
    """variant_columns on a variant-blind template returns exactly the
    template's top-level columns -- the byte-identical regression guard."""
    case = _cases()["empty_variants"]
    template = case["template"]
    words = normalize_word_set(case["word_set"])
    for section in ("items", "summary"):
        got = variant_columns(
            template,
            section,
            section_sequence=case["section_sequence"],
            word_set=words,
        )
        assert got == [dict(c) for c in template["columns"][section]]


def test_absent_variants_key_selects_default():
    template = {"version": 1, "columns": {"items": []}}
    assert select_variant(template, word_set=frozenset({"SELF"})) is None


# --- builders round-trip with the matcher ---------------------------------


def test_builders_emit_matchable_hints():
    tm = make_text_marker_hint(["self_checkout"])
    assert tm == {"type": HINT_TEXT_MARKER, "tokens": ["CHECKOUT", "SELF"]}
    assert hint_matches(
        tm,
        section_sequence=[],
        word_set=normalize_word_set(["SELF-CHECKOUT lane"]),
    )
    sp = make_section_presence_hint(["Survey"])
    assert sp == {"type": HINT_SECTION_PRESENCE, "sections": ["survey"]}
    assert hint_matches(
        sp, section_sequence=["items", "survey"], word_set=frozenset()
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
