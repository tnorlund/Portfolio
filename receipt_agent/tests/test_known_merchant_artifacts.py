"""Regression guard: the emitted merchant-intelligence artifacts reproduce the
hand-validated tax config's GATE-RELEVANT fields, so wiring the loader changes
only the *source* of the config, not its values.

Compares each committed ``merchant_intelligence/<slug>.json`` against
``MERCHANT_TAX_PROFILES``. Gate-relevant fields are: taxable/nontaxable flags,
can_support_taxable_edits, and (only when support is on) the validated rate /
jurisdiction rates. ``confidence`` is a research-derived signal and is allowed
to differ from the hand label.
"""

from decimal import Decimal

import pytest

from receipt_agent.agents.label_evaluator.merchant_tax_config import (
    MERCHANT_TAX_PROFILES,
    merchant_supports_taxable_edits,
    merchant_tax_profile,
    merchant_taxable_edit_rate,
)
from receipt_agent.agents.label_evaluator.merchant_research import (
    load_merchant_intelligence,
)
from receipt_agent.agents.label_evaluator.merchant_research.known_merchants import (
    KNOWN_MERCHANTS,
    build_artifact_payloads,
    build_known_artifacts,
)
from receipt_agent.agents.label_evaluator.merchant_research.loader import (
    _ARTIFACT_DIR,
    effective_structure,
    list_available_slugs,
    structure_is_enabling,
)

GEN_AT = "2026-06-26T00:00:00+00:00"


def test_artifact_exists_for_every_hardcoded_merchant():
    available = set(list_available_slugs())
    missing = set(MERCHANT_TAX_PROFILES) - available
    assert not missing, f"no artifact for hand-validated merchants: {missing}"


@pytest.mark.parametrize("slug", sorted(MERCHANT_TAX_PROFILES))
def test_committed_artifact_matches_config_gate_fields(slug):
    intel = load_merchant_intelligence(slug)
    assert intel is not None, f"artifact {slug}.json missing or unparseable"
    prof = MERCHANT_TAX_PROFILES[slug]
    tax = intel.tax

    artifact_taxable = frozenset({tax.taxable_flag} if tax.taxable_flag else set())
    assert artifact_taxable == prof.taxable_flags
    assert frozenset(tax.nontaxable_flags) == prof.nontaxable_flags
    assert tax.can_support_taxable_edits == prof.can_support_taxable_edits

    # Rate(s) are only consulted by the gate when support is on.
    if prof.can_support_taxable_edits:
        assert tax.validated_rate == prof.validated_rate
        assert tuple(tax.jurisdiction_rates) == tuple(prof.jurisdiction_rates)


def test_builder_is_deterministic():
    a = build_known_artifacts(GEN_AT)
    b = build_known_artifacts(GEN_AT)
    assert {s: i.to_dict() for s, i in a.items()} == {
        s: i.to_dict() for s, i in b.items()
    }


def test_every_committed_artifact_is_builder_generated():
    # The set of committed artifacts must EXACTLY equal the builder's output —
    # not just a subset. Otherwise a hand-added merchant_intelligence/<x>.json
    # (e.g. one enabling a previously-unsupported merchant) would silently change
    # gate behavior and slip past the per-slug freshness check. Every committed
    # artifact must be reproducible by the builder; hand-editing is forbidden
    # (see merchant_intelligence/README.md). New merchants are added by
    # extending the builder, which keeps this invariant.
    built = set(build_known_artifacts(GEN_AT))
    on_disk = set(list_available_slugs())
    assert on_disk == built, (
        f"committed artifacts diverge from the builder: "
        f"unexpected={on_disk - built}, missing={built - on_disk}"
    )


def test_committed_artifacts_are_fresh():
    # The committed JSON (INCLUDING the stored review block) must equal what the
    # builder emits today, so the artifacts cannot silently drift from the
    # research inputs or carry a stale/hand-edited review block.
    import json

    payloads = build_artifact_payloads(GEN_AT)
    for slug, payload in payloads.items():
        on_disk = json.loads((_ARTIFACT_DIR / f"{slug}.json").read_text("utf-8"))
        assert on_disk == payload, (
            f"{slug}.json is stale; re-run "
            f"`known_merchants write --generated-at {GEN_AT}`"
        )


def test_every_known_merchant_has_provenance():
    for intel in build_known_artifacts(GEN_AT).values():
        assert intel.tax.provenance, f"{intel.slug} has no provenance"


# --- The gate behaves identically through the artifact path ----------------- #


def test_gate_behavior_unchanged_with_review_gate():
    # The four validated merchants support edits; the rest do not — identical to
    # the hand-validated set. Vons/Sprouts resolve through their AUTO-APPROVED
    # artifacts; Amazon Fresh/Target are parked (needs_review) and resolve via
    # the hardcoded fallback — either way the gate values match.
    assert merchant_supports_taxable_edits("Vons") is True
    assert merchant_supports_taxable_edits("Sprouts Farmers Market") is True
    assert merchant_supports_taxable_edits("Amazon Fresh") is True
    assert merchant_supports_taxable_edits("Target") is True
    assert merchant_supports_taxable_edits("Costco Wholesale") is False
    assert merchant_supports_taxable_edits("The Home Depot") is False
    assert merchant_supports_taxable_edits("Gelson's") is False
    assert merchant_supports_taxable_edits("Smith's") is False


def test_vons_edit_rate_snaps_to_7_25_via_approved_artifact():
    # A slightly noisy observed rate snaps to the artifact-sourced 7.25%.
    assert merchant_taxable_edit_rate("Vons", Decimal("0.0726")) == Decimal("0.0725")


def test_target_multi_jurisdiction_rates_resolve():
    # Target is parked (medium confidence / multi-jurisdiction) so it resolves
    # via the hardcoded fallback, but the allowed rates are identical.
    prof = merchant_tax_profile("Target")
    assert prof is not None
    assert prof.allowed_rates() == (
        Decimal("0.08375"),
        Decimal("0.0950"),
        Decimal("0.0975"),
    )
    # An NV-jurisdiction observation snaps to the NV rate, not a CA one.
    assert merchant_taxable_edit_rate("Target", Decimal("0.0838")) == Decimal("0.08375")


# --- NEW merchants (M5): researched beyond the hand-validated 8 ------------- #


def test_cvs_new_merchant_onboarded_at_nv_rate():
    # CVS is NOT in the hand-validated set; a real Las Vegas receipt
    # ("PLAN B ONE STEP 49.99T", "NV 8.375% TAX 4.19") onboards it at 8.375%.
    assert "cvs" not in MERCHANT_TAX_PROFILES
    prof = merchant_tax_profile("CVS")
    assert prof is not None
    assert prof.can_support_taxable_edits is True
    assert prof.validated_rate == Decimal("0.08375")


def test_cvs_store_name_variants_resolve_via_artifact_brand_prefix():
    # Receipts say "CVS", "CVS Pharmacy", "CVS pharmacy"; all must resolve to the
    # cvs artifact (exact for "CVS", brand-prefix for the longer variants).
    for name in ("CVS", "CVS Pharmacy", "CVS pharmacy"):
        assert merchant_supports_taxable_edits(name) is True
        assert merchant_taxable_edit_rate(name, Decimal("0.0838")) == Decimal("0.08375")


@pytest.mark.parametrize("name", ["Trader Joe's", "TRADER JOE'S", "Whole Foods Market"])
def test_researched_new_merchants_correctly_off(name):
    # Both were researched and conservatively REFUSED (mislabeled tax / single
    # taxed receipt) — the artifact records why, but support stays off.
    assert merchant_supports_taxable_edits(name) is False


def test_new_merchant_off_reasons_are_recorded():
    tj = load_merchant_intelligence("trader_joes")
    assert tj is not None and not tj.tax.can_support_taxable_edits
    assert any("mislabeled" in p for p in tj.tax.provenance)
    wf = load_merchant_intelligence("whole_foods_market")
    assert wf is not None and not wf.tax.can_support_taxable_edits
    assert any("1 taxed receipt" in p for p in wf.tax.provenance)


# --- Structure blocks (M7) -------------------------------------------------- #


def test_vons_structure_is_line_item_and_enabling():
    s = effective_structure("vons")
    assert s is not None
    assert s["structure_type"] == "line_item"
    assert s["status"] == "auto_approved"
    assert structure_is_enabling("vons") is True
    assert "add_line_item" in s["applicable_operations"]


def test_tan_la_is_a_service_merchant_parked_for_review():
    # Tan L.A. (NEW service merchant): classified service, NO line-item ops, and
    # parked (needs_review) because a new/split structural assignment is not
    # auto-trusted — synthesis grounding is granted only once it is approved.
    s = effective_structure("tan_l_a")
    assert s is not None
    assert s["structure_type"] == "service"
    assert "add_line_item" not in s["applicable_operations"]
    assert "remove_line_item" not in s["applicable_operations"]
    assert s["status"] == "needs_review"
    assert structure_is_enabling("tan_l_a") is False


def test_hand_edited_structure_status_or_confidence_cannot_enable(tmp_path, monkeypatch):
    # The loader RECOMPUTES the whole structure from archetype_mix; forging the
    # stored status OR confidence must not enable a parked structure (codex
    # MEDIUM). Tan L.A.'s mix (2 service / 1 line_item -> medium) recomputes to
    # needs_review regardless of edited derived fields.
    import json

    from receipt_agent.agents.label_evaluator.merchant_research import loader

    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    payload = dict(build_artifact_payloads(GEN_AT)["tan_l_a"])
    payload["structure"] = dict(
        payload["structure"], status="auto_approved", confidence="high"
    )
    (tmp_path / "tan_l_a.json").write_text(json.dumps(payload), encoding="utf-8")
    eff = loader.effective_structure("tan_l_a")
    assert eff["status"] == "needs_review"
    assert eff["confidence"] == "medium"  # recomputed from the mix, not the file
    assert loader.structure_is_enabling("tan_l_a") is False


def test_structure_without_mix_is_parked(tmp_path, monkeypatch):
    import json

    from receipt_agent.agents.label_evaluator.merchant_research import loader

    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    payload = dict(build_artifact_payloads(GEN_AT)["vons"])
    # Strip the mix but keep a forged high-confidence auto_approved structure.
    payload["structure"] = {
        "structure_type": "line_item", "confidence": "high",
        "status": "auto_approved", "applicable_operations": ["add_line_item"],
    }
    (tmp_path / "vons.json").write_text(json.dumps(payload), encoding="utf-8")
    assert loader.effective_structure("vons")["status"] == "needs_review"
    assert loader.structure_is_enabling("vons") is False


# --- Service grounding contract hook (M7b) ---------------------------------- #


def test_service_grounding_hook_for_line_item_merchant_is_noop():
    from receipt_agent.agents.label_evaluator.merchant_research import (
        service_grounding_for,
    )

    c = service_grounding_for("vons")
    assert c["is_service"] is False
    assert c["valid_grounding_without_line_items"] is False


def test_service_grounding_hook_parked_service_grants_no_override():
    from receipt_agent.agents.label_evaluator.merchant_research import (
        service_grounding_for,
    )

    c = service_grounding_for("tan_l_a")
    assert c["is_service"] is True
    # Parked (needs_review) -> override NOT granted; line-item ops excluded.
    assert c["valid_grounding_without_line_items"] is False
    assert "add_line_item" not in c["applicable_operations"]


def test_service_grounding_hook_grants_override_only_when_approved(tmp_path, monkeypatch):
    from receipt_agent.agents.label_evaluator.merchant_research import loader

    _copy = build_artifact_payloads  # noqa: F841 (ensure import side-effects)
    import pathlib
    import shutil

    src = pathlib.Path(loader._ARTIFACT_DIR)
    for f in src.glob("*.json"):
        shutil.copy(f, tmp_path / f.name)
    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    assert loader.service_grounding_for("tan_l_a")["valid_grounding_without_line_items"] is False
    loader.record_approval(
        "tan_l_a", approved_by="tyler", approved_at="t", kind="structure",
    )
    c = loader.service_grounding_for("tan_l_a")
    assert c["valid_grounding_without_line_items"] is True
    assert set(c["applicable_operations"]) == {
        "replace_field", "amount_mutation", "compose_header", "hard_negative",
    }


def test_known_merchants_cover_the_validated_set():
    slugs = {
        __import__(
            "receipt_agent.agents.label_evaluator.merchant_research.research",
            fromlist=["_slug"],
        )._slug(spec.merchant)
        for spec in KNOWN_MERCHANTS
    }
    assert set(MERCHANT_TAX_PROFILES) <= slugs
