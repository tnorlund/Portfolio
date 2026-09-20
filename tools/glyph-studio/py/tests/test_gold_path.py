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
    CLOSED_PROFILE_MARGIN,
    apply_gold_pins,
    closed_profile_geometry,
    gold_render_pins,
    numeric_pin,
    resolve_gold_inputs,
    vendor_uses_measured_separators,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_STUDIO = os.path.abspath(os.path.join(_HERE, "..", ".."))


def _boom_profile(*_a, **_k):
    raise AssertionError("cached_font_profile must not run on gold")


def _boom_thin(*_a, **_k):
    raise AssertionError("resolve_bitmap_thin must not run on gold")


def _make_profile(merchant, pins):
    return SimpleNamespace(merchant_name=merchant, receipt_count=0, pins=pins)


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
    assert costco["cap_px"] == pytest.approx(22)
    assert costco["pin_pitch_ratio"] is False

    sprouts = gold_render_pins("sprouts")
    assert sprouts["pitch_ratio"] == pytest.approx(0.545)
    assert sprouts["bitmap_thin"] is None
    assert sprouts["use_measured_separators"] is False
    assert sprouts["cap_px"] == pytest.approx(22)
    assert sprouts["pin_pitch_ratio"] is False


def test_font_json_auto_thin_is_not_a_pin():
    assert numeric_pin("auto") is None
    assert numeric_pin(0.31) == pytest.approx(0.31)
    assert numeric_pin(0) == 0.0
    assert numeric_pin(True) is None
    sprouts = gold_render_pins("sprouts")
    assert sprouts["bitmap_thin"] is None


def test_closed_profile_geometry_uses_recorded_cap_px():
    pins = gold_render_pins("sprouts")
    pins["canvas_height"] = 2497
    font_height, _char_width = closed_profile_geometry(pins)
    inner_h = 2497 - 2 * CLOSED_PROFILE_MARGIN
    assert font_height == pytest.approx(22.0 / inner_h)
    assert round(font_height * inner_h) == 22
    assert font_height * inner_h * 1.35 == pytest.approx(29.7)
    fallback_h, _ = closed_profile_geometry({})
    assert fallback_h == pytest.approx(0.018)


def test_apply_gold_pins_does_not_overlay_font_json_pitch_ratio():
    stand = apply_gold_pins(
        {"pitch_ratio": 0.647, "condense": 0.9376},
        gold_render_pins("thestand"),
    )
    assert stand["pitch_ratio"] == pytest.approx(0.647)
    sprouts = apply_gold_pins(
        {"condense": 0.895, "bitmap_thin": 0.225},
        gold_render_pins("sprouts"),
    )
    assert "pitch_ratio" not in sprouts
    opted = apply_gold_pins(
        {"pitch_ratio": 0.647},
        {"pitch_ratio": 0.7426, "pin_pitch_ratio": True},
    )
    assert opted["pitch_ratio"] == pytest.approx(0.7426)


def test_closed_gold_inputs_do_not_call_live_profile_or_thin():
    rsr = SimpleNamespace(
        cached_font_profile=_boom_profile,
        resolve_bitmap_thin=_boom_thin,
    )
    typ = {"bitmap_font": {"regular": "/nope.npz"}, "condense": 0.93}
    prof, out = resolve_gold_inputs(
        "Costco Wholesale",
        typ,
        table="t",
        region="us-east-1",
        rsr=rsr,
        make_profile=_make_profile,
        canvas_height=1800,
    )
    assert out["bitmap_thin"] == 0.0
    assert out["ocr_cap_height_ratio"] == pytest.approx(0.72)
    assert "pitch_ratio" not in out
    assert prof.receipt_count == 0
    assert prof.merchant_name == "Costco Wholesale"
    assert prof.pins["pitch_ratio"] is None
    assert prof.pins["canvas_height"] == 1800
    assert prof.pins["cap_px"] == pytest.approx(22)


def test_calibrate_from_corpus_still_uses_live_profile():
    rsr = SimpleNamespace(
        cached_font_profile=lambda *_a, **_k: "PROF",
        resolve_bitmap_thin=lambda *_a, **_k: 0.31,
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
    )
    assert prof == "PROF"
    assert out["bitmap_thin"] == 0.31


def test_apply_gold_pins_does_not_wipe_recorded_thin_when_unpinned():
    typ = apply_gold_pins({"bitmap_thin": 0.225, "condense": 0.895}, {})
    assert typ["bitmap_thin"] == 0.225
    missing = apply_gold_pins({"condense": 0.9}, {})
    assert missing["bitmap_thin"] == 0.0


def test_closed_profile_callback_receives_font_json_pitch():
    seen = {}

    def make_profile(merchant, pins):
        seen["merchant"] = merchant
        seen["pins"] = pins
        return SimpleNamespace(merchant_name=merchant, receipt_count=0)

    _, out = resolve_gold_inputs(
        "Sprouts Farmers Market",
        {"condense": 0.93},
        table="t",
        region="r",
        rsr=SimpleNamespace(
            cached_font_profile=_boom_profile,
            resolve_bitmap_thin=_boom_thin,
        ),
        make_profile=make_profile,
        canvas_height=2497,
    )
    assert seen["merchant"] == "Sprouts Farmers Market"
    assert seen["pins"]["pitch_ratio"] == pytest.approx(0.545)
    assert seen["pins"]["cap_px"] == pytest.approx(22)
    assert seen["pins"]["canvas_height"] == 2497
    assert "pitch_ratio" not in out


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
    assert len(block["font_sha256"]) == 64
    assert block["font_sha256"] != hashlib.sha256(b"{}").hexdigest()

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
    assert os.path.isdir(os.path.join(_STUDIO, "fonts", "costco"))


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
