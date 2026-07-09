"""Tests for _build_receipt_window_examples — per-receipt sliding-window BIO assignment."""

import pytest

from receipt_layoutlm.data_loader import (
    WordInfo,
    _build_receipt_window_examples,
    _crop_to_line_item_band,
    _env_positive_int,
)


def _w(idx: int, label: str, orig: str | None = None) -> WordInfo:
    return WordInfo(
        word_id=idx,
        text=f"w{idx}",
        bbox=[idx * 10, idx * 10, idx * 10 + 5, idx * 10 + 5],
        label=label,
        original_label=orig if orig is not None else label,
        line_id=idx,
        image_id="img1",
        receipt_id=1,
    )


def test_empty_receipt_returns_no_examples():
    assert _build_receipt_window_examples([], "img1#00001") == []


def test_single_word_single_example():
    out = _build_receipt_window_examples([_w(0, "MERCHANT_NAME")], "k")
    assert len(out) == 1
    assert out[0].tokens == ["w0"]
    assert out[0].ner_tags == ["B-MERCHANT_NAME"]


def test_bio_continuation_for_same_original_label():
    words = [
        _w(0, "ADDRESS", "ADDRESS_LINE"),
        _w(1, "ADDRESS", "ADDRESS_LINE"),
        _w(2, "ADDRESS", "ADDRESS_LINE"),
    ]
    out = _build_receipt_window_examples(words, "k")
    assert out[0].ner_tags == ["B-ADDRESS", "I-ADDRESS", "I-ADDRESS"]


def test_bio_restarts_when_original_label_changes_even_if_merged_same():
    # SUBTOTAL and TAX both merge to AMOUNT but are distinct entities;
    # original_label difference must restart the BIO span.
    words = [
        _w(0, "AMOUNT", "SUBTOTAL"),
        _w(1, "AMOUNT", "TAX"),
    ]
    out = _build_receipt_window_examples(words, "k")
    assert out[0].ner_tags == ["B-AMOUNT", "B-AMOUNT"]


def test_o_breaks_entity_span():
    words = [
        _w(0, "ADDRESS", "ADDRESS_LINE"),
        _w(1, "O", "O"),
        _w(2, "ADDRESS", "ADDRESS_LINE"),
    ]
    out = _build_receipt_window_examples(words, "k")
    assert out[0].ner_tags == ["B-ADDRESS", "O", "B-ADDRESS"]


def test_sliding_window_splits_long_receipt():
    words = [_w(i, "O") for i in range(500)]
    out = _build_receipt_window_examples(
        words, "k", window_size=200, stride=150
    )
    # 500 words, stride 150: windows start at 0, 150, 300. window 3 (start=300,
    # end=500) consumes the tail and the loop exits via `end >= n`. So 3 windows.
    assert len(out) == 3
    assert [len(ex.tokens) for ex in out] == [200, 200, 200]
    # Verify coverage: union of window indices covers 0..499
    covered = set()
    starts = [0, 150, 300]
    for start, ex in zip(starts, out, strict=True):
        covered.update(range(start, start + len(ex.tokens)))
    assert covered == set(range(500))


def test_window_boundary_promotes_leading_i_to_b():
    # An entity spans words 195..210; with window_size=200 the second window
    # starts at 150 and word 200's tag is I-, but as the first token of a new
    # example it must be promoted to B- so seqeval treats it as the start.
    words = []
    for i in range(300):
        if 195 <= i <= 210:
            words.append(_w(i, "MERCHANT_NAME", "MERCHANT_NAME"))
        else:
            words.append(_w(i, "O"))
    out = _build_receipt_window_examples(
        words, "k", window_size=200, stride=150
    )
    # First window: indices 0..199 — word 195 is B-, words 196..199 are I-
    assert out[0].ner_tags[195] == "B-MERCHANT_NAME"
    assert out[0].ner_tags[196] == "I-MERCHANT_NAME"
    # Second window starts at 150 — word 200 is at offset 50.
    # In full_tags it was I-, but window-start promotion only applies to
    # offset 0, so word 200 stays I- here. The boundary promotion fires
    # only if the FIRST token of a window is I-.
    # Verify by checking with a window that starts mid-entity:
    words2 = [_w(i, "MERCHANT_NAME", "MERCHANT_NAME") for i in range(300)]
    out2 = _build_receipt_window_examples(
        words2, "k", window_size=200, stride=150
    )
    # Window 1 starts at offset 150 of an all-MERCHANT receipt.
    # full_tags[150] would have been I-MERCHANT_NAME; must be promoted to B-.
    assert out2[1].ner_tags[0] == "B-MERCHANT_NAME"


def test_receipt_smaller_than_window_yields_single_example():
    words = [_w(i, "O") for i in range(50)]
    out = _build_receipt_window_examples(
        words, "k", window_size=200, stride=150
    )
    assert len(out) == 1
    assert len(out[0].tokens) == 50


@pytest.mark.parametrize("bad_stride", [0, -1, -150])
def test_stride_must_be_positive(bad_stride):
    # Regression: stride <= 0 used to spin forever when the receipt exceeded
    # window_size. Now raises immediately.
    words = [_w(i, "O") for i in range(500)]
    with pytest.raises(ValueError, match="stride must be positive"):
        _build_receipt_window_examples(
            words, "k", window_size=200, stride=bad_stride
        )


@pytest.mark.parametrize("bad_window", [0, -1])
def test_window_size_must_be_positive(bad_window):
    words = [_w(0, "O")]
    with pytest.raises(ValueError, match="window_size must be positive"):
        _build_receipt_window_examples(
            words, "k", window_size=bad_window, stride=10
        )


def test_reading_order_sort_top_to_bottom_left_to_right():
    # Three words: y=20,x=5 / y=10,x=5 / y=10,x=15
    # Reading order should be: y=10/x=5, y=10/x=15, y=20/x=5
    w_top_left = WordInfo(
        word_id=0,
        text="a",
        bbox=[5, 10, 8, 12],
        label="O",
        original_label="O",
        line_id=0,
        image_id="img1",
        receipt_id=1,
    )
    w_top_right = WordInfo(
        word_id=1,
        text="b",
        bbox=[15, 10, 18, 12],
        label="O",
        original_label="O",
        line_id=0,
        image_id="img1",
        receipt_id=1,
    )
    w_bottom = WordInfo(
        word_id=2,
        text="c",
        bbox=[5, 20, 8, 22],
        label="O",
        original_label="O",
        line_id=1,
        image_id="img1",
        receipt_id=1,
    )
    out = _build_receipt_window_examples(
        [w_bottom, w_top_right, w_top_left], "k"
    )
    assert out[0].tokens == ["a", "b", "c"]


def test_crop_to_line_item_band_keeps_product_rows_between_anchors():
    words = [
        WordInfo(
            word_id=0,
            text="STORE",
            bbox=[0, 0, 50, 10],
            label="MERCHANT_NAME",
            original_label="MERCHANT_NAME",
            line_id=1,
            image_id="img1",
            receipt_id=1,
        ),
        WordInfo(
            word_id=1,
            text="555-1111",
            bbox=[0, 20, 50, 30],
            label="PHONE_NUMBER",
            original_label="PHONE_NUMBER",
            line_id=2,
            image_id="img1",
            receipt_id=1,
        ),
        WordInfo(
            word_id=2,
            text="MILK",
            bbox=[0, 40, 50, 50],
            label="PRODUCT_NAME",
            original_label="PRODUCT_NAME",
            line_id=3,
            image_id="img1",
            receipt_id=1,
        ),
        WordInfo(
            word_id=3,
            text="$4.29",
            bbox=[80, 40, 100, 50],
            label="LINE_TOTAL",
            original_label="LINE_TOTAL",
            line_id=3,
            image_id="img1",
            receipt_id=1,
        ),
        WordInfo(
            word_id=4,
            text="TOTAL",
            bbox=[0, 80, 50, 90],
            label="GRAND_TOTAL",
            original_label="GRAND_TOTAL",
            line_id=4,
            image_id="img1",
            receipt_id=1,
        ),
    ]
    label_map = {
        ("img1", 1, word.line_id, word.word_id): [word.original_label]
        for word in words
    }

    out = _crop_to_line_item_band(words, label_map, "img1", 1)

    assert [word.text for word in out] == ["MILK", "$4.29"]


@pytest.mark.parametrize("bad_value", ["not-an-int", "0", "-4"])
def test_env_positive_int_falls_back_for_bad_values(monkeypatch, bad_value):
    monkeypatch.setenv("LAYOUTLM_ITEM_WINDOW_SIZE", bad_value)

    assert _env_positive_int("LAYOUTLM_ITEM_WINDOW_SIZE", 200) == 200
