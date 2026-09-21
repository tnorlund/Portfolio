"""Offline contracts for new_vendor.py (no AWS)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
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
    chdir.
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


def test_calibrate_writes_vendor_json_export_pin(tmp_path, monkeypatch):
    vendor_dir = tmp_path / "costco"
    vendor_dir.mkdir()
    vendor_path = vendor_dir / "vendor.json"
    vendor_path.write_text(
        json.dumps({"merchant": "Costco Wholesale", "slug": "costco"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(nv, "FONTS_DIR", str(tmp_path))
    assert nv._export_ocr_cap_pin(
        {"ocr_cap_height_ratio": 0.72}, {"ocr_cap_height_ratio": 0.88}
    ) == pytest.approx(0.72)
    assert nv._export_ocr_cap_pin({}, {"ocr_cap_height_ratio": 0.88}) == (
        pytest.approx(0.88)
    )
    nv._set_vendor_export_pin("costco", "ocr_cap_height_ratio", 0.81)
    saved = json.loads(vendor_path.read_text(encoding="utf-8"))
    assert saved["ocr_cap_height_ratio"] == pytest.approx(0.81)
    assert saved["merchant"] == "Costco Wholesale"
    assert "portfolio_slug" not in saved


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


def _git(repo, *args, env=None):
    subprocess.check_call(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )


def test_cmd_export_captures_commit_before_set_entry(tmp_path, monkeypatch):
    """set_entry dirties the manifest; the exporter must still see a clean SHA."""
    from glyphstudio import portfolio_wiring as pw

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    tracked = repo / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(
        repo,
        "commit",
        "-m",
        "i",
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )
    head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    v = {
        "merchant": "Test Mart",
        "slug": "testmart",
        "gold_receipt": {"image_id": "img", "receipt_id": 1},
        "studio_dir": str(tmp_path / "studio"),
    }
    _seed_vendor(tmp_path / "fonts", monkeypatch, v)
    monkeypatch.setattr(nv, "_ROOT", str(repo))
    monkeypatch.setattr(nv, "PIPELINE_PUBLIC", str(tmp_path / "public"))
    monkeypatch.setattr(nv, "FINALE_FILES", ())
    monkeypatch.setattr(nv, "_truth_env", lambda v, t: {})
    monkeypatch.setattr(nv, "_clear_render_cache", lambda v: None)
    monkeypatch.setattr(pw, "set_dims", lambda *a, **k: None)
    order = []

    def dirty_set_entry(*_a, **_k):
        order.append("set_entry")
        tracked.write_text("dirty\n", encoding="utf-8")

    def fake_run(cmd, env=None, capture=False):
        order.append("run")
        from glyphstudio.provenance import exporter_commit

        if "--exporter-commit" not in cmd:
            exporter_commit(str(repo))
        seen["cmd"] = cmd
        return "  testmart: { w: 760, h: 2308 }\n"

    seen = {}
    monkeypatch.setattr(pw, "set_entry", dirty_set_entry)
    monkeypatch.setattr(nv, "_run", fake_run)
    rc = nv.cmd_export(
        argparse.Namespace(slug="testmart", hero_assets=False, truth=None)
    )
    assert rc == 0
    assert order == ["set_entry", "run"]
    cmd = seen["cmd"]
    assert cmd[cmd.index("--exporter-commit") + 1] == head


def test_cmd_export_refuses_dirty_head_before_set_entry(tmp_path, monkeypatch):
    from glyphstudio import portfolio_wiring as pw

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "tracked.txt").write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(
        repo,
        "commit",
        "-m",
        "i",
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )
    (repo / "tracked.txt").write_text("already dirty\n", encoding="utf-8")
    v = {
        "merchant": "Test Mart",
        "slug": "testmart",
        "gold_receipt": {"image_id": "img", "receipt_id": 1},
        "studio_dir": str(tmp_path / "studio"),
    }
    _seed_vendor(tmp_path / "fonts", monkeypatch, v)
    monkeypatch.setattr(nv, "_ROOT", str(repo))
    called = {"set_entry": False}

    def boom(*_a, **_k):
        called["set_entry"] = True

    monkeypatch.setattr(pw, "set_entry", boom)
    with pytest.raises(RuntimeError, match="refusing dirty HEAD"):
        nv.cmd_export(
            argparse.Namespace(slug="testmart", hero_assets=False, truth=None)
        )
    assert called["set_entry"] is False


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

    ``renders`` is either the sequence of (h_ratio, wpc_ratio) the fake
    review returns per call, or a callable ``model(pitch_ratio) -> dict``
    of metric fields evaluated at the profile's current pitch_ratio (a
    renderer stand-in). Knob writes are recorded instead of touching the
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
    queue = None if callable(renders) else list(renders)
    tags = []

    def fake_review(v, tag, truth):
        tags.append(tag)
        m = {"density_ratio": 1.0, "png": "x.png", "scorecard": "x.md"}
        if queue is None:
            pitch = next(
                (val for k, val in reversed(knobs) if k == "pitch_ratio"),
                0.55,
            )
            m.update(renders(pitch))
        else:
            h, wpc = queue.pop(0)
            m.update(h_ratio=h, wpc_ratio=wpc)
        return m

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
    # Flat on BOTH sides of the clamp edge = true saturation. With no clamp
    # geometry the expansion doubles the pitch delta: 0.585 -> 0.655 ->
    # 0.795 -> 1.075, and the next (1.635) would leave PITCH_BOUNDS, so
    # calibrate says so and stops creeping the knob.
    knobs, _, tags = _calibrate_harness(
        tmp_path, monkeypatch, [(1.0, 0.94)] + [(1.0, 0.941)] * 5
    )
    rc = nv.cmd_calibrate(
        argparse.Namespace(slug="testmart", iterations=8, truth="fixture")
    )
    assert rc == 1  # wpc still off the floor
    assert [k for k, _ in knobs] == ["pitch_ratio"] * 4
    assert [val for _, val in knobs] == pytest.approx(
        [0.585, 0.655, 0.795, 1.075]
    )
    assert tags == ["cal0", "cal1", "cal2", "cal3", "cal4"]
    out = capsys.readouterr().out
    assert "expansion step 1/4" in out and "expansion step 3/4" in out
    assert "did not respond to pitch_ratio" in out
    assert "would leave pitch_ratio bounds" in out
    assert "Leaving pitch_ratio at 1.075" in out
    assert "wpc still off after 8 iterations" in out


def test_calibrate_saturation_after_the_step_cap(
    tmp_path, monkeypatch, capsys
):
    # From pitch 0.35 the doubled deltas (0.019 -> 0.038 -> 0.076 -> 0.152
    # -> 0.304) stay inside PITCH_BOUNDS, so the 4-step cap is what ends it.
    knobs, _, tags = _calibrate_harness(
        tmp_path, monkeypatch, [(1.0, 0.949)] + [(1.0, 0.9491)] * 6
    )
    with open(nv.PROFILES, encoding="utf-8") as fh:
        doc = json.load(fh)
    doc["profiles"]["Test Mart"]["typography"]["pitch_ratio"] = 0.35
    with open(nv.PROFILES, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    rc = nv.cmd_calibrate(
        argparse.Namespace(slug="testmart", iterations=9, truth="fixture")
    )
    assert rc == 1
    vals = [val for _, val in knobs]
    assert vals == pytest.approx([0.369, 0.407, 0.483, 0.635, 0.939], abs=1e-3)
    assert tags == ["cal0", "cal1", "cal2", "cal3", "cal4", "cal5"]
    out = capsys.readouterr().out
    assert "4 expansion steps did not move the render" in out
    assert "Leaving pitch_ratio at 0.939" in out


# --- the clamp deadband (Codex P2 on #1711) ---

# receipt_renderer: advance = clamp(measured, pitch*cap*0.85, pitch*cap*1.15).
# Codex's repro: cap 29px, measured advance 15.95px, wpc 0.90 at pitch 0.55.
_CAP, _MEASURED, _WPC0 = 29.0, 15.95, 0.90


def _clamp_renderer(
    with_geometry, measured=_MEASURED, wpc0=_WPC0, review_pitch=None
):
    """wpc as the renderer would yield it at ``pitch``: the measured OCR
    advance clamped to pitch*cap*[0.85, 1.15]; ``review_pitch`` is what the
    metrics line REPORTS as ocr_pitch_med (may differ on sparse receipts)."""

    def model(pitch):
        lo, hi = pitch * _CAP * 0.85, pitch * _CAP * 1.15
        advance = max(lo, min(hi, measured))
        m = {"h_ratio": 1.0, "wpc_ratio": wpc0 * advance / measured}
        if with_geometry:
            m.update(ocr_pitch_px=review_pitch or measured, synth_cap_px=_CAP)
        return m

    return model


def test_calibrate_crosses_the_clamp_deadband_instead_of_stopping(
    tmp_path, monkeypatch, capsys
):
    # The linear solve 0.55 -> 0.611 keeps 15.95 inside [15.06, 20.37]: the
    # render is unmoved but NOT saturated. With the geometry read back from
    # the metrics, calibrate solves the pitch whose clamp floor lands the
    # advance on target and lands wpc in band on the next render.
    knobs, _, tags = _calibrate_harness(
        tmp_path, monkeypatch, _clamp_renderer(with_geometry=True)
    )
    rc = nv.cmd_calibrate(
        argparse.Namespace(slug="testmart", iterations=10, truth="fixture")
    )
    assert rc == 0
    assert [k for k, _ in knobs] == ["pitch_ratio", "pitch_ratio"]
    assert knobs[0][1] == pytest.approx(0.611)  # deadband move
    # 15.95 / (0.90 * 0.85 * 29) = 0.7185 -> past the 0.647 edge
    assert knobs[1][1] == pytest.approx(0.719, abs=1e-3)
    assert tags == ["cal0", "cal1", "cal2"]
    final = _clamp_renderer(True)(knobs[1][1])["wpc_ratio"]
    assert nv._in_band(final)
    out = capsys.readouterr().out
    assert "deadband" in out and "clamp edge at pitch_ratio 0.647" in out
    assert "did not respond" not in out


def test_calibrate_deadband_fallback_doubles_the_step_without_geometry(
    tmp_path, monkeypatch, capsys
):
    # Same renderer, but the metrics carry no ocr_pitch/cap: the fallback
    # doubles the last step once (0.611 + 2*0.061 = 0.733), which clears the
    # 0.647 edge; the re-render responds and the loop continues.
    knobs, _, tags = _calibrate_harness(
        tmp_path, monkeypatch, _clamp_renderer(with_geometry=False)
    )
    rc = nv.cmd_calibrate(
        argparse.Namespace(slug="testmart", iterations=10, truth="fixture")
    )
    assert rc == 0
    assert [k for k, _ in knobs][:2] == ["pitch_ratio", "pitch_ratio"]
    assert knobs[1][1] == pytest.approx(0.733)
    assert tags[:3] == ["cal0", "cal1", "cal2"]
    out = capsys.readouterr().out
    assert "no clamp geometry in the review metrics" in out
    assert "did not respond" not in out


def test_calibrate_recovers_when_review_geometry_disagrees_with_renderer(
    tmp_path, monkeypatch, capsys
):
    # Codex round-2 case: a sparse receipt where the renderer fell back to
    # box-width advances (16px) but the review reports word-start pitch
    # (20px). wpc 1.10 at pitch 0.55; the linear solve to 0.50 is unmoved
    # (16 in [12.3, 16.7]) and the edge solve from the 20px estimate points
    # UP (0.545), the wrong way. It must be ignored and the expansion must
    # lower pitch_ratio until the render responds, then land in band.
    knobs, _, tags = _calibrate_harness(
        tmp_path,
        monkeypatch,
        _clamp_renderer(True, measured=16.0, wpc0=1.10, review_pitch=20.0),
    )
    # start at 0.55 with wpc0 1.10 there (advance 16 inside the clamp)
    rc = nv.cmd_calibrate(
        argparse.Namespace(slug="testmart", iterations=10, truth="fixture")
    )
    assert rc == 0
    vals = [val for _, val in knobs]
    assert vals[0] == pytest.approx(0.50)  # 0.55 / 1.10, unmoved
    assert vals[1] < 0.50  # expansion lowered it, geometry ignored
    final = _clamp_renderer(True, measured=16.0, wpc0=1.10, review_pitch=20.0)(
        vals[-1]
    )["wpc_ratio"]
    assert nv._in_band(final)
    out = capsys.readouterr().out
    assert "points the wrong way" in out
    assert "did not respond" not in out
    assert len(tags) <= 6


def test_calibrate_no_geometry_wpc_just_under_band_reaches_target(
    tmp_path, monkeypatch, capsys
):
    # Codex round-2 case: no review pitch, wpc 0.949. 0.55 -> 0.58 (unmoved)
    # -> 0.64 (still under the 0.647 edge, unmoved) -> 0.76 (responds, now
    # high) -> linear back to 0.682, in band. A single doubling would have
    # stopped at 0.64 and declared saturation.
    knobs, _, tags = _calibrate_harness(
        tmp_path, monkeypatch, _clamp_renderer(False, wpc0=0.949)
    )
    rc = nv.cmd_calibrate(
        argparse.Namespace(slug="testmart", iterations=10, truth="fixture")
    )
    assert rc == 0
    vals = [val for _, val in knobs]
    assert vals[:3] == pytest.approx([0.58, 0.64, 0.76], abs=1e-3)
    assert vals[-1] == pytest.approx(0.682, abs=1e-3)  # Codex's number
    assert nv._in_band(
        _clamp_renderer(False, wpc0=0.949)(vals[-1])["wpc_ratio"]
    )
    out = capsys.readouterr().out
    assert "expansion step 2/4" in out
    assert "did not respond" not in out


def test_next_pitch_step_caps_and_bounds():
    m = {"wpc_ratio": 0.90}
    assert nv.next_pitch_step(0.6, 0.55, m, 4)[0] is None
    assert "4 expansion steps" in nv.next_pitch_step(0.6, 0.55, m, 4)[1]
    new, why = nv.next_pitch_step(1.1, 1.0, m, 1)  # 1.1 + 0.2 = 1.3 > 1.2
    assert new is None and "bounds" in why
    new, why = nv.next_pitch_step(0.32, 0.35, {"wpc_ratio": 1.2}, 1)
    assert new is None and "bounds" in why  # 0.32 - 0.06 = 0.26 < 0.3
    # a first step with no history still moves by the minimum step
    new, why = nv.next_pitch_step(0.6, None, m, 0)
    assert new == pytest.approx(0.6 + nv.PITCH_MIN_STEP)
    assert "expansion step 1/4" in why and "raising" in why
    new, _ = nv.next_pitch_step(0.6, None, {"wpc_ratio": 1.2}, 0)
    assert new == pytest.approx(0.6 - nv.PITCH_MIN_STEP)


def test_pitch_past_clamp_edge_low_and_high_sides():
    m = {"wpc_ratio": 0.90, "ocr_pitch_px": 15.95, "synth_cap_px": 29.0}
    new, why = nv.pitch_past_clamp_edge(0.611, m)
    assert new == pytest.approx(0.719, abs=1e-3)
    assert new > 15.95 / (0.85 * 29.0) * (1 + nv.PITCH_EDGE_MARGIN)
    assert "deadband" in why
    # wpc high: the ceiling must drop below the measured advance
    m = {"wpc_ratio": 1.10, "ocr_pitch_px": 15.95, "synth_cap_px": 29.0}
    new, _ = nv.pitch_past_clamp_edge(0.50, m)
    assert new < 15.95 / (1.15 * 29.0) * (1 - nv.PITCH_EDGE_MARGIN)
    assert new == pytest.approx(15.95 / (1.10 * 1.15 * 29.0), abs=1e-3)


@pytest.mark.parametrize("wpc", [0.949, 0.90, 0.80, 1.051, 1.10, 1.30])
def test_pitch_past_clamp_edge_always_clears_the_edge_by_the_margin(wpc):
    # For any out-of-band wpc the target solve (edge / wpc) sits further
    # from the edge than PITCH_EDGE_MARGIN, so the re-render provably moves;
    # the margin is the floor for a future narrower band, never the binder.
    m = {"wpc_ratio": wpc, "ocr_pitch_px": 15.95, "synth_cap_px": 29.0}
    new, _ = nv.pitch_past_clamp_edge(0.60, m)
    if wpc < 1:
        edge = 15.95 / (0.85 * 29.0)
        assert new >= edge * (1 + nv.PITCH_EDGE_MARGIN)
        assert new == pytest.approx(edge / wpc, abs=1e-3)
    else:
        edge = 15.95 / (1.15 * 29.0)
        assert new <= edge * (1 - nv.PITCH_EDGE_MARGIN)
        assert new == pytest.approx(edge / wpc, abs=1e-3)


def test_pitch_past_clamp_edge_needs_geometry():
    assert nv.pitch_past_clamp_edge(0.611, {"wpc_ratio": 0.90}) == (
        None,
        "no clamp geometry in the review metrics",
    )
    m = {"wpc_ratio": 0.90, "ocr_pitch_px": 0.0, "synth_cap_px": 29.0}
    assert nv.pitch_past_clamp_edge(0.611, m)[0] is None


def test_render_review_reads_back_clamp_geometry(tmp_path, monkeypatch):
    v = {
        "merchant": "Test Mart",
        "slug": "testmart",
        "gold_receipt": {"image_id": "img", "receipt_id": 1},
        "studio_dir": str(tmp_path / "studio"),
    }
    monkeypatch.setattr(nv, "_clear_render_cache", lambda v: None)
    monkeypatch.setattr(nv, "_truth_env", lambda v, t: {})
    line = (
        "metrics ocr_pitch_med=15.95 real_h_med=30.00 synth_h_med=29.00 "
        "h_ratio=0.967 real_wpc_med=20.00 synth_wpc_med=18.00 "
        "real_density=0.148 synth_density=0.149 density_ratio=1.004"
    )
    monkeypatch.setattr(nv, "_run", lambda *a, **k: "noise\n" + line + "\n")
    m = nv._render_review(v, "t", "fixture")
    assert m["ocr_pitch_px"] == pytest.approx(15.95)
    assert m["synth_cap_px"] == pytest.approx(29.0)
    assert m["wpc_ratio"] == pytest.approx(0.9)
    assert m["h_ratio"] == pytest.approx(0.967)
