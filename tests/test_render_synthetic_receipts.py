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
            "merchant_name": "Sprouts Farmers Market",
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


def test_cached_line_render_deduplicates_combined_sprouts_brand_line():
    module = _load_module()

    receipt = module._cached_line_receipt_dict(
        {
            "merchant_name": "Sprouts Farmers Market",
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

    assert texts.count("SPROUTS FARMERS MARKET") == 1
    assert texts.count("1012 WESTLAKE BLVD.") == 1


def test_cached_line_render_keeps_split_totals_with_payment_section():
    module = _load_module()

    receipt = module._cached_line_receipt_dict(
        {
            "merchant_name": "Sprouts Farmers Market",
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


def test_cached_token_render_does_not_classify_chips_as_payment():
    module = _load_module()

    example = {
        "merchant_name": "Sprouts Farmers Market",
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


# --- Merchant profile registry (PR-1 generalization) -----------------------
# These lock the data-driven registry to the exact behavior of the former
# hardcoded dicts, so future generalization PRs can't silently drift the config
# layer. Asset-independent: they only assert values that don't depend on the
# local $BITMATRIX_DIR atlases/logos being present.

def test_registry_has_known_merchants():
    module = _load_module()
    profiles = module.load_merchant_profiles()
    assert set(profiles) == {
        "Costco Wholesale", "Amazon Fresh", "Target", "Vons",
        "Sprouts Farmers Market", "Smith's", "Gelson's Westlake Village",
    }


def test_section_scale_defaults_and_overrides():
    module = _load_module()
    assert module.section_scale_for_merchant("Costco Wholesale") == {}
    assert module.section_scale_for_merchant("Amazon Fresh") == {"HEADER": 0.78}
    # Unknown merchant -> measured default, not empty.
    assert module.section_scale_for_merchant("No Such Store") == {"HEADER": 0.80}


def test_font_token_resolves_to_bundled_path():
    module = _load_module()
    # Target uses VT323 (bundled/OFL, always present) and no logo.
    typo = module.merchant_typography("Target")
    assert typo["font_path"] == module._VT323
    assert typo["condense"] == 0.95 and typo["stroke"] == 0
    assert "bitmap_font" not in typo


def test_typography_never_leaks_comment_keys():
    module = _load_module()
    for merchant in module.load_merchant_profiles():
        assert not any(k.startswith("_") for k in module.merchant_typography(merchant))


def test_costco_profile_treatments_preserved():
    module = _load_module()
    typo = module.get_merchant_profile("Costco Wholesale")["typography"]
    assert typo["reverse_total"] is True
    assert typo["reverse_date_after_items"] is True
    assert typo["dashed_separators"] is True
    assert typo["condense"] == 0.93
    # display_headings phrases + order (first-match wins in the renderer).
    assert list(typo["display_headings"]) == [
        "SELF-CHECKOUT", "SELF CHECKOUT", "THANK YOU",
        "PLEASE COME AGAIN", "ITEMS SOLD:",
    ]


def test_unknown_merchant_typography_empty():
    module = _load_module()
    assert module.merchant_typography("No Such Store") == {}


def test_costco_anchors_come_from_profile():
    # PR-2: the phrase anchors that used to be hardcoded in receipt_renderer.py
    # now flow from the merchant profile through merchant_typography().
    module = _load_module()
    typo = module.merchant_typography("Costco Wholesale")
    assert typo["heading_bleed_phrase"] == "ITEMS SOLD:"
    assert typo["reverse_date_anchor"] == "NUMBER OF ITEMS SOLD"
    assert typo["dash_after_amount_date"] is True
    # A merchant with no anchors gets none (generic renderer defaults apply).
    assert "reverse_date_anchor" not in module.merchant_typography("Target")


def test_graphics_for_merchant_merges_profile_over_substring_default():
    # PR-3: substring default retained; a profile graphics block overrides it.
    module = _load_module()
    # Costco: no graphics block -> substring default (code128, qr, no HRI).
    gfx = module.graphics_for_merchant("Costco Wholesale")
    assert gfx["barcode_kind"] == "code128"
    assert gfx["qr"] is True
    # Substring rule still classifies the grocers as UPC-A.
    assert module.graphics_for_merchant("Vons")["barcode_kind"] == "upca"


def test_inbody_barcode_defaults_match_prior_constants():
    module = _load_module()
    d = module._INBODY_BARCODE_DEFAULTS
    assert d["symbology"] == "code128"
    assert d["max_count"] == 2
    assert d["min_gap_px"] == 34
    assert d["bar_h_px"] == 30


def test_header_profile_derives_brand_and_reflow_from_registry():
    # PR-5: reflow/dedup + brand token come from the merchant profile, not text.
    module = _load_module()
    sp = module._header_profile_for("Sprouts Farmers Market")
    assert sp["brand"] == "SPROUTS"
    assert sp["reflow"] is True and sp["dedup"] is True
    assert "PRODUCE" in sp["body_anchors"]
    assert "WESTLAKE" in sp["contains"]
    assert "FARMERSMARKET" in sp["exact"]
    # A merchant with no header block does not reflow (generic default).
    costco = module._header_profile_for("Costco Wholesale")
    assert costco["reflow"] is False and costco["dedup"] is False
    # "FARMERS MARKET SAVINGS!" is a footer promo, not a header line (exact match).
    assert module._is_header_line("FARMERSMARKETSAVINGS", sp) is False
    assert module._is_header_line("FARMERSMARKET", sp) is True
