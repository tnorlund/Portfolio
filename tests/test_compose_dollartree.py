"""Unit tests for the Dollar Tree canonical composer (PR #1187 review)."""

from __future__ import annotations

import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "synthesis_loop")
)

from compose_dollartree import _norm_price, canonical_words  # noqa: E402


def test_norm_price_parses_shattered_tokens_with_flag():
    assert _norm_price("1251") == ("1.25", True)
    assert _norm_price("1.251") == ("1.25", True)
    assert _norm_price("1.25T") == ("1.25", True)
    assert _norm_price("1.25") == ("1.25", False)
    assert _norm_price(".52") == ("0.52", False)
    assert _norm_price("junk") is None


def _word(text, x0, y, w=40, line_id=1, labels=None):
    return {
        "text": text,
        "bbox": [x0, y, x0 + w, y + 20],
        "labels": labels or [],
        "line_id": line_id,
        "word_id": 1,
    }


def test_quantity_arithmetic_never_copies_totals():
    words = [
        _word("DESCRIPTION", 0, 800, line_id=1),
        _word("SCRUB BRUSH THING", 10, 700, line_id=2),
        _word("2", 620, 702, w=15, line_id=3),
        _word("1.25", 720, 701, line_id=4),
    ]
    out = canonical_words(words)
    texts = [w["text"] for w in out]
    assert "2.50" in texts  # total = 1.25 x 2, not a bare copy
    assert "2.50T" not in texts  # no fabricated tax flag


def test_observed_tax_flag_is_preserved():
    words = [
        _word("DESCRIPTION", 0, 800, line_id=1),
        _word("SCRUB BRUSH THING", 10, 700, line_id=2),
        _word("1.25T", 920, 701, line_id=3),
    ]
    out = canonical_words(words)
    assert any(w["text"] == "1.25T" for w in out)


def test_long_receipts_scale_pitch_instead_of_clipping():
    words = [_word("DESCRIPTION", 0, 900, line_id=1)]
    for i in range(30):
        y = 850 - i * 25
        words.append(_word(f"ITEM NUMBER {i} THING", 10, y, line_id=2 + i))
        words.append(_word("1.25", 720, y + 1, line_id=100 + i))
    out = canonical_words(words)
    assert out and min(w["bbox"][1] for w in out) >= 0.0
