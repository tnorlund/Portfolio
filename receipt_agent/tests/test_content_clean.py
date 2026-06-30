"""Tests for render-time synthetic-content cleanup (deterministic, no AWS)."""

from __future__ import annotations

from receipt_agent.agents.label_evaluator.rendering.content_clean import (
    canonicalize_auth_tokens,
    clean_for_render,
)


def _w(text):
    return {"text": text}


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


def test_clean_for_render_reports_counts():
    receipt = {"lines": [{"words": [_w("Seg#"), _w("1")]}]}
    rep = clean_for_render(receipt)
    assert rep["auth_fixed"] == 1
