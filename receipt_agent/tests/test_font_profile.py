"""Tests for per-merchant font/geometry profiles (no AWS, deterministic)."""

from __future__ import annotations

from types import SimpleNamespace

from receipt_agent.agents.label_evaluator.rendering.font_profile import (
    MerchantFontProfile,
    ReceiptFontProfile,
    build_merchant_font_profile,
    extract_receipt_font_profile,
)


def _box(x: float, y: float, width: float, height: float) -> dict[str, float]:
    return {"x": x, "y": y, "width": width, "height": height}


def _word(
    *,
    text: str,
    line_id: int,
    word_id: int,
    x: float,
    y: float,
    glyph_width: float,
    height: float,
    image_id: str = "image-1",
    receipt_id: int = 1,
) -> SimpleNamespace:
    width = glyph_width * len(text)
    return SimpleNamespace(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box=_box(x, y, width, height),
    )


def _line(
    *, line_id: int, x: float, y: float, width: float, height: float
) -> SimpleNamespace:
    return SimpleNamespace(
        line_id=line_id,
        bounding_box=_box(x, y, width, height),
    )


def _simple_receipt() -> tuple[list[SimpleNamespace], list[SimpleNamespace]]:
    # Three body rows, y-high-is-top, evenly spaced ~0.05 apart. The price
    # column sits at center x ~0.8.
    words = [
        _word(
            text="MILK",
            line_id=1,
            word_id=1,
            x=0.10,
            y=0.80,
            glyph_width=0.02,
            height=0.03,
        ),
        _word(
            text="3.49",
            line_id=1,
            word_id=2,
            x=0.78,
            y=0.80,
            glyph_width=0.02,
            height=0.03,
        ),
        _word(
            text="BREAD",
            line_id=2,
            word_id=1,
            x=0.10,
            y=0.75,
            glyph_width=0.02,
            height=0.03,
        ),
        _word(
            text="2.99",
            line_id=2,
            word_id=2,
            x=0.78,
            y=0.75,
            glyph_width=0.02,
            height=0.03,
        ),
        _word(
            text="EGGS",
            line_id=3,
            word_id=1,
            x=0.10,
            y=0.70,
            glyph_width=0.02,
            height=0.03,
        ),
        _word(
            text="5.00",
            line_id=3,
            word_id=2,
            x=0.78,
            y=0.70,
            glyph_width=0.02,
            height=0.03,
        ),
    ]
    lines = [
        _line(line_id=1, x=0.10, y=0.80, width=0.72, height=0.03),
        _line(line_id=2, x=0.10, y=0.75, width=0.72, height=0.03),
        _line(line_id=3, x=0.10, y=0.70, width=0.72, height=0.03),
    ]
    return words, lines


def test_extract_receipt_profile_measures_geometry():
    words, lines = _simple_receipt()
    profile = extract_receipt_font_profile(words, lines)

    assert profile is not None
    # Font height comes straight from the word boxes.
    assert abs(profile.font_height - 0.03) < 1e-6
    # Char width = box width / visible chars = glyph_width.
    assert abs(profile.char_width - 0.02) < 1e-6
    assert abs(profile.char_aspect - (0.02 / 0.03)) < 1e-6
    # Rows are 0.05 apart center-to-center.
    assert profile.line_pitch is not None
    assert abs(profile.line_pitch - 0.05) < 1e-6
    # Price column = center x of the "x.xx" tokens (0.78 + width/2).
    assert profile.price_column_x is not None
    assert abs(profile.price_column_x - (0.78 + (0.02 * 4) / 2)) < 1e-6


def test_extract_receipt_profile_without_lines_uses_word_line_ids():
    words, _ = _simple_receipt()
    profile = extract_receipt_font_profile(words, None)

    assert profile is not None
    assert profile.line_pitch is not None
    assert abs(profile.line_pitch - 0.05) < 1e-6


def test_extract_receipt_profile_returns_none_for_empty():
    assert extract_receipt_font_profile([], []) is None


def test_price_column_none_when_no_price_tokens():
    words = [
        _word(
            text="HELLO",
            line_id=1,
            word_id=1,
            x=0.1,
            y=0.8,
            glyph_width=0.02,
            height=0.03,
        ),
        _word(
            text="WORLD",
            line_id=2,
            word_id=1,
            x=0.1,
            y=0.75,
            glyph_width=0.02,
            height=0.03,
        ),
    ]
    profile = extract_receipt_font_profile(words, None)
    assert profile is not None
    assert profile.price_column_x is None


def test_price_column_picks_rightmost_aligned_column():
    # A unit-price column on the left (~0.55) and a line-total column on the
    # right (~0.82). The profile must lock onto the right-aligned line totals,
    # not a median between the two columns.
    words = []
    for row, (unit, total) in enumerate(
        [("1.20", "3.49"), ("0.99", "2.99"), ("2.50", "5.00")], start=1
    ):
        y = 0.80 - (row - 1) * 0.05
        words.append(
            _word(
                text="ITEM",
                line_id=row,
                word_id=1,
                x=0.10,
                y=y,
                glyph_width=0.02,
                height=0.03,
            )
        )
        # Unit price: left column, right edge ~0.59.
        words.append(
            _word(
                text=unit,
                line_id=row,
                word_id=2,
                x=0.51,
                y=y,
                glyph_width=0.02,
                height=0.03,
            )
        )
        # Line total: right column, right edge ~0.86.
        words.append(
            _word(
                text=total,
                line_id=row,
                word_id=3,
                x=0.78,
                y=y,
                glyph_width=0.02,
                height=0.03,
            )
        )
    profile = extract_receipt_font_profile(words, None)
    assert profile is not None
    assert profile.price_column_x is not None
    # Right column center = 0.78 + (0.02*4)/2 = 0.82, NOT the ~0.70 mid-point.
    assert abs(profile.price_column_x - 0.82) < 1e-6


def test_price_column_none_when_two_unaligned_prices():
    # Two amount-like tokens in different columns cannot prove a column.
    words = [
        _word(
            text="3.49",
            line_id=1,
            word_id=1,
            x=0.20,
            y=0.80,
            glyph_width=0.02,
            height=0.03,
        ),
        _word(
            text="9.99",
            line_id=2,
            word_id=1,
            x=0.70,
            y=0.75,
            glyph_width=0.02,
            height=0.03,
        ),
    ]
    profile = extract_receipt_font_profile(words, None)
    assert profile is not None
    assert profile.price_column_x is None


def test_build_merchant_profile_aggregates_medians():
    words_a, lines_a = _simple_receipt()
    profile_a = extract_receipt_font_profile(words_a, lines_a)

    # A second, slightly larger receipt.
    words_b = [
        _word(
            text="MILK",
            line_id=1,
            word_id=1,
            x=0.10,
            y=0.80,
            glyph_width=0.03,
            height=0.04,
            image_id="image-2",
        ),
        _word(
            text="9.99",
            line_id=1,
            word_id=2,
            x=0.70,
            y=0.80,
            glyph_width=0.03,
            height=0.04,
            image_id="image-2",
        ),
        _word(
            text="BREAD",
            line_id=2,
            word_id=1,
            x=0.10,
            y=0.74,
            glyph_width=0.03,
            height=0.04,
            image_id="image-2",
        ),
        _word(
            text="8.99",
            line_id=2,
            word_id=2,
            x=0.70,
            y=0.74,
            glyph_width=0.03,
            height=0.04,
            image_id="image-2",
        ),
    ]
    profile_b = extract_receipt_font_profile(words_b, None)

    merchant = build_merchant_font_profile(
        "Test Merchant", [profile_a, profile_b]
    )
    assert isinstance(merchant, MerchantFontProfile)
    assert merchant.receipt_count == 2
    # Median of {0.03, 0.04}.
    assert abs(merchant.font_height - 0.035) < 1e-6
    assert set(merchant.source_image_ids) == {"image-1", "image-2"}


def test_to_geometry_params_scales_to_canvas():
    words, lines = _simple_receipt()
    profile = extract_receipt_font_profile(words, lines)
    merchant = build_merchant_font_profile("M", [profile])
    params = merchant.to_geometry_params(canvas=1000)

    assert abs(params["char_width_px"] - 20.0) < 1e-6
    assert abs(params["font_height_px"] - 30.0) < 1e-6
    assert abs(params["line_step_px"] - 50.0) < 1e-6
    assert params["price_column_x_px"] is not None


def test_build_merchant_profile_none_for_empty():
    assert build_merchant_font_profile("M", []) is None


def test_extract_uses_dominant_cluster_when_letters_present():
    # With letters, a clustering is computed and the dominant cluster's metrics
    # drive typography. Build a uniform-style receipt so the dominant cluster's
    # height matches the word height.
    words = []
    letters = []
    for row, (text, price) in enumerate(
        [("MILK", "3.49"), ("BREAD", "2.99"), ("EGGS", "5.00")], start=1
    ):
        y = 0.80 - (row - 1) * 0.05
        for word_id, token in enumerate((text, price), start=1):
            x = 0.10 if word_id == 1 else 0.78
            gw = 0.02
            words.append(
                _word(
                    text=token,
                    line_id=row,
                    word_id=word_id,
                    x=x,
                    y=y,
                    glyph_width=gw,
                    height=0.03,
                )
            )
            for idx, ch in enumerate(token):
                letters.append(
                    SimpleNamespace(
                        image_id="image-1",
                        receipt_id=1,
                        line_id=row,
                        word_id=word_id,
                        letter_id=idx + 1,
                        text=ch,
                        bounding_box=_box(x + idx * gw, y, gw, 0.03),
                        confidence=0.96,
                        angle_radians=0.0,
                    )
                )

    profile = extract_receipt_font_profile(words, None, letters=letters)
    assert isinstance(profile, ReceiptFontProfile)
    assert profile.font_height > 0
    assert profile.char_width > 0
