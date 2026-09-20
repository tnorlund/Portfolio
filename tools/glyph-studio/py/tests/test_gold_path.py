"""Closed gold/export path: no live 12-receipt profile or bitmap_thin.

These tests import only stdlib glyphstudio modules so the glyph-studio
suite stays resolvable without the renderer / receipt_embeddings stack.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from types import SimpleNamespace

import pytest
from glyphstudio.provenance import (
    PROVENANCE_KEYS,
    card_provenance,
    exporter_commit,
    write_manifest_provenance,
)
from glyphstudio.vendor_package import (
    _numeric_pin,
    apply_gold_pins,
    closed_font_height,
    gold_render_pins,
    numeric_pin,
    resolve_gold_inputs,
    slug_for_merchant,
    vendor_uses_measured_separators,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_STUDIO = os.path.abspath(os.path.join(_HERE, "..", ".."))


def _boom_profile(*_a, **_k):
    raise AssertionError("cached_font_profile must not run on gold")


def _boom_thin(*_a, **_k):
    raise AssertionError("resolve_bitmap_thin must not run on gold")


def _boom_corpus(*_a, **_k):
    raise AssertionError("corpus_font_inputs must not run on gold")


def _closed_rsr():
    return SimpleNamespace(
        cached_font_profile=_boom_profile,
        resolve_bitmap_thin=_boom_thin,
        corpus_font_inputs=_boom_corpus,
    )


def _make_profile(merchant, pins, canvas_height=None, canvas_width=None):
    return SimpleNamespace(
        merchant_name=merchant,
        receipt_count=0,
        pins=pins,
        canvas_height=canvas_height,
        canvas_width=canvas_width,
    )


def test_costco_vendor_opts_into_measured_separators():
    assert vendor_uses_measured_separators("Costco Wholesale") is True
    for name in (
        "Gelson's Westlake Village",
        "The Stand - American Classics Redefined",
        "Dollar Tree",
        "Sprouts Farmers Market",
    ):
        assert vendor_uses_measured_separators(name) is False


def test_gold_pins_read_font_json_and_vendor_json():
    costco = gold_render_pins("costco")
    assert costco["bitmap_thin"] == 0.0
    assert costco["ocr_cap_height_ratio"] == pytest.approx(0.72)
    assert costco["use_measured_separators"] is True

    sprouts = gold_render_pins("sprouts")
    assert sprouts["pitch_ratio"] == pytest.approx(0.545)
    assert sprouts["bitmap_thin"] is None
    assert sprouts["use_measured_separators"] is False


def test_closed_gold_inputs_do_not_call_live_profile_or_thin():
    typ = {"bitmap_font": {"regular": "/nope.npz"}, "condense": 0.93}
    prof, out = resolve_gold_inputs(
        "Costco Wholesale",
        typ,
        table="t",
        region="us-east-1",
        rsr=_closed_rsr(),
        make_profile=_make_profile,
    )
    assert out["bitmap_thin"] == 0.0
    assert out["ocr_cap_height_ratio"] == pytest.approx(0.72)
    assert prof.receipt_count == 0
    assert prof.merchant_name == "Costco Wholesale"
    assert prof.pins["pitch_ratio"] is None


def test_calibrate_from_corpus_uses_the_shared_corpus_helper():
    seen = {}

    def corpus_font_inputs(
        table, merchant, *, region, typography, atlas, section_scale
    ):
        seen.update(
            table=table,
            merchant=merchant,
            region=region,
            atlas=atlas,
            section_scale=section_scale,
        )
        return "PROF", dict(typography, bitmap_thin=0.31)

    rsr = SimpleNamespace(
        cached_font_profile=_boom_profile,
        resolve_bitmap_thin=_boom_thin,
        corpus_font_inputs=corpus_font_inputs,
    )
    typ = {"bitmap_font": {"regular": "x.npz"}, "condense": 1.0}
    prof, out = resolve_gold_inputs(
        "X",
        typ,
        table="t",
        region="r",
        rsr=rsr,
        make_profile=_make_profile,
        calibrate_from_corpus=True,
        atlas="ATLAS",
        section_scale={"HEADER": 0.8},
    )
    assert prof == "PROF"
    assert out["bitmap_thin"] == 0.31
    assert out["condense"] == 1.0
    assert seen == {
        "table": "t",
        "merchant": "X",
        "region": "r",
        "atlas": "ATLAS",
        "section_scale": {"HEADER": 0.8},
    }


def test_apply_gold_pins_fills_missing_pitch_ratio_only():
    # truth-bundle typography with no pitch_ratio (clamp off): font.json's
    # pitchRatioTarget fills it in
    filled = apply_gold_pins({"condense": 0.9}, {"pitch_ratio": 0.545})
    assert filled["pitch_ratio"] == pytest.approx(0.545)
    explicit_none = apply_gold_pins(
        {"pitch_ratio": None}, {"pitch_ratio": 0.545}
    )
    assert explicit_none["pitch_ratio"] == pytest.approx(0.545)
    # a recorded pitch_ratio is kept unless the vendor opts in
    kept = apply_gold_pins(
        {"pitch_ratio": 0.647},
        {"pitch_ratio": 0.7426, "pin_pitch_ratio": False},
    )
    assert kept["pitch_ratio"] == pytest.approx(0.647)
    kept_default = apply_gold_pins(
        {"pitch_ratio": 0.647}, {"pitch_ratio": 0.7}
    )
    assert kept_default["pitch_ratio"] == pytest.approx(0.647)
    # vendor.json pin_pitch_ratio: true replaces the recorded value
    pinned = apply_gold_pins(
        {"pitch_ratio": 0.647},
        {"pitch_ratio": 0.7426, "pin_pitch_ratio": True},
    )
    assert pinned["pitch_ratio"] == pytest.approx(0.7426)
    # opt-in without a font.json pitch changes nothing
    untouched = apply_gold_pins(
        {"pitch_ratio": 0.647}, {"pitch_ratio": None, "pin_pitch_ratio": True}
    )
    assert untouched["pitch_ratio"] == pytest.approx(0.647)


def test_recorded_vendors_opt_into_the_font_json_pitch():
    assert gold_render_pins("costco")["pin_pitch_ratio"] is True
    assert gold_render_pins("wholefoods")["pin_pitch_ratio"] is True
    assert gold_render_pins("thestand")["pin_pitch_ratio"] is False
    assert gold_render_pins("sprouts")["pin_pitch_ratio"] is False
    assert gold_render_pins(None)["pin_pitch_ratio"] is False


def test_apply_gold_pins_does_not_wipe_recorded_thin_when_unpinned():
    typ = apply_gold_pins({"bitmap_thin": 0.225, "condense": 0.895}, {})
    assert typ["bitmap_thin"] == 0.225
    missing = apply_gold_pins({"condense": 0.9}, {})
    assert missing["bitmap_thin"] == 0.0


def test_closed_profile_callback_receives_font_json_pitch_and_canvas():
    seen = {}

    def make_profile(merchant, pins, canvas_height=None, canvas_width=None):
        seen["merchant"] = merchant
        seen["pins"] = pins
        seen["canvas_height"] = canvas_height
        seen["canvas_width"] = canvas_width
        return SimpleNamespace(merchant_name=merchant, receipt_count=0)

    resolve_gold_inputs(
        "Sprouts Farmers Market",
        {"condense": 0.93},
        table="t",
        region="r",
        rsr=_closed_rsr(),
        make_profile=make_profile,
        canvas_height=2497,
        canvas_width=760,
    )
    assert seen["merchant"] == "Sprouts Farmers Market"
    assert seen["pins"]["pitch_ratio"] == pytest.approx(0.545)
    assert seen["pins"]["cap_px"] == pytest.approx(22.0)
    assert seen["canvas_height"] == 2497
    assert seen["canvas_width"] == 760


def test_closed_font_height_inverts_grid_and_ocr_cap_metrics():
    # 22px cap at ratio 0.72 on a 760x2497 canvas (margin 10): word height
    # 30.56px over inner_h 2477 -> font_px 31, not the 45px the 0.018
    # fallback produced.
    font_height = closed_font_height(22.0, 0.72, 2497)
    assert font_height == pytest.approx((22.0 / 0.72) / 2477.0)
    assert round(font_height * 2477.0) == 31
    # ratio falls back to the renderer default and is clamped like the
    # renderer clamps ocr_cap_height_ratio
    assert closed_font_height(22.0, None, 2497) == pytest.approx(
        closed_font_height(22.0, 0.72, 2497)
    )
    assert closed_font_height(22.0, 0.3, 2497) == pytest.approx(
        closed_font_height(22.0, 0.65, 2497)
    )
    # the cap is recorded on the 760-wide canvas: another width scales it
    # so glyphs keep their proportion to the paper (same font_height when
    # both dims scale together, double the px on a doubled canvas)
    base = closed_font_height(22.0, 0.72, 2999, canvas_width=760)
    assert base == pytest.approx(closed_font_height(22.0, 0.72, 2999))
    doubled = closed_font_height(22.0, 0.72, 5998, canvas_width=1520)
    assert doubled * (5998 - 20) == pytest.approx(2 * base * (2999 - 20))
    halved = closed_font_height(22.0, 0.72, 1500, canvas_width=380)
    assert halved * (1500 - 20) == pytest.approx(
        0.5 * base * (2999 - 20), rel=1e-2
    )
    # no recorded cap / unknown canvas -> caller falls back
    assert closed_font_height(None, 0.72, 2497) is None
    assert closed_font_height(22.0, 0.72, None) is None
    assert closed_font_height(0.0, 0.72, 2497) is None


def test_preview_thin_pin_accepts_numeric_strings_and_ignores_auto():
    assert _numeric_pin("auto") is None
    assert _numeric_pin("AUTO ") is None
    assert _numeric_pin("") is None
    assert _numeric_pin(None) is None
    assert _numeric_pin(True) is None
    assert _numeric_pin("0.125") == pytest.approx(0.125)
    assert _numeric_pin(0.2) == pytest.approx(0.2)
    assert _numeric_pin(1) == pytest.approx(1.0)
    assert _numeric_pin("thin") is None


def test_committed_font_json_thin_is_auto_so_only_vendor_json_pins_thin():
    fonts_dir = os.path.join(_STUDIO, "fonts")
    for slug in sorted(os.listdir(fonts_dir)):
        path = os.path.join(fonts_dir, slug, "font.json")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            preview = json.load(fh).get("preview") or {}
        pins = gold_render_pins(slug)
        vendor_path = os.path.join(fonts_dir, slug, "vendor.json")
        vendor_thin = None
        if os.path.isfile(vendor_path):
            with open(vendor_path, encoding="utf-8") as fh:
                vendor_thin = json.load(fh).get("bitmap_thin")
        expected = _numeric_pin(preview.get("thin"))
        if vendor_thin is not None:
            expected = float(vendor_thin)
        assert pins["bitmap_thin"] == expected, slug


def test_slug_for_merchant_resolves_vendor_card_and_font_package():
    # vendor.json (merchant / alias, casefold)
    assert slug_for_merchant("Costco Wholesale") == "costco"
    assert slug_for_merchant("costco") == "costco"
    # pipeline card, casefolded
    assert slug_for_merchant("Sprouts Farmers Market") == "sprouts"
    assert slug_for_merchant("SPROUTS FARMERS MARKET") == "sprouts"
    assert slug_for_merchant("Trader Joe's") == "traderjoes"
    # committed font.json but no vendor.json / card: the truth-bundle faces
    # name the package
    stand = {"bitmap_font": {"regular": "/cache/thestand.glyphs.npz"}}
    assert (
        slug_for_merchant("The Stand - American Classics Redefined", stand)
        == "thestand"
    )
    depot = {
        "bitmap_font": {
            "regular": "/cache/homedepot.glyphs.npz",
            "heavy": "/cache/homedepot-heavy.glyphs.npz",
        }
    }
    assert slug_for_merchant("The Home Depot", depot) == "homedepot"
    heavy_only = {"bitmap_font": {"heavy": "/c/gelsons-heavy.glyphs.npz"}}
    assert slug_for_merchant("Gelson's Westlake Village", heavy_only) == (
        "gelsons"
    )
    # unknown faces / merchants stay None
    assert slug_for_merchant("Nowhere Mart") is None
    unknown = {"bitmap_font": {"regular": "/cache/bitMatrix-C2.glyphs.npz"}}
    assert slug_for_merchant("Nowhere Mart", unknown) is None
    assert slug_for_merchant(None) is None


def test_closed_inputs_reach_font_only_merchants():
    seen = {}

    def make_profile(merchant, pins, canvas_height=None, **_kw):
        seen["pins"] = pins
        return SimpleNamespace(merchant_name=merchant, receipt_count=0)

    typ = {"bitmap_font": {"regular": "/cache/thestand.glyphs.npz"}}
    _, out = resolve_gold_inputs(
        "The Stand - American Classics Redefined",
        typ,
        table="t",
        region="r",
        rsr=_closed_rsr(),
        make_profile=make_profile,
        canvas_height=2999,
    )
    assert seen["pins"]["slug"] == "thestand"
    assert seen["pins"]["cap_px"] == pytest.approx(22.0)
    # no pitch_ratio recorded -> font.json target fills it
    assert out["pitch_ratio"] == pytest.approx(0.7426)
    # a recorded pitch_ratio is kept (thestand has no vendor.json opt-in)
    _, kept = resolve_gold_inputs(
        "The Stand - American Classics Redefined",
        dict(typ, pitch_ratio=0.647),
        table="t",
        region="r",
        rsr=_closed_rsr(),
        make_profile=make_profile,
        canvas_height=2999,
    )
    assert kept["pitch_ratio"] == pytest.approx(0.647)


def test_font_json_auto_thin_is_not_a_pin():
    assert numeric_pin("auto") is None
    assert numeric_pin(0.31) == pytest.approx(0.31)
    assert numeric_pin(0) == 0.0
    assert numeric_pin(True) is None
    sprouts = gold_render_pins("sprouts")
    assert sprouts["bitmap_thin"] is None
    assert sprouts["cap_px"] == pytest.approx(22)


def test_vendor_json_pitch_ratio_is_an_opt_in_pin(tmp_path, monkeypatch):
    from glyphstudio import vendor_package as vp

    fonts = tmp_path / "fonts"
    (fonts / "pinned").mkdir(parents=True)
    (fonts / "pinned" / "font.json").write_text(
        json.dumps({"metrics": {"pitchRatioTarget": 0.5}, "preview": {}}),
        encoding="utf-8",
    )
    (fonts / "pinned" / "vendor.json").write_text(
        json.dumps({"merchant": "Pinned", "pitch_ratio": "0.61"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(vp, "FONTS_DIR", str(fonts))
    pins = vp.gold_render_pins("pinned")
    assert pins["pitch_ratio"] == pytest.approx(0.61)
    assert pins["pin_pitch_ratio"] is True
    assert apply_gold_pins({"pitch_ratio": 0.4}, pins)["pitch_ratio"] == (
        pytest.approx(0.61)
    )


def test_card_provenance_hashes_rendered_faces_not_skipped_logo(tmp_path):
    regular = tmp_path / "bitMatrix-C2.glyphs.npz"
    heavy = tmp_path / "bitMatrix-C2-heavy.glyphs.npz"
    regular.write_bytes(b"regular-face")
    heavy.write_bytes(b"heavy-face")
    skipped_logo = tmp_path / "logo.png"
    skipped_logo.write_bytes(b"should-not-hash")
    webp = tmp_path / "final.webp"
    webp.write_bytes(b"webp-bytes")
    block = card_provenance(
        payload={"words": [{"text": "A"}], "merchant": "X"},
        font_paths=[str(regular), str(heavy)],
        logo_path=str(skipped_logo),
        logo_used=False,
        final_webp_path=str(webp),
        image_type="SCAN",
        commit="deadbeef",
    )
    assert set(block) == set(PROVENANCE_KEYS)
    assert block["exporter_commit"] == "deadbeef"
    assert block["image_type"] == "SCAN"
    assert block["logo_sha256"] is None
    assert block["font_sha256"] is not None
    assert block["font_sha256"] != hashlib.sha256(b"{}").hexdigest()
    assert len(block["final_webp_sha256"]) == 64

    manifest = tmp_path / "pipeline_merchants.json"
    manifest.write_text(
        json.dumps(
            {
                "merchants": {
                    "sprouts": {
                        "merchant": "Sprouts Farmers Market",
                        "receipt": {"image_id": "x", "receipt_id": 1},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    write_manifest_provenance(str(manifest), "sprouts", block)
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert saved["merchants"]["sprouts"]["provenance"] == block


def test_exporter_commit_refuses_dirty_head(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo)]
    subprocess.check_call(
        [*git, "init"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    subprocess.check_call([*git, "config", "user.email", "t@example.com"])
    subprocess.check_call([*git, "config", "user.name", "t"])
    subprocess.check_call([*git, "config", "commit.gpgsign", "false"])
    (repo / "f").write_text("a", encoding="utf-8")
    subprocess.check_call([*git, "add", "f"])
    subprocess.check_call(
        [*git, "commit", "-m", "i"],
        stdout=subprocess.DEVNULL,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )
    clean = exporter_commit(str(repo))
    assert clean and "-dirty" not in clean
    (repo / "f").write_text("dirty", encoding="utf-8")
    with pytest.raises(RuntimeError, match="refusing dirty HEAD"):
        exporter_commit(str(repo))
    assert exporter_commit(str(repo), allow_dirty=True).endswith("-dirty")
