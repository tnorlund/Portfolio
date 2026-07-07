from receipt_agent.agents.label_evaluator.rendering.receipt_stylemap import (
    classify_row,
    row_style,
)


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
