"""Tests for the deterministic research assembly + cross-check core.

These exercise ``recommend_taxable_support`` / ``assemble_merchant_intelligence``
against evidence shaped like the 8 hand-validated merchants, plus the
cross-source edge cases (web corroboration, disagreement, single-receipt,
grocery-exempt, block override). The gate-relevant outputs
(``can_support_taxable_edits``, ``validated_rate``, ``jurisdiction_rates``,
flags) must reproduce the hand-validated decisions.
"""

from decimal import Decimal

import pytest

from receipt_agent.agents.label_evaluator.merchant_research import (
    PlacesEvidence,
    ReceiptEvidence,
    WebEvidence,
    assemble_merchant_intelligence,
    recommend_taxable_support,
)
from receipt_agent.agents.label_evaluator.merchant_research.schema import (
    MerchantIntelligence,
)

GEN_AT = "2026-06-26T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# Single-jurisdiction validated merchants -> support ON at the web rate
# --------------------------------------------------------------------------- #


def _vons_receipts() -> ReceiptEvidence:
    return ReceiptEvidence(
        taxable_flag="T",
        nontaxable_flags=("S",),
        effective_rates=(
            Decimal("0.0725"),
            Decimal("0.0726"),
            Decimal("0.0726"),
            Decimal("0.0727"),
            Decimal("0.0726"),
        ),
        # Clean per-item observations confirming the 7.25% rate applies here.
        observed_taxable_rates=(Decimal("0.0725"), Decimal("0.0726")),
        receipt_count=5,
        taxed_receipt_count=5,
        image_ids=("a", "b", "c", "d", "e"),
    )


def test_vons_single_jurisdiction_uses_authoritative_web_rate():
    web = WebEvidence(
        jurisdiction="CA-Ventura",
        published_rate=Decimal("0.0725"),
        urls=("https://cdtfa.ca.gov",),
    )
    rec = recommend_taxable_support(_vons_receipts(), web)
    assert rec.can_support_taxable_edits is True
    # Web rate is authoritative (clean 7.25%), not the noisy effective ~7.26%.
    assert rec.validated_rate == Decimal("0.0725")
    assert rec.jurisdiction_rates == ()
    assert rec.confidence == "high"  # >=3 taxed + web corroborates


def test_single_jurisdiction_without_web_is_not_supported():
    # The noisy lower-bound effective rates alone cannot pin the exact rate the
    # downstream gate applies directly, so without an authoritative web rate the
    # merchant is NOT cleared and carries no validated_rate.
    rec = recommend_taxable_support(_vons_receipts(), web=None)
    assert rec.can_support_taxable_edits is False
    assert rec.validated_rate is None
    assert any("no authoritative web rate" in r for r in rec.reasons)


# --------------------------------------------------------------------------- #
# Multi-jurisdiction (Target) -> ON only with per-receipt reconciliation
# --------------------------------------------------------------------------- #


def test_target_multi_jurisdiction_supported_with_reconciliation():
    receipts = ReceiptEvidence(
        taxable_flag="T",
        nontaxable_flags=("NF",),
        effective_rates=(
            Decimal("0.0838"),
            Decimal("0.0951"),
            Decimal("0.0974"),
        ),
        receipt_count=12,
        taxed_receipt_count=12,
        multi_jurisdiction=True,
        per_receipt_rates_reconcile=True,
        reconciled_jurisdiction_rates=(
            Decimal("0.08375"),
            Decimal("0.0950"),
            Decimal("0.0975"),
        ),
    )
    rec = recommend_taxable_support(receipts)
    assert rec.can_support_taxable_edits is True
    assert rec.validated_rate is None
    assert rec.jurisdiction_rates == (
        Decimal("0.08375"),
        Decimal("0.0950"),
        Decimal("0.0975"),
    )
    assert rec.confidence == "high"


def test_multi_jurisdiction_without_reconciliation_blocked():
    # Costco / Home Depot: multi-jurisdiction, per-item flag OCR too sparse to
    # reconcile each receipt to a jurisdiction -> OFF.
    receipts = ReceiptEvidence(
        taxable_flag="A",
        nontaxable_flags=(),
        effective_rates=(Decimal("0.084"), Decimal("0.095"), Decimal("0.097")),
        receipt_count=8,
        taxed_receipt_count=8,
        multi_jurisdiction=True,
        per_receipt_rates_reconcile=False,
    )
    rec = recommend_taxable_support(receipts)
    assert rec.can_support_taxable_edits is False
    assert rec.jurisdiction_rates == ()
    assert any("per-receipt rate" in r for r in rec.reasons)


# --------------------------------------------------------------------------- #
# OFF cases: single taxed receipt, grocery-exempt, explicit block
# --------------------------------------------------------------------------- #


def test_single_taxed_receipt_blocked():
    # Gelson's: validates arithmetically but only ONE taxed receipt.
    receipts = ReceiptEvidence(
        taxable_flag="T",
        nontaxable_flags=("F",),
        effective_rates=(Decimal("0.0724"),),
        receipt_count=4,
        taxed_receipt_count=1,
    )
    rec = recommend_taxable_support(receipts, WebEvidence(published_rate=Decimal("0.0725")))
    assert rec.can_support_taxable_edits is False
    assert any("only 1 taxed receipt" in r for r in rec.reasons)


def test_no_positive_tax_blocked_high_confidence():
    # Smith's: NV grocery exemption, every TAX row is 0.00, no T flag.
    receipts = ReceiptEvidence(
        taxable_flag="",
        nontaxable_flags=("F",),
        effective_rates=(),
        receipt_count=6,
        taxed_receipt_count=0,
    )
    rec = recommend_taxable_support(receipts)
    assert rec.can_support_taxable_edits is False
    assert rec.confidence == "high"  # confidently OFF: no tax to derive a rate
    assert rec.validated_rate is None


def test_block_reason_forces_off_even_with_good_evidence():
    rec = recommend_taxable_support(
        _vons_receipts(),
        WebEvidence(published_rate=Decimal("0.0725")),
        block_reason="per-item OCR sparse this corpus",
    )
    assert rec.can_support_taxable_edits is False
    assert any("blocked: per-item OCR sparse" in r for r in rec.reasons)


def test_block_reason_never_forces_support_on():
    # An override can only turn support OFF; it cannot enable an unsupported one.
    receipts = ReceiptEvidence(
        taxable_flag="",
        nontaxable_flags=(),
        taxed_receipt_count=0,
    )
    rec = recommend_taxable_support(receipts, block_reason="")
    assert rec.can_support_taxable_edits is False


# --------------------------------------------------------------------------- #
# Cross-source disagreement
# --------------------------------------------------------------------------- #


def test_mixed_basket_merchant_supported_at_web_rate():
    # Amazon Fresh: real taxed receipts are MIXED baskets, so effective rates
    # (tax/subtotal) sit well BELOW the 7.25% per-item rate. The effective rate
    # is only a lower bound, so it must NOT block support; the authoritative web
    # rate is adopted because no effective rate contradicts (exceeds) it.
    receipts = ReceiptEvidence(
        taxable_flag="T",
        nontaxable_flags=("F",),
        effective_rates=(Decimal("0.0465"), Decimal("0.0353")),  # mixed baskets
        # The smart layer isolated the taxable item on one receipt and measured
        # its rate directly: 7.25%, confirming the web lookup.
        observed_taxable_rates=(Decimal("0.0725"),),
        receipt_count=6,
        taxed_receipt_count=2,
    )
    web = WebEvidence(jurisdiction="CA-Ventura", published_rate=Decimal("0.0725"))
    rec = recommend_taxable_support(receipts, web)
    assert rec.can_support_taxable_edits is True
    assert rec.validated_rate == Decimal("0.0725")
    assert rec.confidence == "medium"  # only 2 taxed receipts


def test_implausible_web_rate_is_rejected():
    # A garbage web rate above the plausible band is not trusted (receipts, a
    # lower bound, cannot cap it, so the band is the belt).
    web = WebEvidence(jurisdiction="??", published_rate=Decimal("0.30"))
    rec = recommend_taxable_support(_vons_receipts(), web)
    assert rec.can_support_taxable_edits is False
    assert rec.validated_rate is None
    assert any("plausible band" in r for r in rec.reasons)


def test_web_below_lower_bound_effective_rate_is_not_adopted():
    # Effective rate is a LOWER bound: a web rate materially BELOW the highest
    # observed effective rate is contradictory (true rate must be >= the lower
    # bound) and must NOT be adopted.
    receipts = ReceiptEvidence(
        taxable_flag="T",
        nontaxable_flags=("S",),
        effective_rates=(Decimal("0.0760"), Decimal("0.0774")),
        receipt_count=2,
        taxed_receipt_count=2,
    )
    web = WebEvidence(jurisdiction="CA", published_rate=Decimal("0.0725"))
    rec = recommend_taxable_support(receipts, web)
    assert rec.can_support_taxable_edits is False
    assert rec.validated_rate is None


def test_high_effective_rate_signals_uncorroborated_jurisdiction():
    # A receipt whose effective rate is far above the web rate signals a
    # higher-tax jurisdiction; a single-jurisdiction merchant is not cleared.
    receipts = ReceiptEvidence(
        taxable_flag="T",
        nontaxable_flags=("S",),
        effective_rates=(Decimal("0.0725"), Decimal("0.0950")),
        receipt_count=2,
        taxed_receipt_count=2,
    )
    web = WebEvidence(jurisdiction="CA", published_rate=Decimal("0.0725"))
    rec = recommend_taxable_support(receipts, web)
    assert rec.can_support_taxable_edits is False
    assert any("contradicted" in r for r in rec.reasons)


def test_wrong_high_web_rate_rejected_by_per_item_confirmation():
    # A wrong-but-plausible high web rate (9.5%) for a 7.25% store: mixed-basket
    # effective rates cannot contradict it (lower bound), but the per-item
    # observations snap to 7.25%, NOT 9.5%, so the rate is not confirmed and the
    # merchant is not cleared. (Authority check — codex MEDIUM.)
    receipts = ReceiptEvidence(
        taxable_flag="T",
        nontaxable_flags=("F",),
        effective_rates=(Decimal("0.0465"), Decimal("0.0353")),
        observed_taxable_rates=(Decimal("0.0725"),),
        receipt_count=6,
        taxed_receipt_count=2,
    )
    web = WebEvidence(jurisdiction="??", published_rate=Decimal("0.0950"))
    rec = recommend_taxable_support(receipts, web)
    assert rec.can_support_taxable_edits is False
    assert rec.validated_rate is None
    assert any("not confirmed" in r for r in rec.reasons)


def test_single_jurisdiction_without_per_item_confirmation_blocked():
    # Uncontradicted plausible web rate but NO per-item observation to confirm
    # it -> not cleared (web evidence alone is insufficient authority).
    receipts = ReceiptEvidence(
        taxable_flag="T",
        nontaxable_flags=("F",),
        effective_rates=(Decimal("0.0465"), Decimal("0.0353")),
        observed_taxable_rates=(),  # none computable
        receipt_count=6,
        taxed_receipt_count=2,
    )
    web = WebEvidence(jurisdiction="CA-Ventura", published_rate=Decimal("0.0725"))
    rec = recommend_taxable_support(receipts, web)
    assert rec.can_support_taxable_edits is False
    assert any("not confirmed" in r for r in rec.reasons)


def test_multi_jurisdiction_blind_positive_tax_receipt_blocked():
    # A positive-tax receipt with no computable effective rate is invisible to
    # the jurisdiction veto; for a multi-jurisdiction merchant it could belong to
    # an unobserved higher jurisdiction -> refuse. (codex MEDIUM.)
    receipts = ReceiptEvidence(
        taxable_flag="T",
        nontaxable_flags=("NF",),
        effective_rates=(Decimal("0.0838"), Decimal("0.0951")),
        receipt_count=12,
        taxed_receipt_count=12,
        multi_jurisdiction=True,
        per_receipt_rates_reconcile=True,
        reconciled_jurisdiction_rates=(Decimal("0.08375"), Decimal("0.0950")),
        blind_positive_tax_count=1,
    )
    rec = recommend_taxable_support(receipts)
    assert rec.can_support_taxable_edits is False
    assert any("no computable effective rate" in r for r in rec.reasons)


def test_multi_jurisdiction_single_distinct_rate_blocked():
    # A "multi-jurisdiction" merchant with only ONE distinct reconciled rate
    # would resolve as single-jurisdiction downstream and bypass the multi-rate
    # safeguard, so it is refused.
    receipts = ReceiptEvidence(
        taxable_flag="T",
        nontaxable_flags=("NF",),
        effective_rates=(Decimal("0.0838"), Decimal("0.0837")),
        receipt_count=3,
        taxed_receipt_count=3,
        multi_jurisdiction=True,
        per_receipt_rates_reconcile=True,
        reconciled_jurisdiction_rates=(Decimal("0.08375"),),
    )
    rec = recommend_taxable_support(receipts)
    assert rec.can_support_taxable_edits is False
    assert any("2 distinct reconciled" in r for r in rec.reasons)


# --------------------------------------------------------------------------- #
# Full artifact assembly
# --------------------------------------------------------------------------- #


def test_assemble_produces_round_trippable_artifact():
    web = WebEvidence(
        jurisdiction="CA-Ventura",
        published_rate=Decimal("0.0725"),
        urls=("https://cdtfa.ca.gov",),
    )
    places = PlacesEvidence(
        address="2734 Townsgate Rd, Westlake Village, CA 91361",
        store_id="2187",
        category="grocery",
        place_ids=("ChIJxyz",),
        jurisdictions=("CA-Ventura",),
    )
    intel = assemble_merchant_intelligence(
        "Vons",
        receipts=_vons_receipts(),
        web=web,
        places=places,
        generated_at=GEN_AT,
    )
    assert intel.slug == "vons"
    assert intel.tax.can_support_taxable_edits is True
    assert intel.tax.validated_rate == Decimal("0.0725")
    assert intel.details.address.startswith("2734 Townsgate")
    assert intel.tax.provenance  # non-empty evidence trail
    # Lossless JSON round-trip.
    assert MerchantIntelligence.from_dict(intel.to_dict()) == intel


def test_assemble_apostrophe_merchant_slug():
    intel = assemble_merchant_intelligence(
        "Gelson's",
        receipts=ReceiptEvidence(
            taxable_flag="T",
            nontaxable_flags=("F",),
            effective_rates=(Decimal("0.0724"),),
            receipt_count=4,
            taxed_receipt_count=1,
        ),
        generated_at=GEN_AT,
    )
    # Apostrophe collapses; mirrors merchant_tax_config._slug.
    assert intel.slug == "gelsons"
    assert intel.tax.can_support_taxable_edits is False
