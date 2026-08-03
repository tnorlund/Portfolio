"""Tier routing for the P2 adjudicator (scripts/agentic_adjudicate.py).

Fixture dossiers cover every tier and demotion path from
docs/line-items/agentic-review/OPERATING_MODEL.md: T0's four-way gate
and per-pass cap, T1 batching and golden signal requirements, every T2
escalation class, abstention, and freeze-marker demotion.
"""

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_adjudicator():
    spec = importlib.util.spec_from_file_location(
        "agentic_adjudicate_test",
        REPO_ROOT / "scripts" / "agentic_adjudicate.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adj = _load_adjudicator()


def _dossier(
    image_id="img-1",
    receipt_id=1,
    merchant="Test Mart",
    mode="H-clean-extension",
    recon_status="mismatch",
    recommendation="approve-fix",
    proposal="t0",
    signals=(),
    **overrides,
):
    proposals = {
        "t0": {
            "add_line_ids": [3],
            "contiguous": True,
            "verified": True,
            "before": {"status": "mismatch", "delta": -4.0},
            "after": {"status": "match", "delta": 0.0},
            "vision_products_confirmed": True,
        },
        "near": {
            "add_line_ids": [3],
            "contiguous": True,
            "verified": True,
            "after": {"status": "near", "delta": -0.5},
            "vision_products_confirmed": True,
        },
        "noncontiguous": {
            "add_line_ids": [7],
            "contiguous": False,
            "verified": True,
            "after": {"status": "match", "delta": 0.0},
            "vision_products_confirmed": True,
        },
        "no-vision": {
            "add_line_ids": [3],
            "contiguous": True,
            "verified": True,
            "after": {"status": "match", "delta": 0.0},
            "vision_products_confirmed": False,
        },
        "unverified": {
            "add_line_ids": [3],
            "contiguous": True,
            "verified": False,
            "vision_products_confirmed": True,
        },
        None: None,
    }
    dossier = {
        "schema": "dossier-v2",
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant": merchant,
        "mode": mode,
        "recon": {"status": recon_status, "delta": -4.0},
        "bank": {"amount": None},
        "image_suspect": False,
        "destructive": False,
        "duplicate_group": None,
        "proposal": proposals[proposal],
        "visual_evidence": ["row transcription"],
        "verdict_recommendation": recommendation,
        "confidence": "medium",
        "confidence_justification": None,
        "signals_concurring": list(signals),
        "verdict_by": "agent:test-pass",
    }
    dossier.update(overrides)
    return dossier


def _route(dossier):
    return adj.route_dossier(dossier)


# --------------------------------------------------------------------------
# T0: all four conditions, and each missing leg demotes
# --------------------------------------------------------------------------


def test_t0_requires_all_four_conditions():
    assert _route(_dossier()) == ("T0", "auto-extension", False)


def test_t0_denied_when_post_status_is_only_near():
    tier, reason, _ = _route(_dossier(proposal="near"))
    assert tier == "T1"
    assert reason == "guarded-extension"


def test_t0_denied_for_noncontiguous_extension():
    tier, _, _ = _route(_dossier(proposal="noncontiguous"))
    assert tier == "T1"


def test_t0_denied_without_vision_confirmation():
    tier, _, _ = _route(_dossier(proposal="no-vision"))
    assert tier == "T1"


def test_t0_cap_overflows_to_t1():
    dossiers = [
        (f"d{i}.json", _dossier(image_id=f"img-{i}")) for i in range(7)
    ]
    entries = adj.adjudicate(dossiers, "pass-x", frozen=set(), t0_limit=5)
    tiers = [e["tier"] for e in entries]
    assert tiers.count("T0") == 5
    assert tiers.count("T1") == 2
    assert all(
        e["reason"] == "t0-overflow" for e in entries if e["tier"] == "T1"
    )


# --------------------------------------------------------------------------
# T1: batch + golden
# --------------------------------------------------------------------------


def test_unverified_proposal_with_approve_fix_abstains():
    tier, reason, _ = _route(_dossier(proposal="unverified"))
    assert (tier, reason) == (
        "abstain",
        "approve-fix-without-verified-proposal",
    )


def test_golden_with_all_three_signals_is_t1_golden():
    dossier = _dossier(
        recommendation="golden",
        recon_status="match",
        proposal=None,
        signals=("arithmetic", "bank", "vision"),
    )
    assert _route(dossier) == ("T1", "golden-candidate", True)


def test_golden_missing_a_signal_abstains():
    dossier = _dossier(
        recommendation="golden",
        recon_status="match",
        proposal=None,
        signals=("arithmetic", "vision"),
    )
    assert _route(dossier) == (
        "abstain",
        "golden-insufficient-signals",
        False,
    )


# --------------------------------------------------------------------------
# T2 escalations
# --------------------------------------------------------------------------


def test_image_suspect_escalates_even_with_t0_proposal():
    tier, reason, _ = _route(_dossier(image_suspect=True))
    assert (tier, reason) == ("T2", "image_suspect")


def test_destructive_escalates():
    tier, reason, _ = _route(_dossier(destructive=True))
    assert (tier, reason) == ("T2", "destructive")


def test_duplicate_group_is_destructive():
    tier, reason, _ = _route(_dossier(duplicate_group="grp-7"))
    assert (tier, reason) == ("T2", "destructive")


def test_j_unknown_mode_escalates():
    tier, reason, _ = _route(
        _dossier(mode="J-unknown", proposal=None, recommendation="flag")
    )
    assert (tier, reason) == ("T2", "j-unknown")


def test_flag_on_green_escalates():
    tier, reason, _ = _route(
        _dossier(recommendation="flag", recon_status="match", proposal=None)
    )
    assert (tier, reason) == ("T2", "flag-on-green")


def test_flag_on_non_green_abstains():
    tier, reason, _ = _route(
        _dossier(recommendation="flag", recon_status="mismatch", proposal=None)
    )
    assert (tier, reason) == ("abstain", "flag-no-safe-fix")


def test_confirm_abstains():
    tier, reason, _ = _route(
        _dossier(recommendation="confirm", recon_status="match", proposal=None)
    )
    assert (tier, reason) == ("abstain", "confirm-no-action")


# --------------------------------------------------------------------------
# Freeze markers
# --------------------------------------------------------------------------


def test_frozen_tier_demotes_t0_to_t2():
    entries = adj.adjudicate([("d.json", _dossier())], "pass-x", frozen={"T0"})
    assert entries[0]["tier"] == "T2"
    assert entries[0]["reason"] == "frozen:T0"


def test_frozen_mode_class_demotes_t1_to_t2():
    dossier = _dossier(proposal="near", mode="H-ambiguous-overshoot")
    entries = adj.adjudicate([("d.json", dossier)], "pass-x", frozen={"H"})
    assert entries[0]["tier"] == "T2"
    assert entries[0]["reason"] == "frozen:H"


def test_freeze_does_not_touch_other_classes():
    entries = adj.adjudicate(
        [("d.json", _dossier(mode="G-phantom-item"))],
        "pass-x",
        frozen={"H"},
    )
    assert entries[0]["tier"] == "T0"


def test_frozen_golden_class_loses_golden_flag():
    dossier = _dossier(
        recommendation="golden",
        recon_status="match",
        proposal=None,
        signals=("arithmetic", "bank", "vision"),
    )
    entries = adj.adjudicate([("d.json", dossier)], "pass-x", frozen={"T1"})
    assert entries[0]["tier"] == "T2"
    assert entries[0]["golden"] is False


# --------------------------------------------------------------------------
# Digest grouping + end-to-end file flow
# --------------------------------------------------------------------------


def test_digest_groups_t1_by_merchant_and_mode():
    dossiers = [
        ("a.json", _dossier(image_id="a", proposal="near")),
        ("b.json", _dossier(image_id="b", proposal="near")),
        (
            "c.json",
            _dossier(
                image_id="c",
                merchant="Gelson's",
                mode="B-baseline-ocr-broken",
                proposal="near",
            ),
        ),
    ]
    entries = adj.adjudicate(dossiers, "pass-x", frozen=set())
    digest = adj.build_digest(entries, "pass-x")
    groups = {g["group_id"]: g for g in digest["t1_groups"]}
    assert len(groups) == 2
    assert len(groups["test-mart::h-clean-extension"]["receipts"]) == 2
    assert len(groups["gelson-s::b-baseline-ocr-broken"]["receipts"]) == 1
    assert digest["counts"] == {"T1": 3}


def test_main_writes_verdicts_and_digest(tmp_path):
    dossier_dir = tmp_path / "dossiers"
    dossier_dir.mkdir()
    (dossier_dir / "img-1-1.json").write_text(
        json.dumps(_dossier()), encoding="utf-8"
    )
    freeze_dir = tmp_path / "freeze"
    freeze_dir.mkdir()
    (freeze_dir / "T0").write_text("audit disagreement", encoding="utf-8")
    out_dir = tmp_path / "verdicts"

    rc = adj.main(
        [
            "--pass-id",
            "pass-e2e",
            "--dossier-dir",
            str(dossier_dir),
            "--out-dir",
            str(out_dir),
            "--freeze-dir",
            str(freeze_dir),
        ]
    )
    assert rc == 0
    lines = (out_dir / "pass-e2e.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    # The freeze marker demoted what would have been T0.
    assert entry["tier"] == "T2"
    assert entry["reason"] == "frozen:T0"
    digest = json.loads((out_dir / "pass-e2e.digest.json").read_text())
    assert digest["pass_id"] == "pass-e2e"
    assert digest["counts"] == {"T2": 1}
