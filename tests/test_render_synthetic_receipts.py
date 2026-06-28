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


def _line_for_text(receipt: dict, text: str) -> dict:
    for line in receipt["lines"]:
        if " ".join(word["text"] for word in line["words"]) == text:
            return line
    raise AssertionError(f"line not found: {text}")


def _line_bounds(line: dict) -> tuple[float, float]:
    xs = [word["bbox"][0] for word in line["words"]]
    xs.extend(word["bbox"][2] for word in line["words"])
    return min(xs), max(xs)


def _line_center_y(line: dict) -> float:
    return sum(
        (word["bbox"][1] + word["bbox"][3]) / 2 for word in line["words"]
    ) / len(line["words"])


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


def test_cached_line_render_uses_tighter_sprouts_section_gaps():
    module = _load_module()

    receipt = module._cached_line_receipt_dict(
        {
            "lines": [
                {"y": 983.5, "text": "SPROUTS", "labels": ["MERCHANT_NAME"]},
                {"y": 943.2, "text": "1012 WESTLAKE BLVD.", "labels": []},
                {"y": 927.1, "text": "Store Hours MON-SUN 7AM-10PM", "labels": []},
                {"y": 900.0, "text": "PRODUCE", "labels": []},
                {"y": 880.0, "text": "ORGANIC GREEN ONIONS 1.67", "labels": []},
                {"y": 850.0, "text": "BALANCE DUE 1.67", "labels": []},
                {"y": 820.0, "text": "We need your feedback!", "labels": []},
            ]
        }
    )

    produce_y = _line_center_y(_line_for_text(receipt, "PRODUCE"))
    header_y = _line_center_y(_line_for_text(receipt, "Store Hours MON-SUN 7AM-10PM"))
    balance_y = _line_center_y(_line_for_text(receipt, "BALANCE DUE 1.67"))
    feedback_y = _line_center_y(_line_for_text(receipt, "We need your feedback!"))

    assert (
        header_y - produce_y
        == module._CACHED_MAX_LINE_SPACING + module._CACHED_SECTION_GAP
    )
    assert (
        produce_y - balance_y
        == module._CACHED_MAX_LINE_SPACING * 2 + module._CACHED_SECTION_GAP
    )
    assert (
        balance_y - feedback_y
        == module._CACHED_MAX_LINE_SPACING + module._CACHED_SECTION_GAP
    )


def test_cached_line_render_deduplicates_combined_sprouts_brand_line():
    module = _load_module()

    receipt = module._cached_line_receipt_dict(
        {
            "lines": [
                {"y": 983.5, "text": "SPROUTS FARMERS MARKET", "labels": ["MERCHANT_NAME"]},
                {"y": 943.2, "text": "1012 WESTLAKE BLVD.", "labels": []},
                {"y": 930.0, "text": "SPROUTS FARMERS MARKET", "labels": ["MERCHANT_NAME"]},
                {"y": 918.0, "text": "1012 WESTLAKE BLVD.", "labels": []},
                {"y": 800.0, "text": "PRODUCE", "labels": []},
                {"y": 780.0, "text": "ORGANIC GREEN ONIONS 1.67", "labels": []},
            ]
        }
    )

    texts = _line_texts(receipt)

    assert texts[:2] == ["SPROUTS", "FARMERS MARKET"]
    assert texts.count("1012 WESTLAKE BLVD.") == 1


def test_cached_line_render_centers_sprouts_store_header_block():
    module = _load_module()

    receipt = module._cached_line_receipt_dict(
        {
            "lines": [
                {"y": 983.5, "text": "SPROUTS", "labels": ["MERCHANT_NAME"]},
                {"y": 943.2, "text": "1012 WESTLAKE BLVD.", "labels": []},
                {"y": 927.1, "text": "WESTLAKE, CA 91361", "labels": []},
                {"y": 915.5, "text": "(805)917-4200", "labels": []},
                {"y": 881.0, "text": "Store Hours MON-SUN 7AM-10PM", "labels": []},
            ]
        }
    )

    texts = _line_texts(receipt)
    assert texts[:2] == ["SPROUTS", "FARMERS MARKET"]

    for text in (
        "FARMERS MARKET",
        "1012 WESTLAKE BLVD.",
        "WESTLAKE, CA 91361",
        "(805)917-4200",
        "Store Hours MON-SUN 7AM-10PM",
    ):
        left, right = _line_bounds(_line_for_text(receipt, text))
        assert abs(((left + right) / 2) - 500.0) < 0.001


def test_cached_line_render_uses_larger_sprouts_glyph_boxes():
    module = _load_module()

    receipt = module._cached_line_receipt_dict(
        {
            "lines": [
                {"y": 983.5, "text": "SPROUTS", "labels": ["MERCHANT_NAME"]},
                {"y": 940.0, "text": "PRODUCE", "labels": []},
            ]
        }
    )

    logo_word = _line_for_text(receipt, "SPROUTS")["words"][0]
    logo_y = _line_center_y(_line_for_text(receipt, "SPROUTS"))
    subtitle_y = _line_center_y(_line_for_text(receipt, "FARMERS MARKET"))
    subtitle_word = _line_for_text(receipt, "FARMERS MARKET")["words"][0]
    body_word = _line_for_text(receipt, "PRODUCE")["words"][0]

    assert logo_word["bbox"][2] - logo_word["bbox"][0] == module._CACHED_LOGO_WIDTH
    assert (
        logo_word["bbox"][3] - logo_word["bbox"][1]
        == module._CACHED_LOGO_HALF_HEIGHT * 2
    )
    assert (
        body_word["bbox"][3] - body_word["bbox"][1]
        == module._CACHED_BODY_HALF_HEIGHT * 2
    )
    assert (
        subtitle_word["bbox"][3] - subtitle_word["bbox"][1]
        == module._CACHED_BODY_HALF_HEIGHT * 2
    )
    assert logo_y - subtitle_y == module._CACHED_LOGO_SUBTITLE_GAP
    assert body_word["bbox"][2] - body_word["bbox"][0] == (
        len("PRODUCE") * module._CACHED_CHAR_WIDTH
    )


def test_cached_line_render_keeps_split_totals_with_payment_section():
    module = _load_module()

    receipt = module._cached_line_receipt_dict(
        {
            "lines": [
                {"y": 983.5, "text": "SPROUTS", "labels": ["MERCHANT_NAME"]},
                {"y": 940.0, "text": "PRODUCE", "labels": []},
                {"y": 920.0, "text": "ORGANIC GREEN ONIONS 1.67", "labels": []},
                {"y": 890.0, "text": "Total:", "labels": []},
                {"y": 880.0, "text": "USD$ 1.67", "labels": []},
                {"y": 850.0, "text": "feedback!", "labels": []},
            ]
        }
    )

    texts = _line_texts(receipt)

    assert texts.index("ORGANIC GREEN ONIONS 1.67") < texts.index("Total:")
    assert texts.index("USD$ 1.67") < texts.index("feedback!")


def test_cached_line_render_right_aligns_trailing_amount_cluster():
    module = _load_module()

    receipt = module._cached_line_receipt_dict(
        {
            "lines": [
                {"y": 983.5, "text": "SPROUTS", "labels": ["MERCHANT_NAME"]},
                {
                    "y": 920.0,
                    "text": "ORGANIC GREEN ONIONS 1.67",
                    "labels": ["B-PRODUCT_NAME", "B-LINE_TOTAL"],
                },
                {
                    "y": 890.0,
                    "text": "Total: USD$ 1.67",
                    "labels": ["B-GRAND_TOTAL"],
                },
            ]
        }
    )

    product_line = _line_for_text(receipt, "ORGANIC GREEN ONIONS 1.67")
    product_words = product_line["words"]
    assert product_words[0]["bbox"][0] == 70.0
    assert product_words[-1]["bbox"][2] == module._CACHED_PRICE_RIGHT_X
    assert "B-LINE_TOTAL" not in product_words[0]["labels"]
    assert "B-LINE_TOTAL" in product_words[-1]["labels"]

    total_words = _line_for_text(receipt, "Total: USD$ 1.67")["words"]
    assert total_words[0]["bbox"][0] == 70.0
    assert total_words[-2]["bbox"][0] > 675.0
    assert total_words[-1]["bbox"][2] == module._CACHED_PRICE_RIGHT_X


def test_cached_line_render_centers_long_barcode_numbers():
    module = _load_module()

    receipt = module._cached_line_receipt_dict(
        {
            "lines": [
                {"y": 983.5, "text": "SPROUTS", "labels": ["MERCHANT_NAME"]},
                {"y": 900.0, "text": "99022003402972471754", "labels": []},
            ]
        }
    )

    barcode_word = _line_for_text(receipt, "99022003402972471754")["words"][0]
    assert barcode_word["bbox"][0] == 220.0
    assert barcode_word["bbox"][2] == 780.0


def test_cached_line_render_drops_sprouts_barcode_footer_fragments():
    module = _load_module()

    receipt = module._cached_line_receipt_dict(
        {
            "lines": [
                {"y": 983.5, "text": "SPROUTS", "labels": ["MERCHANT_NAME"]},
                {"y": 940.0, "text": "PRODUCE", "labels": []},
                {"y": 920.0, "text": "GREEN BEANS 3.49", "labels": []},
                {"y": 890.0, "text": "19022003126062", "labels": []},
                {"y": 885.0, "text": "XXXXXXXXXXXX5061", "labels": []},
                {"y": 883.0, "text": "[ ] 62566Z 317081", "labels": []},
                {"y": 881.0, "text": "Auth# Ref#", "labels": []},
                {"y": 879.0, "text": "CHANGE 0.00", "labels": []},
                {"y": 878.0, "text": "******************** K***********", "labels": []},
                {"y": 880.0, "text": "to:", "labels": []},
                {"y": 870.0, "text": "31 220", "labels": []},
                {"y": 860.0, "text": "PM", "labels": []},
                {"y": 850.0, "text": "th", "labels": []},
                {"y": 840.0, "text": "used.", "labels": []},
                {"y": 830.0, "text": "62566Z —", "labels": []},
                {"y": 825.0, "text": "We need your chan", "labels": []},
                {"y": 823.0, "text": "Take a quick survey & enter for the", "labels": []},
                {"y": 820.0, "text": "Cashier:SSCO 31 Store: 220", "labels": []},
                {"y": 810.0, "text": "POS:031 Transaction:2806", "labels": []},
                {"y": 800.0, "text": "Tuesday, July 30, 2024 07:35 PM", "labels": []},
            ]
        }
    )

    texts = _line_texts(receipt)

    assert "19022003126062" in texts
    assert "XXXXXXXXXXXX5061" in texts
    assert "CHANGE 0.00" in texts
    assert "Cashier:SSCO 31 Store: 220" in texts
    assert "POS:031 Transaction:2806" in texts
    assert "Tuesday, July 30, 2024 07:35 PM" in texts
    assert "[ ] 62566Z 317081" not in texts
    assert "Auth# Ref#" not in texts
    assert "******************** K***********" not in texts
    assert "to:" not in texts
    assert "31 220" not in texts
    assert "PM" not in texts
    assert "th" not in texts
    assert "used." not in texts
    assert "62566Z —" not in texts
    assert "We need your chan" not in texts
    assert "Take a quick survey & enter for the" not in texts


def test_cached_hybrid_renderer_stamps_barcode_band():
    from PIL import Image

    module = _load_module()
    receipt = module._cached_line_receipt_dict(
        {
            "lines": [
                {"y": 900.0, "text": "99022003402972471754", "labels": []},
            ]
        }
    )
    image = Image.new("RGBA", (576, 1176), (250, 249, 245, 255))

    module._overlay_cached_barcodes(
        image,
        receipt,
        config=module.RenderConfig(width=576, height=1176, margin=10),
        coord_max=1000.0,
    )

    dark_pixels = sum(1 for value in image.convert("L").getdata() if value < 100)
    assert dark_pixels > 100


def test_cached_should_draw_qr_only_for_add_item_feedback_receipts():
    module = _load_module()

    assert module._cached_should_draw_qr(
        {
            "candidate_id": "sprouts-arithmetic-1-add-line-item-abc",
            "tokens": ["SproutsFeedback.com"],
        }
    )
    assert not module._cached_should_draw_qr(
        {
            "candidate_id": "sprouts-arithmetic-2-remove-line-item-abc",
            "tokens": ["SproutsFeedback.com"],
        }
    )
    assert not module._cached_should_draw_qr(
        {
            "candidate_id": "sprouts-arithmetic-1-add-line-item-abc",
            "tokens": ["feedback!"],
        }
    )


def test_cached_hybrid_renderer_stamps_qr_like_footer_block():
    from PIL import Image

    module = _load_module()
    receipt = module._cached_line_receipt_dict(
        {
            "lines": [
                {"y": 900.0, "text": "We need your feedback!", "labels": []},
                {"y": 880.0, "text": "SproutsFeedback.com", "labels": ["WEBSITE"]},
                {"y": 860.0, "text": "*5 Winners Monthly*", "labels": []},
            ]
        }
    )
    image = Image.new("RGBA", (576, 1176), (250, 249, 245, 255))

    module._overlay_cached_qr_code(
        image,
        receipt,
        config=module.RenderConfig(width=576, height=1176, margin=10),
        coord_max=1000.0,
    )

    dark_pixels = sum(1 for value in image.convert("L").getdata() if value < 100)
    assert dark_pixels > 2000


def test_cached_qr_block_uses_receipt_scaled_size_and_position():
    from PIL import Image

    module = _load_module()
    receipt = module._cached_line_receipt_dict(
        {
            "lines": [
                {"y": 900.0, "text": "SPROUTS", "labels": ["MERCHANT_NAME"]},
                {"y": 500.0, "text": "SproutsFeedback.com", "labels": ["WEBSITE"]},
            ]
        }
    )
    image = Image.new("RGBA", (576, 1176), (250, 249, 245, 255))

    left, top, right, bottom = module._cached_qr_pixel_box(
        image,
        receipt,
        config=module.RenderConfig(width=576, height=1176, margin=10),
        coord_max=1000.0,
    )

    expected_size = min(
        module._CACHED_QR_MAX_SIZE,
        max(module._CACHED_QR_MIN_SIZE, image.width * module._CACHED_QR_SIZE_FACTOR),
    )
    assert abs((right - left) - expected_size) < 0.001
    assert abs((bottom - top) - expected_size) < 0.001
    assert abs(top - image.height * module._CACHED_QR_TOP_FACTOR) < 0.001


def test_cached_qr_block_sits_below_winners_line_when_present():
    from PIL import Image

    module = _load_module()
    receipt = module._cached_line_receipt_dict(
        {
            "lines": [
                {"y": 900.0, "text": "SPROUTS", "labels": ["MERCHANT_NAME"]},
                {"y": 500.0, "text": "SproutsFeedback.com", "labels": ["WEBSITE"]},
                {"y": 480.0, "text": "*5 Winners Monthly*", "labels": []},
            ]
        }
    )
    image = Image.new("RGBA", (576, 1176), (250, 249, 245, 255))
    config = module.RenderConfig(width=576, height=1176, margin=10)

    _, top, _, _ = module._cached_qr_pixel_box(
        image,
        receipt,
        config=config,
        coord_max=1000.0,
    )
    _, _, _, winners_bottom = module._to_pixel_box(
        module._union_bbox(
            [
                word["bbox"]
                for word in _line_for_text(receipt, "*5 Winners Monthly*")["words"]
            ]
        ),
        coord_max=1000.0,
        margin=config.margin,
        inner_w=config.width - 2 * config.margin,
        inner_h=config.height - 2 * config.margin,
    )

    assert top >= winners_bottom + 12.0


def test_cached_qr_footer_reflows_cashier_lines_below_reserved_band():
    module = _load_module()

    receipt = module._cached_receipt_dict(
        {
            "candidate_id": "sprouts-arithmetic-1-add-line-item-abc",
            "lines": [
                {"y": 900.0, "text": "SPROUTS", "labels": ["MERCHANT_NAME"]},
                {"y": 880.0, "text": "We need your feedback!", "labels": []},
                {"y": 860.0, "text": "SproutsFeedback.com", "labels": ["WEBSITE"]},
                {"y": 840.0, "text": "*5 Winners Monthly*", "labels": []},
                {
                    "y": 820.0,
                    "text": "in our rewards program please please do t",
                    "labels": [],
                },
                {"y": 800.0, "text": "Cashier:SSCO 34 Store: 220", "labels": []},
                {"y": 780.0, "text": "POS:034 Transaction: 5092", "labels": []},
                {
                    "y": 760.0,
                    "text": "Please keep your original receipt, th",
                    "labels": [],
                },
            ],
        }
    )
    texts = _line_texts(receipt)

    assert "in our rewards program please please do t" not in texts
    cashier = _line_for_text(receipt, "Cashier:SSCO 34 Store: 220")["words"][0]
    pos = _line_for_text(receipt, "POS:034 Transaction: 5092")["words"][0]
    assert (
        (cashier["bbox"][1] + cashier["bbox"][3]) / 2
        == module._CACHED_QR_FOOTER_TAIL_START_Y
    )
    assert (pos["bbox"][1] + pos["bbox"][3]) / 2 < (
        module._CACHED_QR_FOOTER_TAIL_START_Y
    )


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
