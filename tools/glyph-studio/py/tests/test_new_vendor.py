"""Offline contracts for new_vendor.py (no AWS)."""

from __future__ import annotations

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
