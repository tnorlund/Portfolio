"""Adversarial tests for the committed render regression gate."""

from __future__ import annotations

import json

import pytest

from synthesis_loop import render_regression_guard as guard


def test_pinned_fleet_covers_nine_receipts_and_names_dollar_tree_fixture():
    assert len(guard.PINNED) == 9
    assert {pin.slug for pin in guard.PINNED} == {
        "costco_golden",
        "costco_new",
        "dollar_tree_fixture",
        "gelsons",
        "sprouts",
        "the_stand",
        "trader_joes",
        "vons_golden",
        "wild_fork",
    }
    dollar_tree = next(
        pin for pin in guard.PINNED if pin.slug == "dollar_tree_fixture"
    )
    assert dollar_tree.truth_fixture == "legacy-profile-without-logo"


@pytest.mark.parametrize("gate", ["check", "compare"])
def test_gate_rejects_omitted_pinned_slug(
    gate: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline = {
        "costco_golden": "costco-sha",
        "vons_golden": "vons-sha",
    }
    rendered = {"costco_golden": "costco-sha"}
    monkeypatch.setattr(guard, "_render_all", lambda _out_dir: rendered)

    if gate == "check":
        baseline_path = tmp_path / "committed-baseline.json"
        baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
        monkeypatch.setattr(guard, "COMMITTED_BASELINE", str(baseline_path))
        result = guard.check(str(tmp_path / "out"))
    else:
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        (baseline_dir / "manifest.json").write_text(
            json.dumps(baseline), encoding="utf-8"
        )
        result = guard.compare(str(baseline_dir), str(tmp_path / "out"))

    assert result != 0
    output = capsys.readouterr().out
    assert "missing slugs: ['vons_golden']" in output
    assert "extra slugs: []" in output


@pytest.mark.parametrize("gate", ["check", "compare"])
def test_gate_rejects_unexpected_rendered_slug(
    gate: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline = {"costco_golden": "costco-sha"}
    rendered = {
        "costco_golden": "costco-sha",
        "unexpected": "unexpected-sha",
    }
    monkeypatch.setattr(guard, "_render_all", lambda _out_dir: rendered)

    if gate == "check":
        baseline_path = tmp_path / "committed-baseline.json"
        baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
        monkeypatch.setattr(guard, "COMMITTED_BASELINE", str(baseline_path))
        result = guard.check(str(tmp_path / "out"))
    else:
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        (baseline_dir / "manifest.json").write_text(
            json.dumps(baseline), encoding="utf-8"
        )
        result = guard.compare(str(baseline_dir), str(tmp_path / "out"))

    assert result != 0
    output = capsys.readouterr().out
    assert "missing slugs: []" in output
    assert "extra slugs: ['unexpected']" in output
