"""Unit tests for the M4 A/B driver's rules-path modeling.

The driver must score the RENDERER's actual rules path. For merchants
without a stylemap (Costco) that path is ``display_headings`` (substring ->
heavy + enlarged, plus the bleed date row) and the logo overlay (anchor rows
are never drawn as text) -- scoring them as all-body misstates production.
"""

from m4_face_pilot import _heading_rules, _heading_scale, _is_logo_anchor_line

_COSTCO_TYP = {
    "display_headings": {
        "SELF-CHECKOUT": 1.7,
        "THANK YOU": 1.4,
        "ITEMS SOLD:": 1.8,
    },
    "heading_bleed_phrase": "ITEMS SOLD:",
}


def test_heading_substring_matches_like_renderer():
    rules, bleed, bleed_scale = _heading_rules(_COSTCO_TYP)
    assert _heading_scale(rules, bleed, bleed_scale, "ITEMS SOLD: 18", "") == 1.8
    assert _heading_scale(rules, bleed, bleed_scale, "PLEASE THANK YOU!", "") == 1.4
    assert _heading_scale(rules, bleed, bleed_scale, "1 BANANAS 0.99", "") is None


def test_ocr_broken_heading_text_misses_the_rules():
    # 60a24649's real footer prints "Please Come Again" large; OCR read
    # "Please Come Asain", so the substring anchor fails -> rules render it
    # at body. (Measured selection still styles it -- the pilot's point.)
    typ = dict(_COSTCO_TYP, display_headings={"PLEASE COME AGAIN": 1.4})
    rules, bleed, bleed_scale = _heading_rules(typ)
    assert _heading_scale(rules, bleed, bleed_scale, "PLEASE COME ASAIN", "") is None


def test_bleed_row_inherits_items_sold_scale():
    rules, bleed, bleed_scale = _heading_rules(_COSTCO_TYP)
    assert (
        _heading_scale(rules, bleed, bleed_scale, "F3 04/23/2023", "ITEMS SOLD: 18")
        == 1.8
    )
    assert (
        _heading_scale(rules, bleed, bleed_scale, "F3 04/23/2023", "CHANGE 0.00")
        is None
    )


def test_tuple_headings_use_heading_scale():
    typ = {"display_headings": ("SELF CHECKOUT",), "heading_scale": 1.5}
    rules, bleed, bleed_scale = _heading_rules(typ)
    assert _heading_scale(rules, bleed, bleed_scale, "SELF CHECKOUT", "") == 1.5
    assert bleed is None and bleed_scale is None


def test_logo_anchor_row_detection_mirrors_renderer():
    anchors = ["EWHOLESALE", "WHOLESALE"]
    header = {"text": "-WHOLESALE", "bbox": [10, 40, 300, 90]}
    footer = {"text": "WHOLESALE", "bbox": [10, 900, 300, 950]}
    body = {"text": "1 BANANAS 0.99", "bbox": [10, 40, 300, 90]}
    assert _is_logo_anchor_line(header, anchors, 1000) is True
    # Renderer only accepts header-band lines (footer URLs must not drop).
    assert _is_logo_anchor_line(footer, anchors, 1000) is False
    assert _is_logo_anchor_line(body, anchors, 1000) is False
    assert _is_logo_anchor_line(header, [], 1000) is False
