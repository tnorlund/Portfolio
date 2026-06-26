"""Tests for the receipt-validated per-merchant tax config."""

from decimal import Decimal

import pytest

from receipt_agent.agents.label_evaluator.merchant_tax_config import (
    MERCHANT_TAX_PROFILES,
    merchant_supports_taxable_edits,
    merchant_tax_profile,
    merchant_taxable_edit_rate,
)


def test_only_receipt_validated_merchants_support_taxable_edits():
    enabled = {
        slug
        for slug, profile in MERCHANT_TAX_PROFILES.items()
        if profile.can_support_taxable_edits
    }
    # Exactly the four merchants the receipt-reading workflow validated.
    assert enabled == {"vons", "sprouts_farmers_market", "amazon_fresh", "target"}


@pytest.mark.parametrize(
    "name",
    [
        "Vons",
        "VONS",
        "Sprouts Farmers Market",
        "Sprouts Farmers Market #123",
        "Amazon Fresh",
        "Target",
    ],
)
def test_validated_merchants_resolve_and_support_edits(name):
    assert merchant_supports_taxable_edits(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "Costco Wholesale",
        "The Home Depot",
        "Gelson's Westlake Village",
        "Smith's",
        "Some Unknown Bodega",
        "",
    ],
)
def test_unvalidated_merchants_do_not_support_edits(name):
    assert merchant_supports_taxable_edits(name) is False


def test_apostrophe_merchant_slugs_resolve():
    # "Gelson's"/"Smith's" must collapse to gelsons/smiths, not gelson_s.
    assert merchant_tax_profile("Gelson's Westlake Village").merchant == "Gelson's"
    assert merchant_tax_profile("Smith's").merchant == "Smith's"


def test_edit_rate_snaps_observed_to_validated():
    # A slightly noisy observation within tolerance snaps to the validated rate.
    assert merchant_taxable_edit_rate("Vons", Decimal("0.0726")) == Decimal("0.0725")
    assert merchant_taxable_edit_rate("Sprouts Farmers Market #9", Decimal("0.0722")) == (
        Decimal("0.0725")
    )


def test_edit_rate_rejects_off_jurisdiction_observation():
    # An observed rate far from any validated rate is rejected (no edit).
    assert merchant_taxable_edit_rate("Vons", Decimal("0.0850")) is None


def test_target_rate_is_keyed_per_jurisdiction():
    # Two-state merchant: the observation selects the matching jurisdiction rate.
    assert merchant_taxable_edit_rate("Target", Decimal("0.0840")) == Decimal("0.08375")
    assert merchant_taxable_edit_rate("Target", Decimal("0.0948")) == Decimal("0.0950")
    assert merchant_taxable_edit_rate("Target", Decimal("0.0973")) == Decimal("0.0975")
    # A rate that matches no jurisdiction is rejected.
    assert merchant_taxable_edit_rate("Target", Decimal("0.0725")) is None


def test_unvalidated_merchant_never_returns_a_rate():
    # Even at a plausible rate, an OFF merchant gets no edit rate.
    assert merchant_taxable_edit_rate("Costco Wholesale", Decimal("0.0840")) is None
    assert merchant_taxable_edit_rate("The Home Depot", Decimal("0.0725")) is None
    assert merchant_taxable_edit_rate("Unknown Merchant", Decimal("0.0725")) is None


def test_edit_rate_requires_an_observation():
    assert merchant_taxable_edit_rate("Vons", None) is None


@pytest.mark.parametrize(
    "name",
    [
        "Targeted Coupons",  # "targeted_..." has no "target_" boundary
        "Amazon Freshly",  # "amazon_freshly" != "amazon_fresh_..."
        "Vonsmart",  # "vonsmart" has no "vons_" boundary
    ],
)
def test_brand_prefix_requires_a_token_boundary(name):
    # A merchant that merely shares a leading substring with a validated brand
    # must not resolve to it — a false ENABLE is the dangerous direction.
    assert merchant_tax_profile(name) is None
    assert merchant_supports_taxable_edits(name) is False


def test_token_boundary_match_to_an_off_brand_still_blocks_edits():
    # A location-suffixed name CAN match an OFF brand on the "_" boundary, but
    # that brand is not cleared for edits, so the safety property still holds.
    assert merchant_supports_taxable_edits("Smith's Food and Drug #455") is False


def test_ambiguous_observation_between_two_jurisdiction_rates_is_rejected():
    # A Target batch median landing between CA 9.50% and 9.75% can't be safely
    # snapped to one rate -> no edit. (Bands are disjoint under the tightened
    # tolerance, so 0.0962 matches neither and is rejected.)
    assert merchant_taxable_edit_rate("Target", Decimal("0.0962")) is None
