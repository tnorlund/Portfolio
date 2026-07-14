"""Unit tests for face-map v2's section join + measured-face aggregation."""

import pytest

from glyphstudio.face_map_v2 import (
    LineFace,
    aggregate_cell,
    aggregate_map,
    assign_sections,
    items_body_baseline,
    line_face,
    merchant_section_priors,
)

R = ("img-1", 1)


def _line(line_ids, cap=10.0, stroke=2.0, n_letters=8, caps=6, **kw):
    """A stylescan visual line with enough letter statistics to measure."""
    letters = [{"ch": "A"}] * caps + [{"ch": "a"}] * (n_letters - caps)
    d = {
        "line_ids": line_ids,
        "cap_px": cap,
        "stroke_med": stroke,
        "n_letters": n_letters,
        "letters": letters,
        "underline": False,
        "reverse_video": 0,
    }
    d.update(kw)
    return d


# --- join -------------------------------------------------------------------


def test_assign_sections_by_line_id():
    lines = [_line([1]), _line([2]), _line([9])]
    sections = [
        {"section_type": "ITEMS", "line_ids": [1]},
        {"section_type": "TOTAL_LINE", "line_ids": [2]},
    ]
    pairs, ambiguous = assign_sections(lines, sections)
    assert ambiguous == 0
    assert [sec for _, sec in pairs] == ["items", "total_line", None]


def test_assign_sections_drops_straddlers():
    # a visual line whose OCR lines are claimed by TWO section types must not
    # vote for either cell
    lines = [_line([1, 2])]
    sections = [
        {"section_type": "SUMMARY", "line_ids": [1]},
        {"section_type": "TOTAL_LINE", "line_ids": [2]},
    ]
    pairs, ambiguous = assign_sections(lines, sections)
    assert ambiguous == 1
    assert pairs == []


def test_assign_sections_multi_line_one_section_ok():
    # merging two OCR lines of the SAME section is not a straddle
    lines = [_line([1, 2])]
    sections = [{"section_type": "ITEMS", "line_ids": [1, 2, 3]}]
    pairs, ambiguous = assign_sections(lines, sections)
    assert ambiguous == 0
    assert pairs[0][1] == "items"


def test_assign_sections_ignores_non_canonical():
    lines = [_line([1])]
    sections = [{"section_type": "HEADER", "line_ids": [1]}]  # legacy type
    pairs, _ = assign_sections(lines, sections)
    assert pairs[0][1] is None


# --- body baseline ------------------------------------------------------------


def test_body_baseline_from_items():
    assigned = [
        (_line([1], cap=10.0, stroke=2.0), "items"),
        (_line([2], cap=12.0, stroke=2.2), "items"),
        (_line([3], cap=11.0, stroke=2.1), "items"),
        (_line([4], cap=30.0, stroke=6.0), "storefront"),  # must not skew body
    ]
    cap, stroke, src = items_body_baseline(assigned, 99.0, 9.0)
    assert src == "items"
    assert cap == 11.0
    assert stroke == 2.1


def test_body_baseline_falls_back_when_items_thin():
    assigned = [(_line([1], cap=10.0), "items")]  # < MIN_BODY_LINES
    cap, stroke, src = items_body_baseline(assigned, 9.5, 1.9)
    assert (cap, stroke, src) == (9.5, 1.9, "stylescan")


# --- per-line face -------------------------------------------------------------


def test_line_face_body_line_is_plain():
    lf = line_face(_line([1], cap=10.0, stroke=2.0), 10.0, 2.0, R)
    assert (lf.scale, lf.weight, lf.underline) == (1.0, "normal", False)


def test_line_face_bold_needs_disproportionate_stroke():
    # 1.5x stroke at body cap -> bold
    lf = line_face(_line([1], cap=10.0, stroke=3.0), 10.0, 2.0, R)
    assert lf.weight == "bold"
    # enlarged header whose strokes scale WITH caps (same face, bigger) ->
    # NOT bold (the hand-won Wild Fork header rule)
    lf = line_face(_line([1], cap=17.0, stroke=3.3), 10.0, 2.0, R)
    assert lf.weight == "normal"
    assert lf.scale == 1.7


def test_line_face_scale_needs_cap_samples():
    # a 1.5x cap read off <3 upper/digit samples is an OCR smear, not a size
    lf = line_face(_line([1], cap=15.0, caps=2, n_letters=8), 10.0, 2.0, R)
    assert lf.scale == 1.0


def test_line_face_scale_clamped():
    lf = line_face(_line([1], cap=50.0), 10.0, 2.0, R)
    assert lf.scale == 2.5


def test_line_face_thin_stats_yield_none():
    assert line_face(_line([1], n_letters=2, caps=2), 10.0, 2.0, R) is None
    assert line_face(_line([1], cap=None), 10.0, 2.0, R) is None
    assert line_face(_line([1]), None, None, R) is None


def test_line_face_underline_plus_reverse_is_scan_band():
    # both probes firing on a non-bold line = one dark band artifact
    lf = line_face(
        _line([1], underline=True, reverse_video=1), 10.0, 2.0, R
    )
    assert lf.underline is False and lf.reverse_video is False
    # on a BOLD line the pair is kept (Costco reverse-video TOTAL box)
    lf = line_face(
        _line([1], stroke=3.0, underline=True, reverse_video=1), 10.0, 2.0, R
    )
    assert lf.underline is True and lf.reverse_video is True


# --- aggregation ---------------------------------------------------------------


def _lf(scale=1.0, weight="normal", underline=False, reverse=False, receipt=R):
    return LineFace(scale, weight, underline, reverse, receipt)


def test_aggregate_cell_median_scale_mode_weight():
    agg = aggregate_cell(
        [
            _lf(1.0),
            _lf(1.5, "bold", underline=True, receipt=("img-2", 1)),
            _lf(2.0, receipt=("img-3", 1)),
        ]
    )
    assert agg["face"].scale == 1.5
    assert agg["face"].weight == "normal"  # mode over lines
    assert agg["face"].underline_rate == pytest.approx(1 / 3, abs=1e-4)
    assert agg["n_lines"] == 3
    assert agg["n_receipts"] == 3
    assert agg["low_confidence"] is False


def test_aggregate_cell_tie_prefers_lighter():
    agg = aggregate_cell([_lf(weight="bold"), _lf(weight="normal")])
    assert agg["face"].weight == "normal"


def test_aggregate_cell_low_confidence_below_three_lines():
    agg = aggregate_cell([_lf(), _lf()])
    assert agg["low_confidence"] is True
    assert agg["n_lines"] == 2


def test_aggregate_cell_empty_raises():
    with pytest.raises(ValueError):
        aggregate_cell([])


def test_aggregate_map_sorted_entries():
    cells = {
        ("vons", "items"): [_lf()],
        ("costco", "total_line"): [_lf(weight="bold")],
    }
    entries = aggregate_map(cells)
    assert [(e["merchant"], e["section"]) for e in entries] == [
        ("costco", "total_line"),
        ("vons", "items"),
    ]


# --- consumer adapter -----------------------------------------------------------


def test_merchant_section_priors_shape_and_filtering():
    map_obj = {
        "map": [
            {
                "merchant": "costco",
                "section": "total_line",
                "face": {"scale": 1.4, "weight": "bold", "underline_rate": 0.8},
                "low_confidence": False,
            },
            {
                "merchant": "costco",
                "section": "survey",
                "face": {"scale": 1.0, "weight": "normal", "underline_rate": 0.0},
                "low_confidence": True,
            },
            {
                "merchant": "vons",
                "section": "items",
                "face": {"scale": 1.0, "weight": "normal", "underline_rate": 0.0},
                "low_confidence": False,
            },
        ]
    }
    priors = merchant_section_priors(map_obj, "costco")
    assert set(priors) == {"total_line"}  # low-confidence excluded by default
    # Mapping-shaped prior with the face_select._style_from_prior keys
    assert priors["total_line"]["weight"] == "bold"
    assert priors["total_line"]["scale"] == 1.4
    assert priors["total_line"]["underline_rate"] == 0.8
    both = merchant_section_priors(map_obj, "costco", include_low_confidence=True)
    assert set(both) == {"total_line", "survey"}
