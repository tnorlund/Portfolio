"""Closed gold/export path: no live 12-receipt profile or bitmap_thin.

These tests import only stdlib glyphstudio modules so the glyph-studio
suite stays resolvable without the renderer / receipt_embeddings stack.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from glyphstudio.provenance import (
    PROVENANCE_KEYS,
    card_provenance,
    write_manifest_provenance,
)
from glyphstudio.vendor_package import (
    apply_gold_pins,
    gold_render_pins,
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

    sprouts = gold_render_pins("sprouts")
    assert sprouts["pitch_ratio"] == pytest.approx(0.545)
    assert sprouts["bitmap_thin"] is None
    assert sprouts["use_measured_separators"] is False


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
    )
    assert out["bitmap_thin"] == 0.0
    assert out["ocr_cap_height_ratio"] == pytest.approx(0.72)
    assert prof.receipt_count == 0
    assert prof.merchant_name == "Costco Wholesale"
    assert prof.pins["pitch_ratio"] is None


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

    resolve_gold_inputs(
        "Sprouts Farmers Market",
        {"condense": 0.93},
        table="t",
        region="r",
        rsr=SimpleNamespace(
            cached_font_profile=_boom_profile,
            resolve_bitmap_thin=_boom_thin,
        ),
        make_profile=make_profile,
    )
    assert seen["merchant"] == "Sprouts Farmers Market"
    assert seen["pins"]["pitch_ratio"] == pytest.approx(0.545)


def test_card_provenance_writes_required_keys(tmp_path):
    font = tmp_path / "font.json"
    font.write_text("{}", encoding="utf-8")
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"logo-bytes")
    webp = tmp_path / "final.webp"
    webp.write_bytes(b"webp-bytes")
    block = card_provenance(
        payload={"words": [{"text": "A"}], "merchant": "X"},
        font_json_path=str(font),
        logo_path=str(logo),
        final_webp_path=str(webp),
        image_type="SCAN",
        commit="deadbeef",
    )
    assert set(block) == set(PROVENANCE_KEYS)
    assert block["exporter_commit"] == "deadbeef"
    assert block["image_type"] == "SCAN"
    assert len(block["source_snapshot_sha256"]) == 64
    assert len(block["font_sha256"]) == 64
    assert len(block["logo_sha256"]) == 64
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
    assert os.path.isdir(os.path.join(_STUDIO, "fonts", "costco"))
