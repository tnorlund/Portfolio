from receipt_agent.agents.label_evaluator.rendering.receipt_stylemap import (
    classify_row,
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
    # body sections deliberately fall through (measured body-normal)
    assert classify_row("SNLK SSM OIL 4.99 F", "smiths") == "other"


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
