"""Unit tests for the (merchant,section)->(family,face) assembly."""
from glyphstudio.section_face_map import (
    Face, _aggregate_faces, families_to_merchant_family,
    build_section_face_map, load_merchant_faces,
)


def test_aggregate_median_scale_mode_weight():
    faces = [Face(1.0, "normal", False), Face(1.1, "normal", False),
             Face(1.4, "bold", True)]
    agg = _aggregate_faces(faces)
    assert agg.scale == 1.1               # median
    assert agg.weight == "normal"          # mode
    assert agg.underline is True           # any


def test_aggregate_tie_prefers_heaviest():
    faces = [Face(1.0, "normal", False), Face(1.0, "bold", False)]
    assert _aggregate_faces(faces).weight == "bold"   # tie -> heaviest


def test_families_to_merchant_family():
    mf = families_to_merchant_family([["cvs", "vons"], ["homedepot"]])
    assert mf["cvs"] == mf["vons"] == "cvs+vons"
    assert mf["homedepot"] == "homedepot"


def test_load_real_vons_faces():
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    vons = os.path.join(here, "..", "fonts", "vons")
    faces = load_merchant_faces(vons)
    # vons stylemap has section_header (bold) etc.; storefront present
    assert "storefront" in faces
    assert all(isinstance(f, Face) for f in faces.values())


def test_build_map_shape():
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fonts = os.path.join(here, "..", "fonts")
    dirs = {"vons": os.path.join(fonts, "vons"), "cvs": os.path.join(fonts, "cvs")}
    mf = families_to_merchant_family([["cvs", "vons"]])
    entries = build_section_face_map(dirs, mf)
    assert entries and all(e.family == "cvs+vons" for e in entries)
    assert {e.merchant for e in entries} == {"cvs", "vons"}
