"""Focused tests for context-gated render-time content repair."""

from receipt_agent.agents.label_evaluator.rendering.content_clean import (
    clean_for_render,
    fix_item_amount_ocr,
)


def _word(text, line_id, labels=()):
    y = 900 - line_id * 24
    return {
        "text": text,
        "line_id": line_id,
        "labels": list(labels),
        "bbox": [40, y, 160, y + 12],
    }


def test_item_amount_ocr_repair_requires_product_row():
    split_line_amount = _word("5Y9,99", 99)
    split_line_amount["bbox"] = _word("MAC", 1)["bbox"]
    words = [
        _word("MAC", 1, ["PRODUCT_NAME"]),
        _word("MINI", 1, ["PRODUCT_NAME"]),
        split_line_amount,
        _word("REFERENCE", 2),
        _word("5Y9,99", 2),
    ]

    assert fix_item_amount_ocr(words) == 1
    assert words[2]["text"] == "579.99"
    assert words[4]["text"] == "5Y9,99"


def test_clean_for_render_reports_item_amount_repairs():
    receipt = {
        "words": [
            _word("SANDISK", 7, ["PRODUCT_NAME"]),
            _word("2TB", 7),
            _word("2I9,99", 7),
        ]
    }

    report = clean_for_render(receipt)

    assert receipt["words"][-1]["text"] == "219.99"
    assert report["item_amounts_fixed"] == 1
