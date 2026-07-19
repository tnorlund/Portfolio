"""Pin the shared price-token patterns byte-for-byte (P1b, #1188).

Each historical pattern moved into ``price_tokens`` verbatim; a change to any
compiled pattern string reflows rendered output at its call site, so the exact
strings are pinned here. If you intend a behavior change, update the pin AND
re-capture the render regression baseline.
"""

from receipt_agent.agents.label_evaluator.rendering.price_tokens import (
    DOLLARTREE_PRICE_TOKEN,
    GLYPH_AMOUNT_TOKEN,
    PRICE_TOKEN,
    PROFILE_PRICE_TOKEN,
    SYNTH_PRICE_TOKEN,
    is_price_token,
)


def test_canonical_pattern_pinned():
    # receipt_grid's historical _PRICE_TOKEN (the canonical form).
    assert PRICE_TOKEN.pattern == (
        r"^[-+]?\$?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}[-+]?[A-Z]?$"
    )


def test_profile_pattern_pinned():
    # font_profile's historical _PRICE_TOKEN.
    assert PROFILE_PRICE_TOKEN.pattern == (
        r"^\$?\d{1,3}(?:,\d{3})*(?:\.\d{2})\$?[A-Z]?$"
    )


def test_synth_pattern_pinned():
    # render_synthetic_receipts' historical _PRICE_TOKEN_RE.
    assert SYNTH_PRICE_TOKEN.pattern == (
        r"^[-+]?\$?\d{1,3}(?:,\d{3})*\.\d{2}[-+]?$"
    )


def test_dollartree_pattern_pinned():
    # compose_dollartree's historical _PRICE_RE.
    assert DOLLARTREE_PRICE_TOKEN.pattern == r"^\$?\d*\.?\d+T?$"


def test_glyph_pattern_pinned():
    # glyph_renderer's historical _AMOUNT_RE.
    assert GLYPH_AMOUNT_TOKEN.pattern == (
        r"^\$?\d{1,4}(?:,\d{3})*\.\d{2}\$?[A-Z]?-?$"
    )


def test_call_sites_reference_shared_patterns():
    from receipt_agent.agents.label_evaluator.rendering import (
        font_profile,
        glyph_renderer,
        receipt_grid,
    )

    assert receipt_grid._PRICE_TOKEN is PRICE_TOKEN
    assert font_profile._PRICE_TOKEN is PROFILE_PRICE_TOKEN
    assert glyph_renderer._AMOUNT_RE is GLYPH_AMOUNT_TOKEN


def test_canonical_predicate():
    for text in ("1.99", "$12.00", "1,299.00", "3.49 T", "-4.00", "1000.00"):
        assert is_price_token(text), text
    for text in ("GROCERY", "1.999", "12", "", None, "1.2.3"):
        assert not is_price_token(text), text
