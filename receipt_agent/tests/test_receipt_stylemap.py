from receipt_agent.agents.label_evaluator.rendering.receipt_stylemap import (
    classify_row,
    row_style,
)


def test_innout_row_classification_sections():
    assert (
        classify_row("IN-N-OUT WESTLAKE VILLAGE", "innout")
        == "store_header"
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
        }
    }

    assert row_style(stylemap, "AUTH AMT: $27.71") == {
        "scale": 1.0,
        "bold": True,
        "underline": False,
    }
