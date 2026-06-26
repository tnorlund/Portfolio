"""Tests for the human-approval review gate.

Covers: deterministic status classification, the CRITICAL invariant that only
auto_approved / human-approved intelligence drives the tax gate (needs_review is
parked), the human-approval overlay keyed by the tax-block content hash, and the
hash-revert on changed data.
"""

import json
from decimal import Decimal

import pytest

from receipt_agent.agents.label_evaluator import merchant_tax_config
from receipt_agent.agents.label_evaluator.merchant_research import (
    AUTO_APPROVED,
    NEEDS_REVIEW,
    REJECTED,
    CatalogEntry,
    MerchantDetails,
    MerchantIntelligence,
    TaxArtifact,
    compute_review,
    tax_block_hash,
)
from receipt_agent.agents.label_evaluator.merchant_research import loader
from receipt_agent.agents.label_evaluator.merchant_research.known_merchants import (
    build_known_artifacts,
)

GEN_AT = "2026-06-26T00:00:00+00:00"


def _intel(
    *,
    slug="acme",
    merchant="Acme",
    confidence="high",
    can_support=True,
    validated_rate="0.0725",
    jurisdiction="CA-Ventura",
    jurisdiction_rates=(),
    taxed_count=4,
    web_rate="0.0725",
    address="1 Main St, Town, CA 90000",
    provenance=("web rate 0.0725 uncontradicted by 4 taxed receipts",),
) -> MerchantIntelligence:
    sources = {"receipts": {"count": 6, "taxed_count": taxed_count}}
    if web_rate is not None:
        sources["web"] = {"published_rate": web_rate}
    return MerchantIntelligence(
        merchant=merchant,
        slug=slug,
        tax=TaxArtifact(
            taxable_flag="T",
            nontaxable_flags=("F",),
            jurisdiction=jurisdiction,
            validated_rate=Decimal(validated_rate) if validated_rate else None,
            jurisdiction_rates=tuple(Decimal(r) for r in jurisdiction_rates),
            can_support_taxable_edits=can_support,
            confidence=confidence,
            provenance=provenance,
        ),
        catalog=(),
        details=MerchantDetails(address=address, store_id=None, category="grocery"),
        generated_at=GEN_AT,
        sources=sources,
    )


# --------------------------------------------------------------------------- #
# Deterministic classification
# --------------------------------------------------------------------------- #


def test_auto_approved_single_jurisdiction_high_confidence():
    r = compute_review(_intel())
    assert r.status == AUTO_APPROVED
    assert r.is_enabling()


def test_medium_confidence_is_needs_review():
    r = compute_review(_intel(confidence="medium"))
    assert r.status == NEEDS_REVIEW
    assert not r.is_enabling()
    assert any("confidence" in x for x in r.reasons)


def test_too_few_taxed_is_needs_review():
    r = compute_review(_intel(taxed_count=1))
    assert r.status == NEEDS_REVIEW
    assert any("taxed receipt" in x for x in r.reasons)


def test_single_source_is_needs_review():
    # No web rate and no address -> only the receipts source cross-checks.
    r = compute_review(_intel(web_rate=None, address=None))
    assert r.status == NEEDS_REVIEW
    assert any("independent source" in x for x in r.reasons)


def test_multi_jurisdiction_is_needs_review_even_high_confidence():
    r = compute_review(
        _intel(
            validated_rate=None,
            jurisdiction="multi",
            jurisdiction_rates=("0.08375", "0.0950"),
        )
    )
    assert r.status == NEEDS_REVIEW
    assert any("multi-jurisdiction" in x for x in r.reasons)


def test_matches_validated_config_auto_approves_multi_jurisdiction():
    # The "matches existing validated config" branch can auto-approve a merchant
    # the single-jurisdiction branch would not (e.g. multi-jurisdiction).
    intel = _intel(
        validated_rate=None,
        jurisdiction="multi",
        jurisdiction_rates=("0.08375", "0.0950"),
    )
    r = compute_review(intel, matches_validated_config=True)
    assert r.status == AUTO_APPROVED


def test_no_tax_evidence_is_rejected():
    r = compute_review(
        _intel(can_support=False, validated_rate=None, taxed_count=0, web_rate=None,
               provenance=("no positive tax",))
    )
    assert r.status == REJECTED


def test_contradiction_is_rejected_but_uncontradicted_is_not():
    contradicted = _intel(
        provenance=("web rate 0.0725 contradicted: observed effective 0.095 exceeds it",)
    )
    assert compute_review(contradicted).status == REJECTED
    # The positive evidence string must NOT trip the contradiction marker.
    ok = _intel(provenance=("web rate 0.0725 uncontradicted by 4 taxed receipts",))
    assert compute_review(ok).status == AUTO_APPROVED


# --------------------------------------------------------------------------- #
# The CRITICAL gate invariant: only enabling statuses drive the tax gate
# --------------------------------------------------------------------------- #


def _write(tmp_path, intel: MerchantIntelligence, *, review=None):
    payload = intel.to_dict()
    if review is not None:
        payload["review"] = review
    (tmp_path / f"{intel.slug}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_needs_review_artifact_is_parked_from_the_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    merchant_tax_config.merchant_tax_profile.cache_clear()
    # A medium-confidence NEW merchant: needs_review -> parked -> no profile.
    _write(tmp_path, _intel(slug="acme", merchant="Acme", confidence="medium"))
    try:
        assert loader.artifact_tax_profile("acme") is None
        assert merchant_tax_config.merchant_tax_profile("Acme") is None
    finally:
        merchant_tax_config.merchant_tax_profile.cache_clear()


def test_auto_approved_artifact_drives_the_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    merchant_tax_config.merchant_tax_profile.cache_clear()
    _write(tmp_path, _intel(slug="acme", merchant="Acme"))
    try:
        prof = merchant_tax_config.merchant_tax_profile("Acme")
        assert prof is not None
        assert prof.can_support_taxable_edits is True
        assert prof.validated_rate == Decimal("0.0725")
    finally:
        merchant_tax_config.merchant_tax_profile.cache_clear()


def test_hand_edited_review_block_cannot_enable(tmp_path, monkeypatch):
    # CRITICAL: the loader RECOMPUTES the status; a forged "auto_approved" block
    # in the JSON must not enable a merchant the deterministic logic parks.
    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    merchant_tax_config.merchant_tax_profile.cache_clear()
    _write(
        tmp_path,
        _intel(slug="acme", merchant="Acme", confidence="medium"),
        review={"status": AUTO_APPROVED, "reasons": ["forged"], "approved_by": "x",
                "approved_at": "now"},
    )
    try:
        assert loader.effective_review("acme").status == NEEDS_REVIEW
        assert loader.artifact_tax_profile("acme") is None
    finally:
        merchant_tax_config.merchant_tax_profile.cache_clear()


# --------------------------------------------------------------------------- #
# Human approval overlay + content-hash revert
# --------------------------------------------------------------------------- #


def test_human_approval_lifts_needs_review_and_drives_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    merchant_tax_config.merchant_tax_profile.cache_clear()
    _write(tmp_path, _intel(slug="acme", merchant="Acme", confidence="medium"))
    try:
        assert loader.effective_review("acme").status == NEEDS_REVIEW
        loader.record_approval(
            "acme", approved_by="tyler", approved_at="2026-06-26T12:00:00+00:00"
        )
        review = loader.effective_review("acme")
        assert review.status == "approved"
        assert review.is_enabling()
        assert review.approved_by == "tyler"
        merchant_tax_config.merchant_tax_profile.cache_clear()
        assert merchant_tax_config.merchant_supports_taxable_edits("Acme") is True
    finally:
        merchant_tax_config.merchant_tax_profile.cache_clear()


def test_rejected_artifact_cannot_be_human_approved(tmp_path, monkeypatch):
    # rejected is a hard stop: an approval entry for it does NOT lift it.
    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    intel = _intel(
        slug="acme",
        merchant="Acme",
        can_support=False,
        validated_rate=None,
        taxed_count=0,
        web_rate=None,
        provenance=("no positive tax",),
    )
    _write(tmp_path, intel)
    assert loader.effective_review("acme").status == REJECTED
    loader.record_approval(
        "acme", approved_by="tyler", approved_at="2026-06-26T12:00:00+00:00"
    )
    # Still rejected — the sign-off cannot override a hard stop.
    assert loader.effective_review("acme").status == REJECTED
    assert loader.artifact_tax_profile("acme") is None


def test_changed_tax_block_reverts_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    intel = _intel(slug="acme", merchant="Acme", confidence="medium")
    _write(tmp_path, intel)
    loader.record_approval(
        "acme", approved_by="tyler", approved_at="2026-06-26T12:00:00+00:00"
    )
    assert loader.effective_review("acme").status == "approved"
    # Change a tax fact -> new content hash -> old sign-off no longer applies.
    changed = _intel(slug="acme", merchant="Acme", confidence="medium",
                     validated_rate="0.0800")
    assert tax_block_hash(changed) != tax_block_hash(intel)
    _write(tmp_path, changed)
    assert loader.effective_review("acme").status == NEEDS_REVIEW


def test_provenance_edit_does_not_revoke_approval(tmp_path, monkeypatch):
    # Hash excludes provenance: rewording the evidence trail keeps the sign-off.
    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    intel = _intel(slug="acme", merchant="Acme", confidence="medium")
    _write(tmp_path, intel)
    loader.record_approval(
        "acme", approved_by="tyler", approved_at="2026-06-26T12:00:00+00:00"
    )
    reworded = _intel(slug="acme", merchant="Acme", confidence="medium",
                      provenance=("reworded evidence trail",))
    _write(tmp_path, reworded)
    assert loader.effective_review("acme").status == "approved"


# --------------------------------------------------------------------------- #
# Committed-artifact statuses (the real 11)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Structure human-approval (M7b): separate kind, archetype-mix-hash keyed
# --------------------------------------------------------------------------- #


def _copy_artifacts(tmp_path):
    import pathlib
    import shutil

    src = pathlib.Path(loader._ARTIFACT_DIR)
    for f in src.glob("*.json"):
        shutil.copy(f, tmp_path / f.name)


def test_structure_approval_lifts_needs_review_and_enables(tmp_path, monkeypatch):
    _copy_artifacts(tmp_path)
    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    assert loader.effective_structure("tan_l_a")["status"] == NEEDS_REVIEW
    assert loader.structure_is_enabling("tan_l_a") is False
    loader.record_approval(
        "tan_l_a", approved_by="tyler", approved_at="2026-06-26T12:00:00+00:00",
        kind="structure",
    )
    s = loader.effective_structure("tan_l_a")
    assert s["status"] == "approved"
    assert s["approved_by"] == "tyler"
    assert loader.structure_is_enabling("tan_l_a") is True


def test_tax_and_structure_approvals_are_independent(tmp_path, monkeypatch):
    _copy_artifacts(tmp_path)
    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    # A STRUCTURE sign-off must not satisfy the TAX gate (Tan L.A. tax is rejected).
    loader.record_approval(
        "tan_l_a", approved_by="tyler", approved_at="t", kind="structure",
    )
    assert loader.structure_is_enabling("tan_l_a") is True
    assert loader.effective_review("tan_l_a").status == REJECTED  # tax unchanged


def test_structure_approval_reverts_when_mix_changes(tmp_path, monkeypatch):
    import json

    _copy_artifacts(tmp_path)
    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    loader.record_approval(
        "tan_l_a", approved_by="tyler", approved_at="t", kind="structure",
    )
    assert loader.structure_is_enabling("tan_l_a") is True
    # Re-fingerprint shifts the mix -> new archetype-mix hash -> sign-off lapses.
    art = json.loads((tmp_path / "tan_l_a.json").read_text("utf-8"))
    art["structure"]["archetype_mix"] = {"service": 5, "line_item_retail": 1}
    (tmp_path / "tan_l_a.json").write_text(json.dumps(art), encoding="utf-8")
    s = loader.effective_structure("tan_l_a")
    # New mix (5/6 = 0.83) recomputes to high -> auto_approved anyway, so assert
    # the OLD sign-off no longer matches by checking a mix that stays medium.
    art["structure"]["archetype_mix"] = {"service": 2, "line_item_retail": 2}
    (tmp_path / "tan_l_a.json").write_text(json.dumps(art), encoding="utf-8")
    assert loader.effective_structure("tan_l_a")["status"] == NEEDS_REVIEW
    assert loader.structure_is_enabling("tan_l_a") is False


def test_committed_review_statuses():
    grouped = loader.reviews_by_status()
    by_slug = {
        slug: review.status
        for items in grouped.values()
        for slug, review in items
    }
    # Auto-approved (drive the gate): the high-confidence single-jurisdiction set.
    assert by_slug["vons"] == AUTO_APPROVED
    assert by_slug["sprouts_farmers_market"] == AUTO_APPROVED
    assert by_slug["cvs"] == AUTO_APPROVED
    # Parked.
    assert by_slug["amazon_fresh"] == NEEDS_REVIEW
    assert by_slug["target"] == NEEDS_REVIEW
    assert by_slug["whole_foods_market"] == NEEDS_REVIEW
    # Rejected.
    assert by_slug["smiths"] == REJECTED
    assert by_slug["trader_joes"] == REJECTED


def test_builder_review_matches_loader_recomputation():
    # The review block the builder writes equals what the loader recomputes.
    built = build_known_artifacts(GEN_AT)
    for slug in built:
        on_disk = loader.effective_review(slug)
        assert on_disk is not None
        # auto_approved/needs_review/rejected from the loader must match the
        # stored block's status for the committed (un-approved) set.
        stored = json.loads(
            (loader._ARTIFACT_DIR / f"{slug}.json").read_text("utf-8")
        )["review"]
        assert on_disk.status == stored["status"], slug
