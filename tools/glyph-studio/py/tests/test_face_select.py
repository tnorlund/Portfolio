"""Unit tests for the M4 measured-typography face selector."""

import os
import sys

import pytest
from glyphstudio.face_select import (measured_style_for_line,
                                     normalize_face_key, select_row_faces)
from glyphstudio.section_face_map import Face


def _line(text, cap=50.0, stroke=3.8, n=20, section="item", **kw):
    letters = kw.pop(
        "letters",
        ([{"ch": c, "h": cap} for c in text if not c.isspace()] if cap else []),
    )
    out = {
        "text": text,
        "cap_px": cap,
        "stroke_med": stroke,
        "n_letters": n,
        "section": section,
        "section_canonical": kw.pop("section_canonical", section),
        "underline": False,
        "reverse_video": 0,
        "tier": "normal",
        "letters": letters,
    }
    out.update(kw)
    return out


def _measurement(lines, body_cap=50.0, body_stroke=3.8, body_box_h=None):
    return {
        "body_cap_px": body_cap,
        "body_stroke_px": body_stroke,
        "body_box_h": body_box_h,
        "lines": lines,
    }


def test_body_line_is_regular_scale_one():
    faces, stats = select_row_faces(
        _measurement([_line("8304 1 5.00 5.00", cap=53.0, stroke=3.9)])
    )
    style = faces[normalize_face_key("8304 1 5.00 5.00")]
    assert style == {
        "face": "regular",
        "scale": 1.0,
        "underline": False,
        "reverse_video": False,
        "source": "measured",
    }
    assert stats["measured"] == 1


def test_enlarged_header_scales_but_stays_regular():
    # WF's "Thousand Oaks CA": cap_rel ~1.7 with strokes growing WITH the
    # caps -> same face enlarged, NOT the heavy face.
    faces, _ = select_row_faces(
        _measurement([_line("Thousand Oaks CA", cap=85.0, stroke=6.2)])
    )
    style = faces[normalize_face_key("Thousand Oaks CA")]
    assert style["face"] == "regular"
    assert style["scale"] == 1.7


def test_disproportionate_stroke_selects_heavy():
    # body-cap row printed with ~1.6x strokes = a genuinely bolder face
    faces, _ = select_row_faces(
        _measurement([_line("BALANCE DUE 12.34", cap=51.0, stroke=6.2)])
    )
    assert faces[normalize_face_key("BALANCE DUE 12.34")]["face"] == "heavy"


def test_mild_stroke_on_small_caps_is_not_heavy():
    # WF's phone line: stroke_rel 1.17 at cap_rel 0.88 inflates the
    # stroke/cap ratio past 1.3, but absolute stroke is body-like ->
    # regular (hand-checked real crop is plain body).
    faces, _ = select_row_faces(
        _measurement([_line("1-833-300-9453", cap=44.0, stroke=4.45)])
    )
    assert faces[normalize_face_key("1-833-300-9453")]["face"] == "regular"


def test_single_cap_sample_cannot_size_a_row():
    # WF's "Tender:": one cap sample ('T'), an OCR smear read as 1.34x
    # body -> scale stays 1.0 (hand-checked as plain body height).
    letters = [{"ch": c, "h": 50.0} for c in "Tender:"]
    faces, _ = select_row_faces(
        _measurement([_line("Tender:", cap=67.0, letters=letters)])
    )
    assert faces[normalize_face_key("Tender:")]["scale"] == 1.0


def test_underline_plus_reverse_is_scan_artifact():
    # A dark scan band trips both probes on a body-weight line -> both
    # cleared (hand-checked on ccc09736's payment block).
    faces, _ = select_row_faces(
        _measurement(
            [
                _line(
                    "Entry Method: EMV Contactless",
                    underline=True,
                    reverse_video=1,
                )
            ]
        )
    )
    style = faces[normalize_face_key("Entry Method: EMV Contactless")]
    assert style["underline"] is False
    assert style["reverse_video"] is False


def test_real_underline_without_reverse_is_kept():
    faces, _ = select_row_faces(_measurement([_line("SECTION HEADER", underline=True)]))
    assert faces[normalize_face_key("SECTION HEADER")]["underline"] is True


def test_scale_clamped_below_logo_sizes():
    faces, _ = select_row_faces(
        _measurement([_line("W F 4 5", cap=211.0, stroke=10.5, n=4)])
    )
    assert faces[normalize_face_key("W F 4 5")]["scale"] == 2.5


def test_missing_letters_falls_back_to_box_rung():
    # URL rows lose their letter records; the OCR box height still sizes
    # them (and correctly keeps a body-height row at 1.0 even when the
    # section prior says 1.7 -- the f008ea77 footer-URL regression).
    priors = {"storefront": Face(scale=1.7, weight="normal", underline_rate=0.0)}
    faces, stats = select_row_faces(
        _measurement(
            [
                _line(
                    "wildforkfoods.com/pages/careers",
                    cap=None,
                    n=31,
                    box_h=60.0,
                    section_canonical="storefront",
                )
            ],
            body_box_h=58.0,
        ),
        section_priors=priors,
    )
    style = faces[normalize_face_key("wildforkfoods.com/pages/careers")]
    assert style["source"] == "measured_box"
    assert style["scale"] == 1.0
    assert stats["measured_box"] == 1 and stats["prior"] == 0


def test_no_geometry_falls_back_to_section_prior():
    priors = {"storefront": Face(scale=1.7, weight="normal", underline_rate=0.0)}
    faces, stats = select_row_faces(
        _measurement([_line("WF*", cap=None, n=2, section_canonical="storefront")]),
        section_priors=priors,
    )
    style = faces[normalize_face_key("WF*")]
    assert style["source"] == "prior"
    assert style["scale"] == 1.7
    assert style["face"] == "regular"
    assert stats["prior"] == 1


def test_bold_prior_maps_to_heavy_and_underline_rate_threshold():
    priors = {"section_header": Face(scale=1.0, weight="bold", underline_rate=0.6)}
    faces, _ = select_row_faces(
        _measurement(
            [
                _line(
                    "PRODUCE",
                    cap=None,
                    n=0,
                    section_canonical="section_header",
                )
            ]
        ),
        section_priors=priors,
    )
    style = faces[normalize_face_key("PRODUCE")]
    assert style["face"] == "heavy"
    assert style["underline"] is True


def test_no_measurement_no_prior_is_skipped():
    faces, stats = select_row_faces(_measurement([_line("mystery row", cap=None, n=0)]))
    assert faces == {}
    assert stats["skipped"] == 1


def test_conflicting_duplicate_text_is_dropped():
    lines = [
        _line("MASTERCARD", cap=50.0, stroke=3.8),
        _line("MASTERCARD", cap=85.0, stroke=6.4),  # same text, large
    ]
    faces, stats = select_row_faces(_measurement(lines))
    assert normalize_face_key("MASTERCARD") not in faces
    assert stats["conflicts"] == 1


def test_agreeing_duplicate_text_is_kept():
    lines = [
        _line("MASTERCARD", cap=50.0, stroke=3.8),
        _line("MASTERCARD", cap=52.0, stroke=3.9),
    ]
    faces, _ = select_row_faces(_measurement(lines))
    assert faces[normalize_face_key("MASTERCARD")]["face"] == "regular"


def test_measured_style_for_line_thin_stats_return_none():
    assert measured_style_for_line(_line("ab", cap=50.0, n=2), 50.0, 3.8) is None
    assert measured_style_for_line(_line("abcdef", cap=None), 50.0, 3.8) is None


def test_normalize_key_parity_with_renderer():
    """The selector's key MUST equal the renderer's or every row misses."""
    root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
    )
    sys.path.insert(0, os.path.join(root, "receipt_agent"))
    try:
        from receipt_agent.agents.label_evaluator.rendering.receipt_stylemap import \
            normalize_face_key as renderer_key  # noqa: E501
    except ImportError:
        pytest.skip("receipt_agent not importable in this environment")
    for text in (
        "Thousand Oaks CA",
        "  double  spaced   text ",
        "lower case row",
        "x" * 80,
        "Total Tax 0. 00",
    ):
        assert normalize_face_key(text) == renderer_key(text)


def test_bimodal_cap_samples_cannot_size_a_row():
    # In-N-Out 9afeb902#2 "Questions/Comments: ... Call": true caps at
    # 46-47px with 83-84px digits leaked from a neighboring line; the
    # median (65px = 1.51x body) sized a row that does not exist on the
    # print (hand-checked as plain small text). Bimodal caps -> no sizing.
    letters = [
        {"ch": c, "h": h}
        for c, h in [
            ("C", 46),
            ("C", 46),
            ("C", 46),
            ("Q", 47),
            ("C", 47),
            ("Q", 47),
            ("C", 47),
            ("Q", 47),
            ("C", 47),
            ("0", 83),
            ("5", 83),
            ("0", 83),
            ("5", 83),
            ("0", 83),
            ("5", 83),
            ("3", 84),
            ("3", 84),
            ("3", 84),
        ]
    ]
    faces, _ = select_row_faces(
        _measurement(
            [
                _line(
                    "Questions/Comments: ad0a-53e24d Call",
                    cap=65.0,
                    stroke=3.02,
                    n=90,
                    letters=letters,
                )
            ],
            body_cap=43.0,
            body_stroke=3.0,
        )
    )
    key = normalize_face_key("Questions/Comments: ad0a-53e24d Call")
    assert faces[key]["scale"] == 1.0
    assert faces[key]["face"] == "regular"


def test_unimodal_cap_samples_still_size_an_enlarged_header():
    # A genuine enlarged header disperses tight (WF "Thousand Oaks CA"
    # letters all ~85px) and must keep its measured scale.
    letters = [{"ch": c, "h": 85} for c in "THOUSANDOAKSCA"]
    faces, _ = select_row_faces(
        _measurement([_line("Thousand Oaks CA", cap=85.0, stroke=6.2, letters=letters)])
    )
    assert faces[normalize_face_key("Thousand Oaks CA")]["scale"] == 1.7
