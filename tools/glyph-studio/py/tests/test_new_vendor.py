"""Offline contracts for new_vendor.py (no AWS)."""

from __future__ import annotations

import argparse
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import new_vendor as nv  # noqa: E402


def test_vendor_json_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(nv, "FONTS_DIR", str(tmp_path))
    v = {
        "merchant": "Test Mart",
        "slug": "testmart",
        "gold_receipt": {"image_id": "x", "receipt_id": 1},
    }
    os.makedirs(tmp_path / "testmart")
    with open(
        tmp_path / "testmart" / "vendor.json", "w", encoding="utf-8"
    ) as fh:
        json.dump(v, fh)
    loaded = nv.load_vendor("testmart")
    assert loaded["portfolio_slug"] == "testmart"
    assert loaded["hero"] == "T"
    assert loaded["label"] == "Test Mart"
    assert loaded["graphics"]["footer_codes"] is False
    assert loaded["studio_dir"].endswith("testmart_studio")
    assert nv._names(
        {"merchant": "Test Mart", "aliases": ["TEST MART", "Test Mart #1"]}
    ) == [
        "Test Mart",
        "Test Mart #1",
    ]


def test_load_vendor_missing_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setattr(nv, "FONTS_DIR", str(tmp_path))
    with pytest.raises(SystemExit, match="init nomart"):
        nv.load_vendor("nomart")


def test_register_env_mjs_is_idempotent(tmp_path, monkeypatch):
    env = tmp_path / "env.mjs"
    env.write_text(
        'export const SAMPLES = {\n  sprouts: "/tmp/a.npz",\n};\n\n'
        'export const FONT_MERCHANTS = {\n  sprouts: "Sprouts Farmers Market",\n};\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(nv, "ENV_MJS", str(env))
    nv._register_env_mjs("SAMPLES", "newmart", "/tmp/newmart.refined.npz")
    nv._register_env_mjs("SAMPLES", "newmart", "/tmp/newmart.refined.npz")
    nv._register_env_mjs("FONT_MERCHANTS", "newmart", "New Mart")
    text = env.read_text(encoding="utf-8")
    assert text.count('newmart: "/tmp/newmart.refined.npz"') == 1
    assert 'newmart: "New Mart"' in text
    # the existing entries and block terminators survive
    assert 'sprouts: "/tmp/a.npz"' in text and text.count("};") == 2


def test_committed_vendor_files_are_consistent(monkeypatch):
    """Every fonts/<slug>/vendor.json names its own dir and a gold receipt.

    Resolve the font tree from this file, not CWD. CI runs pytest from the
    repo root with rootdir ``tools/glyph-studio/py``; xdist workers may
    chdir. This PR does not commit a vendor.json, so the path check is a
    known font dir (speedway) rather than ``found >= 1``.
    """
    fonts_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "fonts")
    )
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
    )
    monkeypatch.setattr(nv, "FONTS_DIR", fonts_dir)
    monkeypatch.setattr(nv, "_ROOT", repo_root)
    assert os.path.isdir(fonts_dir), fonts_dir
    assert os.path.isdir(os.path.join(fonts_dir, "speedway"))

    for slug in os.listdir(fonts_dir):
        path = os.path.join(fonts_dir, slug, "vendor.json")
        if not os.path.exists(path):
            continue
        v = nv.load_vendor(slug)
        assert v["slug"] == slug
        assert v["gold_receipt"]["image_id"]
        if v.get("logo"):
            assert os.path.exists(os.path.join(repo_root, v["logo"]))
        for donor in [v.get("donor"), *(v.get("donor_for") or {})]:
            if donor:
                assert os.path.isdir(os.path.join(fonts_dir, donor)), donor


def test_calibrate_solve_is_linear_and_clamped():
    ratio, h_ratio = 0.649, 0.587
    solved = max(
        nv.CAP_RATIO_CLAMP[0], min(nv.CAP_RATIO_CLAMP[1], ratio / h_ratio)
    )
    assert solved == pytest.approx(0.95)  # Speedway's first pass hit the clamp
    ratio, h_ratio = 0.95, 1.076
    solved = max(
        nv.CAP_RATIO_CLAMP[0], min(nv.CAP_RATIO_CLAMP[1], ratio / h_ratio)
    )
    assert 0.87 < solved < 0.89  # ...and the second landed at 0.88


# --- card_logo: a card-only wordmark for prints with no logo graphic ---


def _seed_vendor(tmp_path, monkeypatch, v):
    """Point new_vendor at a scratch fonts root holding one vendor.json."""
    monkeypatch.setattr(nv, "FONTS_DIR", str(tmp_path))
    os.makedirs(tmp_path / v["slug"], exist_ok=True)
    with open(
        tmp_path / v["slug"] / "vendor.json", "w", encoding="utf-8"
    ) as fh:
        json.dump(v, fh)


def test_init_card_logo_is_stored_repo_relative_and_separate_from_logo(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(nv, "FONTS_DIR", str(tmp_path / "fonts"))
    monkeypatch.setattr(nv, "_ROOT", str(tmp_path))
    os.makedirs(tmp_path / "fonts")
    mark = tmp_path / "synthesis_loop" / "logo_masters" / "testmart_logo.png"
    mark.parent.mkdir(parents=True)
    mark.write_bytes(b"png")
    rc = nv.main(
        [
            "init",
            "testmart",
            "--merchant",
            "Test Mart",
            "--gold",
            "img#1",
            "--card-logo",
            str(mark),
        ]
    )
    assert rc == 0
    v = nv.load_vendor("testmart")
    assert v["card_logo"] == "synthesis_loop/logo_masters/testmart_logo.png"
    assert "logo" not in v  # card-only: the renderer gets no logo band


@pytest.mark.parametrize(
    "vendor_fields, expected",
    [
        ({"card_logo": "lm/card.png"}, "lm/card.png"),
        ({"logo": "lm/print.png"}, "lm/print.png"),
        ({"logo": "lm/print.png", "card_logo": "lm/card.png"}, "lm/card.png"),
        ({}, None),
    ],
)
def test_export_passes_card_logo_over_logo_to_the_exporter(
    tmp_path, monkeypatch, vendor_fields, expected
):
    from glyphstudio import portfolio_wiring as pw

    v = {
        "merchant": "Test Mart",
        "slug": "testmart",
        "gold_receipt": {"image_id": "img", "receipt_id": 1},
        "studio_dir": str(tmp_path / "studio"),
        **vendor_fields,
    }
    _seed_vendor(tmp_path, monkeypatch, v)
    monkeypatch.setattr(nv, "_ROOT", "/repo")
    monkeypatch.setattr(nv, "PIPELINE_PUBLIC", str(tmp_path / "public"))
    monkeypatch.setattr(nv, "FINALE_FILES", ())
    monkeypatch.setattr(nv, "_truth_env", lambda v, t: {})
    monkeypatch.setattr(nv, "_clear_render_cache", lambda v: None)
    monkeypatch.setattr(pw, "set_entry", lambda *a, **k: None)
    monkeypatch.setattr(pw, "set_dims", lambda *a, **k: None)
    seen = {}

    def fake_run(cmd, env=None, capture=False):
        seen["cmd"] = cmd
        return "  testmart: { w: 760, h: 2308 }\n"

    monkeypatch.setattr(nv, "_run", fake_run)
    rc = nv.cmd_export(
        argparse.Namespace(slug="testmart", hero_assets=False, truth=None)
    )
    assert rc == 0
    cmd = seen["cmd"]
    if expected is None:
        assert "--logo" not in cmd
    else:
        assert cmd[cmd.index("--logo") + 1] == os.path.join("/repo", expected)


# --- profile: section_scale passthrough and --force ---


def _seed_profile_env(tmp_path, monkeypatch, v, profiles=None):
    _seed_vendor(tmp_path, monkeypatch, v)
    with open(tmp_path / v["slug"] / "font.json", "w", encoding="utf-8") as fh:
        json.dump(
            {
                "version": 1,
                "preview": {"condense": 0.9},
                "metrics": {"pitchRatioTarget": 0.6},
            },
            fh,
        )
    profiles_path = tmp_path / "merchant_profiles.json"
    profiles_path.write_text(
        json.dumps({"profiles": profiles or {}}), encoding="utf-8"
    )
    monkeypatch.setattr(nv, "PROFILES", str(profiles_path))
    env = tmp_path / "env.mjs"
    env.write_text(
        "export const SAMPLES = {\n};\n\nexport const FONT_MERCHANTS = {\n};\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nv, "ENV_MJS", str(env))
    return profiles_path


def _profile(profiles_path, merchant):
    with open(profiles_path, encoding="utf-8") as fh:
        return json.load(fh)["profiles"][merchant]


@pytest.mark.parametrize(
    "section_scale", [None, {"HEADER": 1.0}, {"HEADER": 1.0, "FOOTER": 0.9}]
)
def test_profile_section_scale_passes_through_only_when_set(
    tmp_path, monkeypatch, section_scale
):
    v = {
        "merchant": "Test Mart",
        "slug": "testmart",
        "gold_receipt": {"image_id": "img", "receipt_id": 1},
    }
    if section_scale is not None:
        v["section_scale"] = section_scale
    path = _seed_profile_env(tmp_path, monkeypatch, v)
    assert nv.cmd_profile(argparse.Namespace(slug="testmart")) == 0
    rec = _profile(path, "Test Mart")
    if section_scale is None:
        assert "section_scale" not in rec
    else:
        assert rec["section_scale"] == section_scale
    assert rec["typography"]["pitch_ratio"] == pytest.approx(0.6)
    assert rec["typography"]["condense"] == pytest.approx(0.9)
    assert "logo" not in rec  # no print logo -> no renderer logo band


def test_profile_refuses_foreign_profile_unless_forced(tmp_path, monkeypatch):
    v = {
        "merchant": "Test Mart",
        "slug": "testmart",
        "gold_receipt": {"image_id": "img", "receipt_id": 1},
    }
    existing = {
        "Test Mart": {
            "aliases": ["TEST MART"],
            "typography": {"font": "PTMONO", "bitmap_thin": 0.2},
        }
    }
    path = _seed_profile_env(tmp_path, monkeypatch, v, profiles=existing)
    with pytest.raises(SystemExit, match="--force"):
        nv.cmd_profile(argparse.Namespace(slug="testmart", force=False))
    assert (
        _profile(path, "Test Mart")["typography"]
        == existing["Test Mart"]["typography"]
    )
    assert nv.cmd_profile(argparse.Namespace(slug="testmart", force=True)) == 0
    rec = _profile(path, "Test Mart")
    assert rec["typography"]["bitmap_font"]["regular"] == "testmart.glyphs.npz"
    assert rec["typography"]["bitmap_thin"] == pytest.approx(0.2)  # kept
    assert nv.load_vendor("testmart")["owns_profile"] is True


def test_profile_force_flag_is_parsed(monkeypatch):
    seen = {}
    monkeypatch.setitem(nv.COMMANDS, "profile", lambda a: seen.update(a=a))
    nv.main(["profile", "testmart", "--force"])
    assert seen["a"].force is True
    nv.main(["profile", "testmart"])
    assert seen["a"].force is False


# --- calibrate: solve pitch_ratio for wpc; stop when a render is unmoved ---


def test_in_band_is_the_closed_h_band():
    lo, hi = nv.H_BAND
    assert nv._in_band(lo) and nv._in_band(hi)
    assert nv._in_band((lo + hi) / 2)
    assert not nv._in_band(lo - 1e-6) and not nv._in_band(hi + 1e-6)


def _calibrate_harness(tmp_path, monkeypatch, renders):
    """Drive cmd_calibrate against scripted glyph_review metrics.

    ``renders`` is the sequence of (h_ratio, wpc_ratio) the fake review
    returns per call; knob writes are recorded instead of touching the
    profiles file.
    """
    v = {
        "merchant": "Test Mart",
        "slug": "testmart",
        "gold_receipt": {"image_id": "img", "receipt_id": 1},
        "studio_dir": str(tmp_path / "studio"),
    }
    _seed_vendor(tmp_path, monkeypatch, v)
    profiles_path = tmp_path / "merchant_profiles.json"
    profiles_path.write_text(
        json.dumps(
            {
                "profiles": {
                    "Test Mart": {
                        "typography": {
                            "ocr_cap_height_ratio": 0.72,
                            "pitch_ratio": 0.55,
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(nv, "PROFILES", str(profiles_path))
    monkeypatch.setattr(
        nv, "_truth_env", lambda v, t: {"MERCHANT_TRUTH_MODE": "fixture"}
    )
    fixtures = []
    monkeypatch.setattr(nv, "cmd_fixture", lambda a: fixtures.append(a.slug))
    knobs = []
    monkeypatch.setattr(
        nv, "_set_profile_knob", lambda m, k, val: knobs.append((k, val))
    )
    queue = list(renders)
    tags = []

    def fake_review(v, tag, truth):
        tags.append(tag)
        h, wpc = queue.pop(0)
        return {
            "h_ratio": h,
            "wpc_ratio": wpc,
            "density_ratio": 1.0,
            "png": "x.png",
            "scorecard": "x.scorecard.md",
        }

    monkeypatch.setattr(nv, "_render_review", fake_review)
    return knobs, fixtures, tags


def test_calibrate_solves_pitch_ratio_for_wpc_under_ocr_font_sizing(
    tmp_path, monkeypatch, capsys
):
    # h_ratio in band; wpc 0.90 low -> pitch_ratio 0.55/0.90 = 0.611, then the
    # re-render lands in band and the loop stops with no further writes.
    knobs, fixtures, tags = _calibrate_harness(
        tmp_path, monkeypatch, [(1.0, 0.90), (1.0, 1.0)]
    )
    rc = nv.cmd_calibrate(
        argparse.Namespace(slug="testmart", iterations=3, truth="fixture")
    )
    assert rc == 0
    assert knobs == [("pitch_ratio", pytest.approx(0.611))]
    assert fixtures == ["testmart"]  # fixture regenerated after the knob move
    assert tags == ["cal0", "cal1"]
    out = capsys.readouterr().out
    assert "pitch_ratio 0.55 -> 0.611" in out
    assert "condense" not in out  # the inert knob is no longer suggested


def test_calibrate_stops_when_wpc_does_not_respond(
    tmp_path, monkeypatch, capsys
):
    # After the first pitch_ratio move the re-render barely changes (<0.005):
    # the knob is saturated, so calibrate must say so and NOT creep it again.
    knobs, _, tags = _calibrate_harness(
        tmp_path, monkeypatch, [(1.0, 0.94), (1.0, 0.941), (1.0, 0.99)]
    )
    rc = nv.cmd_calibrate(
        argparse.Namespace(slug="testmart", iterations=4, truth="fixture")
    )
    assert rc == 1  # wpc still off the floor
    assert [k for k, _ in knobs] == ["pitch_ratio"]
    assert tags == ["cal0", "cal1"]  # no third render after the stop
    out = capsys.readouterr().out
    assert "did not respond to pitch_ratio" in out
    assert f"Leaving pitch_ratio at {knobs[0][1]}" in out
    assert "wpc still off after 4 iterations" in out


def test_calibrate_solves_both_knobs_in_one_pass(tmp_path, monkeypatch):
    # h_ratio and wpc_ratio both off -> one iteration moves both knobs.
    knobs, _, _ = _calibrate_harness(
        tmp_path, monkeypatch, [(0.9, 1.10), (1.0, 1.0)]
    )
    rc = nv.cmd_calibrate(
        argparse.Namespace(slug="testmart", iterations=2, truth="fixture")
    )
    assert rc == 0
    assert dict(knobs) == {
        "ocr_cap_height_ratio": pytest.approx(0.8),  # 0.72 / 0.9
        "pitch_ratio": pytest.approx(0.5),  # 0.55 / 1.10
    }


def test_calibrate_in_band_from_the_start_writes_nothing(
    tmp_path, monkeypatch
):
    knobs, fixtures, tags = _calibrate_harness(
        tmp_path, monkeypatch, [(1.0, 1.0)]
    )
    rc = nv.cmd_calibrate(
        argparse.Namespace(slug="testmart", iterations=2, truth="fixture")
    )
    assert rc == 0 and knobs == [] and fixtures == [] and tags == ["cal0"]


def test_calibrate_clamped_cap_ratio_does_not_loop(
    tmp_path, monkeypatch, capsys
):
    # ocr_cap_height_ratio pinned at the 0.95 clamp and wpc in band: nothing
    # to change -> break after the first render, exit 1 with the clamp note.
    knobs, _, tags = _calibrate_harness(
        tmp_path, monkeypatch, [(0.5, 1.0), (0.5, 1.0)]
    )
    # start the knob at the clamp so the solve is a no-op
    with open(nv.PROFILES, encoding="utf-8") as fh:
        doc = json.load(fh)
    doc["profiles"]["Test Mart"]["typography"]["ocr_cap_height_ratio"] = 0.95
    with open(nv.PROFILES, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    rc = nv.cmd_calibrate(
        argparse.Namespace(slug="testmart", iterations=3, truth="fixture")
    )
    assert rc == 1
    assert knobs == [] and tags == ["cal0"]
    assert "pinned at the renderer clamp" in capsys.readouterr().out
