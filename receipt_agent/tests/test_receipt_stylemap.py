from __future__ import annotations

from receipt_agent.agents.label_evaluator.rendering.receipt_stylemap import (
    classify_row,
    row_style,
)


def test_home_depot_rows_classify_to_measured_sections():
    cases = {
        "How doers": "store_header",
        "SALE CASHIER APRIL": "transaction",
        "MAX REFUND VALUE $17.24": "discount",
        "SUBTOTAL 195.86": "summary",
        "SALES TAX 14.15": "summary",
        "TOTAL $210.01": "total_line",
        "USD$ 210.01": "total_line",
        "Chip Read Verified By PIN": "payment",
        "PRO XTRA MEMBER STATEMENT": "footer",
    }

    for text, section in cases.items():
        assert classify_row(text) == section


def test_home_depot_stylemap_applies_total_scale():
    stylemap = {
        "sections": {
            "total_line": {
                "sizeScale": 0.93,
                "weight": "normal",
                "underline": False,
            }
        }
    }

    assert row_style(stylemap, "TOTAL $210.01") == {
        "scale": 0.93,
        "bold": False,
        "underline": False,
    }
