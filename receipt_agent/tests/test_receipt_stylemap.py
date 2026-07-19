from receipt_agent.agents.label_evaluator.rendering.receipt_stylemap import (
    classify_row,
    measured_row_style,
    normalize_face_key,
    row_style,
)


def test_innout_row_classification_sections():
    assert (
        classify_row("IN-N-OUT WESTLAKE VILLAGE", "innout") == "store_header"
    )
    assert classify_row("Cashier: ORDERTAKER 1", "innout") == "transaction"
    assert classify_row("Amount Due $27.71", "innout") == "total_line"
    assert classify_row("AUTH AMT: $27.71", "innout") == "total_line"
    assert classify_row("CHARGE DETAIL", "innout") == "payment"
    assert classify_row("THANK YOU!", "innout") == "footer"


def test_innout_total_line_style_uses_bold_section():
    stylemap = {
        "source": {"merchant": "innout"},
        "sections": {
            "total_line": {
                "sizeScale": 1.0,
                "weight": "bold",
                "underline": False,
            }
        },
    }

    assert row_style(stylemap, "AUTH AMT: $27.71") == {
        "scale": 1.0,
        "bold": True,
        "underline": False,
    }


def test_target_department_headers_use_section_header_style():
    stylemap = {
        "sections": {
            "section_header": {
                "sizeScale": 0.95,
                "weight": "bold",
                "underline": False,
            },
            "item": {
                "sizeScale": 1.0,
                "weight": "normal",
                "underline": False,
            },
        }
    }

    assert classify_row("GROCERY") == "section_header"
    assert classify_row("LAUNDRY CLEANING AND CLOSET") == "section_header"
    assert row_style(stylemap, "LAUNDRY CLEANING AND CLOSET") == {
        "scale": 0.95,
        "bold": True,
        "underline": False,
    }


def test_smiths_row_classification_sections():
    # M6 cold-start pilot: only the sections whose measured face departs from
    # body are classified (storefront bold 1.27x, footer 1.10x).
    assert classify_row("Smith's", "smiths") == "store_header"
    assert classify_row("FRESH FOR EVERYONE.", "smiths") == "store_header"
    assert classify_row("EVERYONE-.", "smiths") == "store_header"
    assert classify_row("Fuel Points Earned Today: 10", "smiths") == "footer"
    assert classify_row("Your cashier was SANJA", "smiths") == "footer"
    assert classify_row("RECALL NOTICE", "smiths") == "footer"
    # body sections deliberately fall through (measured body-normal), and
    # the anchored footer rules must not sweep in item/coupon rows that
    # merely contain a footer word mid-line
    assert classify_row("SNLK SSM OIL 4.99 F", "smiths") == "other"
    assert classify_row("GRILL BRUSH UPC: STICKER 2.99", "smiths") == "other"
    assert classify_row("BIG THANK YOU CARD 3.49 F", "smiths") == "other"


def test_smiths_store_header_style_is_bold_scaled():
    stylemap = {
        "source": {"merchant": "smiths"},
        "sections": {
            "store_header": {
                "sizeScale": 1.265,
                "weight": "bold",
                "underline": False,
            }
        },
    }
    assert row_style(stylemap, "FRESH FOR EVERYONE.") == {
        "scale": 1.265,
        "bold": True,
        "underline": False,
    }


def test_normalize_face_key_collapses_case_space_and_truncates():
    assert normalize_face_key("  Thousand  Oaks CA ") == "THOUSAND OAKS CA"
    assert len(normalize_face_key("x" * 90)) == 60


def test_measured_row_style_hit_maps_face_to_bold():
    row_faces = {
        normalize_face_key("Thousand Oaks CA"): {
            "face": "regular",
            "scale": 1.7,
            "underline": False,
        },
        normalize_face_key("BALANCE DUE 12.34"): {
            "face": "heavy",
            "scale": 1.0,
            "underline": True,
        },
    }
    assert measured_row_style(row_faces, "THOUSAND OAKS CA") == {
        "scale": 1.7,
        "bold": False,
        "underline": False,
    }
    assert measured_row_style(row_faces, "BALANCE DUE 12.34") == {
        "scale": 1.0,
        "bold": True,
        "underline": True,
    }


def test_measured_row_style_miss_returns_none_for_stylemap_fallback():
    assert measured_row_style({}, "ANY ROW") is None


def test_measured_row_style_clamps_malformed_scale():
    for bad, expect in (
        (float("nan"), 1.0),
        (float("inf"), 1.0),
        (-1.0, 0.25),
        (99.0, 4.0),
        ("junk", 1.0),
    ):
        style = measured_row_style(
            {normalize_face_key("ROW"): {"face": "regular", "scale": bad}},
            "ROW",
        )
        assert style["scale"] == expect


def test_multiword_category_headers_classify_as_section_header():
    # Real prints qualify a department ("REG DELI", "SERVICE DELI") and style
    # the row exactly like any other category header.
    assert classify_row("REG DELI") == "section_header"
    assert classify_row("SERVICE DELI") == "section_header"
    assert classify_row("REG DELI:") == "section_header"
    assert classify_row("  reg   deli  ") == "section_header"


def test_qualified_header_never_fires_on_item_rows():
    # Only an allowlisted qualifier + department token is a header; product
    # descriptions ending in a department word must never pick up header
    # styling, with or without a price on the row.
    assert classify_row("TURKEY DELI SANDWICH") != "section_header"
    assert classify_row("HOT DELI ITEM 2.99") != "section_header"
    assert classify_row("DELI SLICED HAM 1LB") != "section_header"
    assert classify_row("THE VERY BEST REG DELI") != "section_header"
    assert classify_row("RED WINE") != "section_header"
    assert classify_row("GROUND MEAT") != "section_header"
    assert classify_row("ORGANIC BODY") != "section_header"
