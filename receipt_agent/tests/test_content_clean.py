"""Tests for render-time synthetic-content cleanup (deterministic, no AWS)."""

from __future__ import annotations

from receipt_agent.agents.label_evaluator.rendering.content_clean import (
    canonicalize_auth_tokens,
    canonicalize_dates,
    clean_for_render,
    fix_currency_decimals,
    fix_items_sold_separator,
)


def _w(text):
    return {"text": text}


def test_canonicalize_dates_repairs_garbled_before_time():
    # A clean 10/20/2025 exists; the slashless + month-0 garbles before a time
    # are repaired to it, while store numbers (no following time) are untouched.
    words = [
        _w("10/20/2025"), _w("13:44"), _w("117"),
        _w("1072072025"), _w("3:44"),        # slashless garble -> canonical
        _w("0/20/2025"), _w("13:44"),        # invalid month 0 -> canonical
        _w("529300203501"),                  # long digit id, no time after -> kept
    ]
    n = canonicalize_dates(words)
    assert n == 2
    assert words[3]["text"] == "10/20/2025"
    assert words[5]["text"] == "10/20/2025"
    assert words[7]["text"] == "529300203501"


def test_canonicalize_dates_noop_without_a_valid_anchor():
    words = [_w("1072072025"), _w("3:44")]  # no clean date to anchor on
    assert canonicalize_dates(words) == 0
    assert words[0]["text"] == "1072072025"


def test_fix_items_sold_separator():
    words = [_w("SOLD"), _w("*"), _w("4"), _w("****"), _w("TOTAL")]
    assert fix_items_sold_separator(words) == 1
    assert words[1]["text"] == "="
    assert words[3]["text"] == "****"  # masking run untouched


def test_emv_auth_repairs():
    words = [
        _w("VERIFIED"), _w("BY"), _w("WIN"),       # WIN -> PIN (after BY)
        _w("Seg#"), _w("205061"),                   # Seg# -> Seq#
        _w("DID:"), _w("A0000000980840"),           # DID: + hex -> AID:
        _w("CHANGE"), _w("0.00."),                  # trailing-dot price
    ]
    n = canonicalize_auth_tokens(words)
    texts = [w["text"] for w in words]
    assert n == 4
    assert texts[2] == "PIN"
    assert texts[3] == "Seq#"
    assert texts[5] == "AID:"
    assert texts[8] == "0.00"


def test_win_not_changed_without_by_context():
    # "WIN" not preceded by "BY" is left alone (avoid corrupting legit text).
    words = [_w("YOU"), _w("WIN"), _w("A"), _w("PRIZE")]
    canonicalize_auth_tokens(words)
    assert words[1]["text"] == "WIN"


def test_did_not_changed_without_hex_aid():
    # "DID:" without a following hex AID is left alone.
    words = [_w("DID:"), _w("you")]
    canonicalize_auth_tokens(words)
    assert words[0]["text"] == "DID:"


def test_currency_three_decimals_fixed_rates_preserved():
    words = [
        _w("TOTAL"), _w("19.981"),                 # 3-decimal total -> 19.98
        _w("CA"), _w("TAX"), _w("9.75000"),        # tax RATE -> untouched
        _w("BALANCE"), _w("$1,234.567"),           # comma-grouped 3-dec -> .56
    ]
    n = fix_currency_decimals(words)
    assert n == 2
    assert words[1]["text"] == "19.98"
    assert words[4]["text"] == "9.75000"          # rate preserved (TAX context)
    assert words[6]["text"] == "$1,234.56"


def test_clean_for_render_reports_counts():
    receipt = {"lines": [{"words": [_w("Seg#"), _w("1"), _w("TOTAL"), _w("5.001")]}]}
    rep = clean_for_render(receipt)
    assert rep["auth_fixed"] == 1
    assert rep["totals_fixed"] == 1
