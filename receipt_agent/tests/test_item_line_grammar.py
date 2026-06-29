"""Unit tests for the item-line grammar extractor.

Small, fully synthetic receipts exercise each derived feature so the test does
not depend on the real exports (which are validated separately via the CLI).
"""

from __future__ import annotations

from receipt_agent.agents.label_evaluator.merchant_research.item_line_grammar import (
    ItemLineTemplate,
    _templatize,
    extract_item_line_template,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _w(rid, lid, wid, text, x, width, y, height=0.02, image_id="img"):
    return {
        "image_id": image_id,
        "receipt_id": rid,
        "line_id": lid,
        "word_id": wid,
        "text": text,
        "bounding_box": {"x": x, "width": width, "y": y, "height": height},
    }


def _lab(rid, lid, wid, label, image_id="img"):
    return {
        "image_id": image_id,
        "receipt_id": rid,
        "line_id": lid,
        "word_id": wid,
        "label": label,
    }


def _line(rid, lid, text, y, image_id="img"):
    return {
        "image_id": image_id,
        "receipt_id": rid,
        "line_id": lid,
        "text": text,
        "bounding_box": {"x": 0.0, "width": 1.0, "y": y, "height": 0.02},
    }


# ---------------------------------------------------------------------------
# Amazon-Fresh-shaped grammar: trailing F/T flag, NOW marker, SALE/WAS
# sub-line, "..." truncation.
# ---------------------------------------------------------------------------


def _amazon_export():
    words, labels, lines = [], [], []

    # Item 1: discounted, truncated name -> NOW marker + SALE/WAS sub-line.
    words += [
        _w(1, 10, 1, "Greek", 0.05, 0.10, 0.80),
        _w(1, 10, 2, "Yogurt...", 0.16, 0.30, 0.80),
        _w(1, 11, 1, "$4.39", 0.80, 0.10, 0.80),  # right edge 0.90
        _w(1, 11, 2, "F", 0.92, 0.03, 0.80),
        _w(1, 12, 1, "NOW", 0.30, 0.08, 0.795),
    ]
    labels += [
        _lab(1, 10, 1, "PRODUCT_NAME"),
        _lab(1, 10, 2, "PRODUCT_NAME"),
        _lab(1, 11, 1, "LINE_TOTAL"),
    ]
    lines += [_line(1, 13, "SALE 1@ $4.39, WAS: $5.49 each", 0.78)]

    # Item 2: taxable, not discounted, not truncated.
    words += [
        _w(1, 20, 1, "Vodka", 0.05, 0.12, 0.70),
        _w(1, 21, 1, "$15.99", 0.78, 0.12, 0.70),  # right edge 0.90
        _w(1, 21, 2, "T", 0.92, 0.03, 0.70),
    ]
    labels += [
        _lab(1, 20, 1, "PRODUCT_NAME"),
        _lab(1, 21, 1, "LINE_TOTAL"),
    ]

    return {
        "merchant_name": "Amazon Fresh",
        "receipt_words": words,
        "receipt_word_labels": labels,
        "receipt_lines": lines,
    }


def test_amazon_like_grammar():
    t = extract_item_line_template(_amazon_export())
    assert isinstance(t, ItemLineTemplate)
    assert t.sample_count == 2
    assert t.receipt_count == 1

    # tax flag: F + T, trailing, on the price's OCR line.
    assert t.tax_flag.present
    assert set(t.tax_flag.chars) == {"F", "T"}
    assert t.tax_flag.position == "trailing"
    assert t.tax_flag.trailing_same_line
    assert t.tax_flag.trailing_count == 2
    assert t.tax_flag.leading_count == 0

    # markdown marker
    assert t.markdown_marker.present
    assert t.markdown_marker.token == "NOW"

    # sub-line templated
    assert t.sub_line.present
    assert "WAS" in (t.sub_line.template or "")
    assert "{price}" in (t.sub_line.template or "")

    # truncation: 1 of 2 rows; "Greek Yogurt" -> 12 visible chars
    assert t.name_truncation.present
    assert t.name_truncation.truncated_fraction == 0.5
    assert t.name_truncation.max_char_width == len("Greek Yogurt")

    # columns
    assert abs(t.columns.price_right_x - 0.90) < 1e-6
    assert abs(t.columns.name_start_x - 0.05) < 1e-6
    assert abs(t.columns.flag_x - 0.92) < 1e-6
    assert t.confidence > 0.0


# ---------------------------------------------------------------------------
# Costco-shaped grammar: "itemnum NAME price flag" with a DUAL flag scheme --
# 'A' trailing taxable rows and 'E' in the far-left margin for tax-exempt rows.
# No markdown marker, no sub-line, no truncation.
# ---------------------------------------------------------------------------


def _costco_export():
    words, labels = [], []
    wid_seq = 0

    def add_row(idx, y, exempt):
        nonlocal wid_seq
        lid = 100 + idx
        wid_seq += 1
        # item number (unlabeled), product name, price
        words.append(_w(1, lid, 1, f"{1000 + idx}", 0.20, 0.12, y))
        words.append(_w(1, lid, 2, "PRODUCT", 0.35, 0.25, y))
        words.append(_w(1, lid, 3, "13.99", 0.83, 0.12, y))  # right edge 0.95
        labels.append(_lab(1, lid, 2, "PRODUCT_NAME"))
        labels.append(_lab(1, lid, 3, "LINE_TOTAL"))
        if exempt:
            # leading 'E' in the far-left margin (different OCR line, same y)
            words.append(_w(1, 200 + idx, 1, "E", 0.01, 0.04, y))
        else:
            # trailing 'A' right of the price
            words.append(_w(1, lid, 4, "A", 0.96, 0.03, y))

    # 4 exempt rows (E) + 2 taxable rows (A)
    for i in range(4):
        add_row(i, 0.80 - i * 0.05, exempt=True)
    for i in range(4, 6):
        add_row(i, 0.80 - i * 0.05, exempt=False)

    return {
        "merchant_name": "Costco Wholesale",
        "receipt_words": words,
        "receipt_word_labels": labels,
    }


def test_costco_like_grammar():
    t = extract_item_line_template(_costco_export())
    assert t.sample_count == 6

    # dual flag scheme
    assert t.tax_flag.present
    assert set(t.tax_flag.chars) == {"E", "A"}
    assert t.tax_flag.position == "mixed"
    assert t.tax_flag.trailing_count == 2  # 'A'
    assert t.tax_flag.leading_count == 4  # 'E'
    assert t.tax_flag.char_counts["E"] == 4
    assert t.tax_flag.char_counts["A"] == 2

    # no Amazon-style features
    assert not t.markdown_marker.present
    assert not t.sub_line.present
    assert not t.name_truncation.present

    # name column starts well right of the margin (after the item number)
    assert t.columns.name_start_x > 0.3
    assert abs(t.columns.price_right_x - 0.95) < 1e-6


# ---------------------------------------------------------------------------
# Lone / noisy margin letters must NOT be treated as a tax flag.
# ---------------------------------------------------------------------------


def test_lone_leading_letter_is_not_a_flag():
    words = [
        _w(1, 10, 1, "MILK", 0.35, 0.20, 0.80),
        _w(1, 10, 2, "3.49", 0.83, 0.12, 0.80),
        _w(1, 11, 1, "Q", 0.01, 0.03, 0.80),  # single stray margin letter
    ]
    labels = [_lab(1, 10, 1, "PRODUCT_NAME"), _lab(1, 10, 2, "LINE_TOTAL")]
    t = extract_item_line_template(
        {"merchant_name": "X", "receipt_words": words, "receipt_word_labels": labels}
    )
    # one occurrence < recurrence floor -> dropped
    assert not t.tax_flag.present


# ---------------------------------------------------------------------------
# Edge cases.
# ---------------------------------------------------------------------------


def test_empty_export():
    t = extract_item_line_template({})
    assert t.sample_count == 0
    assert t.receipt_count == 0
    assert t.confidence == 0.0
    assert not t.tax_flag.present
    assert t.columns.price_right_x == -1.0


def test_templatize_replaces_numbers_and_prices():
    out = _templatize("SALE 1@ $4.39, WAS: $5.49 each")
    assert "$4.39" not in out and "5.49" not in out
    assert "{price}" in out and "WAS" in out
