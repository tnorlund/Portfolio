"""Tests for cached synthetic receipt public rendering helpers."""

import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "render_synthetic_receipts.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "render_synthetic_receipts_for_test",
        MODULE_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _line_texts(receipt: dict) -> list[str]:
    return [
        " ".join(word["text"] for word in line["words"])
        for line in receipt["lines"]
    ]


def test_cached_line_render_keeps_one_sprouts_header_and_orders_sections():
    module = _load_module()

    receipt = module._cached_line_receipt_dict(
        {
            "lines": [
                {
                    "y": 983.5,
                    "text": "SPROUTS",
                    "labels": ["MERCHANT_NAME"],
                },
                {"y": 943.2, "text": "1012 WESTLAKE BLVD.", "labels": []},
                {"y": 607.4, "text": "1012 WESTLAKE BLVD.", "labels": []},
                {"y": 582.5, "text": "Store Hours MON-SUN 7AM-10PM", "labels": []},
                {"y": 855.2, "text": "US DEBIT Entry Method: Contactless", "labels": []},
                {"y": 500.0, "text": "DAIRY", "labels": []},
                {"y": 450.0, "text": "BALANCE DUE 10.78", "labels": []},
                {"y": 350.0, "text": "feedback!", "labels": []},
            ]
        }
    )

    texts = _line_texts(receipt)

    assert texts.count("1012 WESTLAKE BLVD.") == 1
    assert texts.index("SPROUTS") < texts.index("DAIRY")
    assert texts.index("Store Hours MON-SUN 7AM-10PM") < texts.index("DAIRY")
    assert texts.index("DAIRY") < texts.index("US DEBIT Entry Method: Contactless")
    assert texts.index("US DEBIT Entry Method: Contactless") < texts.index("feedback!")


def test_cached_token_render_does_not_classify_chips_as_payment():
    module = _load_module()

    example = {
        "tokens": [
            "SPROUTS",
            "1012",
            "WESTLAKE",
            "BLVD.",
            "PRODUCE",
            "ORGANIC",
            "CARROT",
            "CHIPS",
            "2.49",
            "US",
            "DEBIT",
            "Entry",
            "Method:",
            "Contactless",
            "CARD",
            "#:",
            "XXXXXXXXXXXX1454",
            "feedback!",
        ],
        "bboxes": [
            [420, 970, 580, 995],
            [80, 950, 140, 960],
            [150, 950, 260, 960],
            [270, 950, 330, 960],
            [70, 880, 180, 890],
            [70, 850, 160, 860],
            [170, 850, 260, 860],
            [270, 850, 360, 860],
            [820, 850, 900, 860],
            [70, 760, 110, 770],
            [120, 760, 200, 770],
            [210, 760, 280, 770],
            [290, 760, 380, 770],
            [390, 760, 510, 770],
            [70, 740, 140, 750],
            [150, 740, 180, 750],
            [190, 740, 360, 750],
            [70, 600, 180, 610],
        ],
        "ner_tags": [
            "B-MERCHANT_NAME",
            "O",
            "O",
            "O",
            "O",
            "B-PRODUCT_NAME",
            "I-PRODUCT_NAME",
            "I-PRODUCT_NAME",
            "B-LINE_TOTAL",
            "O",
            "O",
            "O",
            "O",
            "O",
            "O",
            "O",
            "O",
            "O",
        ],
    }

    texts = _line_texts(module._cached_token_receipt_dict(example))

    product_line = "ORGANIC CARROT CHIPS 2.49"
    payment_line = "US DEBIT Entry Method: Contactless"

    assert product_line in texts
    assert payment_line in texts
    assert texts.index(product_line) < texts.index(payment_line)
    assert texts.index(payment_line) < texts.index("feedback!")
