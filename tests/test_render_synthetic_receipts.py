"""Tests for cached synthetic receipt public rendering helpers."""

import importlib.util
import json
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
    assert texts.index("DAIRY") < texts.index("US DEBIT Entry Method:Cntctless")
    assert texts.index("US DEBIT Entry Method:Cntctless") < texts.index("feedback!")


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


def test_cached_line_render_spreads_sparse_sprouts_receipts_down_canvas():
    module = _load_module()

    lines = [
        {"y": 983.5, "text": "SPROUTS", "labels": ["MERCHANT_NAME"]},
        {"y": 943.2, "text": "1012 WESTLAKE BLVD.", "labels": []},
        {"y": 927.1, "text": "Store Hours MON-SUN 7AM-10PM", "labels": []},
        {"y": 900.0, "text": "PRODUCE", "labels": []},
    ]
    lines.extend(
        {"y": 880.0 - index, "text": f"ITEM {index:02d} 1.00", "labels": []}
        for index in range(38)
    )
    lines.extend(
        [
            {"y": 200.0, "text": "BALANCE DUE 38.00", "labels": []},
            {"y": 180.0, "text": "CREDIT $38.00", "labels": []},
            {"y": 160.0, "text": "Cashier:SSCO Store: 2806", "labels": []},
        ]
    )

    receipt = module._cached_line_receipt_dict({"lines": lines})
    centers = [_line_center_y(line) for line in receipt["lines"]]

    assert len(centers) == 46
    assert min(centers) < 155.0


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
                {"y": 822.0, "text": "feedback!", "labels": []},
                {"y": 821.0, "text": "SproutsFeedback.com", "labels": []},
                {"y": 819.0, "text": "07/30/2024 19:35:35", "labels": []},
                {"y": 818.0, "text": "x5 Winners", "labels": []},
                {"y": 817.0, "text": "19022003126062", "labels": []},
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
    assert "SproutsFeedback.com" in texts
    assert "x5 Winners" in texts
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
    assert "feedback!" not in texts
    assert "07/30/2024 19:35:35" not in texts
    barcode_y = _line_center_y(_line_for_text(receipt, "19022003126062"))
    winners_y = _line_center_y(_line_for_text(receipt, "x5 Winners"))
    assert winners_y - barcode_y > module._CACHED_MAX_LINE_SPACING


def test_cached_output_size_uses_narrower_sprouts_public_canvas():
    module = _load_module()

    assert module._cached_output_size(
        {"candidate_id": "sprouts-arithmetic-1-add-line-item-abc"}
    ) == module._CACHED_ARITHMETIC_OUTPUT_SIZE
    assert module._cached_output_size(
        {"candidate_id": "sprouts-1-address-line-abc"}
    ) == module._CACHED_ADDRESS_OUTPUT_SIZE
    assert module._CACHED_ARITHMETIC_OUTPUT_SIZE[0] == 320
    assert module._CACHED_ADDRESS_OUTPUT_SIZE[0] < 560
    assert (
        module._CACHED_ARITHMETIC_OUTPUT_SIZE[0]
        / module._CACHED_ARITHMETIC_OUTPUT_SIZE[1]
        < 0.31
    )
    assert (
        module._CACHED_ADDRESS_OUTPUT_SIZE[0]
        / module._CACHED_ADDRESS_OUTPUT_SIZE[1]
        < 0.33
    )


def test_cached_token_render_keeps_rich_sprouts_remove_item_fixture():
    module = _load_module()
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "screenshots"
        / "synthetic_receipts"
        / "sprouts_arithmetic_remove_item.json"
    )

    fixture = json.loads(fixture_path.read_text())
    receipt = module._cached_receipt_dict(fixture)
    texts = _line_texts(receipt)

    assert receipt["candidate_id"] == fixture["candidate_id"]
    assert fixture["metadata"]["retained_line_item_count"] == 5
    assert (
        fixture["metadata"]["structure_similarity"]["candidate_signature"][
            "line_item_count"
        ]
        == 5
    )
    assert "BROCCOLI FLORETS 5.49" in texts
    assert "ORGANIC BANANAS 2.45" in texts
    assert "ORGANIC CARROT CHIPS 2.49" in texts
    assert "ORG WHOLE MILK 10.99" in texts
    assert "PLAIN WHL GRK YOGURT 5.99" in texts
    assert "ORGANIC GREEN ONIONS 1.67" not in texts
    assert "1 @ 3 FOR 5.00" not in texts
    assert not any("*" in text and not any(ch.isalnum() for ch in text) for text in texts)
    assert "1.67" not in texts
    assert "Voided Item" not in texts
    assert "-3.00" not in texts
    assert "SUBTOTAL 27.41" in texts
    assert "TAX 0.00" in texts
    assert "NO. OF ITEMS SOLD 5" in texts
    assert "US DEBIT Entry Method:Cntctless" in texts
    assert "US DEBIT Entry Method: Ontctless" not in texts
    assert "Total: USD$ 27.41" in texts
    assert "BALANCE DUE 27.41" in texts
    assert "DEBIT $27.41" in texts
    assert texts.index("NO. OF ITEMS SOLD 5") < texts.index("Total: USD$ 27.41")
    assert "DUE 27.41" not in texts
    assert "BALANCE" not in texts
    assert "CARD #: XXXXXXXXXXXX1454" in texts
    assert "AUTH CODE: 381723" in texts
    assert "ARC: 00" in texts
    assert "TC: 7E067478389F4545" in texts
    assert "MID: 910664" in texts
    assert "We need your feedback!" in texts
    assert "to WIN a $250 Sprouts gift card. Go to:" in texts
    assert "SproutsFeedback.com" in texts
    assert not any("please please" in text for text in texts)
    assert not any("rewards program" in text for text in texts)
    assert "09022003450923401235" in texts
    assert "Cashier:SSCO 34 Store: 220" in texts
    assert "POS:034 Transaction: 5092" in texts
    assert "Saturday, December 6, 2025 12:35 PM" in texts
    assert "the method of payment used." in texts
    assert "receipt. Limits apply to returns" in texts

    logo_word = _line_for_text(receipt, "SPROUTS")["words"][0]
    assert logo_word["bbox"][2] - logo_word["bbox"][0] == module._CACHED_LOGO_WIDTH
    assert min(_line_center_y(line) for line in receipt["lines"]) < 160.0

    barcode_y = _line_center_y(_line_for_text(receipt, "09022003450923401235"))
    winners_y = _line_center_y(_line_for_text(receipt, "*5 Winners Monthly*"))
    assert barcode_y - winners_y > module._CACHED_MAX_LINE_SPACING


def test_cached_token_render_normalizes_sprouts_feedback_amount_fixture():
    module = _load_module()
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "screenshots"
        / "synthetic_receipts"
        / "sprouts_arithmetic_add_item.json"
    )

    receipt = module._cached_receipt_dict(json.loads(fixture_path.read_text()))
    texts = _line_texts(receipt)

    assert "Saturday, December 6, 2025 12:35 PM" in texts
    assert "12/06/2025 12:36:35" not in texts
    assert "2/06/2025 12:36:35" not in texts
    assert "LIMES 1.50" in texts
    assert "1 @ 3 FOR 5.00" not in texts
    assert not any("*" in text and not any(ch.isalnum() for ch in text) for text in texts)
    assert "SUBTOTAL 30.58" in texts
    assert "TAX 0.00" in texts
    assert "NO. OF ITEMS SOLD 7" in texts
    assert "US DEBIT Entry Method:Cntctless" in texts
    assert "US DEBIT Entry Method: Ontctless" not in texts
    assert texts.index("NO. OF ITEMS SOLD 7") < texts.index("Total: USD$ 30.58")
    assert "to WIN a $250 Sprouts gift card. Go to:" in texts
    assert "Please keep your original receipt, the" in texts
    assert not any("$2b0" in text for text in texts)
    assert "Please keep your original receipt, th" not in texts


def test_cached_line_render_normalizes_sprouts_footer_word_splits_fixture():
    module = _load_module()
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "screenshots"
        / "synthetic_receipts"
        / "sprouts_synthetic_address_hard_negative.json"
    )

    receipt = module._cached_receipt_dict(json.loads(fixture_path.read_text()))
    texts = _line_texts(receipt)

    assert "Save money, save paper" in texts
    assert "Save money, save paper -" not in texts
    assert "US DEBIT Entry Method:Cntctless" in texts
    assert "US DEBIT Entry Method: Ontotless" not in texts
    assert texts.index("Store Hours MON-SUN 7AM-10PM") < texts.index(
        "LOCAL FAVORITES"
    )
    assert texts.index("LOCAL FAVORITES") < texts.index("DAIRY")
    assert "Please keep your original receipt, the" in texts
    assert not any("recei pt" in text for text in texts)
    assert "Please keep your original receipt, th" not in texts
    assert texts.index("POS: 034 Transaction: 0297") < texts.index(
        "Thursday, September 4, 2025 05:54 PM"
    )
    assert texts.index("Thursday, September 4, 2025 05:54 PM") < texts.index(
        "Save money, save paper"
    )
    loyalty_left, loyalty_right = _line_bounds(
        _line_for_text(receipt, "LOCAL FAVORITES")
    )
    assert 470 <= (loyalty_left + loyalty_right) / 2 <= 530


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


def test_cached_thermal_texture_is_deterministic_and_preserves_ink():
    from PIL import Image, ImageDraw

    module = _load_module()
    receipt = module._cached_line_receipt_dict(
        {
            "lines": [
                {"y": 900.0, "text": "SPROUTS", "labels": ["MERCHANT_NAME"]},
                {"y": 500.0, "text": "SproutsFeedback.com", "labels": ["WEBSITE"]},
            ]
        }
    )

    images = []
    for _ in range(2):
        image = Image.new("RGBA", (96, 96), (250, 249, 245, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle([16, 16, 32, 32], fill=(20, 20, 20, 255))
        module._apply_cached_thermal_texture(image, receipt)
        images.append(image)

    assert list(images[0].getdata()) == list(images[1].getdata())
    assert images[0].getpixel((20, 20)) == (20, 20, 20, 255)
    textured_dark = sum(
        1
        for x in range(96)
        for y in range(96)
        if not (16 <= x <= 32 and 16 <= y <= 32)
        and images[0].getpixel((x, y))[0] < 170
    )
    assert textured_dark > 300


def test_cached_thermal_ink_spread_fattens_ink_without_touching_distant_paper():
    from PIL import Image, ImageDraw

    module = _load_module()
    images = []
    for _ in range(2):
        image = Image.new("RGBA", (32, 32), (250, 249, 245, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle([12, 12, 14, 14], fill=(20, 20, 20, 255))
        module._apply_cached_thermal_ink_spread(
            image,
            module.random.Random(8842),
        )
        images.append(image)

    assert list(images[0].getdata()) == list(images[1].getdata())
    assert images[0].getpixel((13, 13)) == (20, 20, 20, 255)
    assert images[0].getpixel((0, 0)) == (250, 249, 245, 255)
    adjacent_dark = sum(
        1
        for x in range(10, 17)
        for y in range(10, 17)
        if images[0].getpixel((x, y))[0] < 120
    )
    assert adjacent_dark > 9


def test_cached_thermal_ink_contrast_darkens_medium_ink_only():
    from PIL import Image

    module = _load_module()
    image = Image.new("RGBA", (4, 1), (250, 249, 245, 255))
    image.putpixel((1, 0), (172, 170, 168, 255))
    image.putpixel((2, 0), (28, 28, 28, 255))

    module._apply_cached_thermal_ink_contrast(image)

    assert image.getpixel((0, 0)) == (250, 249, 245, 255)
    assert image.getpixel((1, 0))[0] < 120
    assert image.getpixel((2, 0))[0] <= 32


def test_cached_thermal_scanline_banding_adds_horizontal_rows():
    from PIL import Image, ImageDraw

    module = _load_module()
    image = Image.new("RGBA", (180, 180), (250, 249, 245, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([40, 40, 70, 70], fill=(20, 20, 20, 255))

    module._apply_cached_thermal_scanline_banding(
        image,
        module.random.Random(8721),
    )

    assert image.getpixel((50, 50)) == (20, 20, 20, 255)
    row_dark_counts = []
    for y in range(image.height):
        row_dark_counts.append(
            sum(
                1
                for x in range(image.width)
                if not (40 <= x <= 70 and 40 <= y <= 70)
                and image.getpixel((x, y))[0] < 170
            )
        )

    assert max(row_dark_counts) > 20
    assert sum(1 for count in row_dark_counts if count > 12) >= 2


def test_cached_thermal_density_floor_adds_paper_speckles_only():
    from PIL import Image, ImageDraw

    module = _load_module()
    image = Image.new("RGBA", (100, 100), (250, 249, 245, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([10, 10, 20, 20], fill=(20, 20, 20, 255))

    module._apply_cached_thermal_density_floor(
        image,
        module.random.Random(431),
        min_density=0.12,
    )

    assert image.getpixel((15, 15)) == (20, 20, 20, 255)
    dark_count = sum(
        1
        for value in image.convert("L").getdata()
        if value < 170
    )
    assert dark_count >= 1200


def test_cached_thermal_density_floor_uses_higher_remove_item_floor():
    module = _load_module()

    remove_receipt = {
        "candidate_id": "sprouts-arithmetic-2-remove-line-item-abc"
    }
    add_receipt = {
        "candidate_id": "sprouts-arithmetic-1-add-line-item-abc"
    }

    assert (
        module._cached_thermal_min_dark_density(remove_receipt)
        == module._CACHED_REMOVE_THERMAL_MIN_DARK_DENSITY
    )
    assert (
        module._cached_thermal_min_dark_density(add_receipt)
        == module._CACHED_THERMAL_MIN_DARK_DENSITY
    )


def test_cached_thermal_mottle_adds_low_frequency_paper_variation():
    from PIL import Image, ImageDraw

    module = _load_module()
    image = Image.new("RGBA", (160, 160), (250, 249, 245, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([64, 64, 96, 96], fill=(20, 20, 20, 255))

    module._apply_cached_thermal_mottle(
        image,
        module.random.Random(5521),
    )

    assert image.getpixel((80, 80)) == (20, 20, 20, 255)
    tile_means = []
    for top in range(0, image.height, 40):
        for left in range(0, image.width, 40):
            values = [
                image.getpixel((x, y))[0]
                for y in range(top, min(top + 40, image.height))
                for x in range(left, min(left + 40, image.width))
                if not (64 <= x <= 96 and 64 <= y <= 96)
            ]
            tile_means.append(sum(values) / max(1, len(values)))

    assert max(tile_means) - min(tile_means) > 2.0


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
    payment_line = "US DEBIT Entry Method:Cntctless"

    assert product_line in texts
    assert payment_line in texts
    assert texts.index(product_line) < texts.index(payment_line)
    assert texts.index(payment_line) < texts.index("feedback!")
