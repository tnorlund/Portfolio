"""Tests for the merchant intelligence schema, loader, and tax-config hook.

Milestone 1 lock: the artifact schema round-trips losslessly, the loader is
read-only and never raises on missing/malformed files, and the tax-config
lookup prefers an artifact when present but always falls back to the hardcoded
``MERCHANT_TAX_PROFILES`` dict.
"""

from decimal import Decimal

import pytest

from receipt_agent.agents.label_evaluator import merchant_tax_config
from receipt_agent.agents.label_evaluator.merchant_research import (
    CatalogEntry,
    MerchantDetails,
    MerchantIntelligence,
    TaxArtifact,
)
from receipt_agent.agents.label_evaluator.merchant_research import loader


def _sample_intel(
    *,
    slug: str = "vons",
    merchant: str = "Vons",
    can_support: bool = True,
    validated_rate: str | None = "0.0725",
    jurisdiction_rates: tuple[str, ...] = (),
) -> MerchantIntelligence:
    return MerchantIntelligence(
        merchant=merchant,
        slug=slug,
        tax=TaxArtifact(
            taxable_flag="T",
            nontaxable_flags=("S",),
            jurisdiction="CA-Ventura",
            validated_rate=Decimal(validated_rate) if validated_rate else None,
            jurisdiction_rates=tuple(Decimal(r) for r in jurisdiction_rates),
            can_support_taxable_edits=can_support,
            confidence="high",
            provenance=("receipts: 5 receipts at 7.25%", "web: CA Ventura 7.25%"),
        ),
        catalog=(
            CatalogEntry(
                name="SPROUTS ORG EGGS 12CT",
                price=Decimal("4.19"),
                taxable=False,
                source="receipt",
            ),
        ),
        details=MerchantDetails(
            address="2734 Townsgate Rd, Westlake Village, CA 91361",
            store_id="2187",
            category="grocery",
        ),
        generated_at="2026-06-26T00:00:00+00:00",
        sources={"receipts": {"count": 5, "image_ids": ["abc"]}},
    )


# --------------------------------------------------------------------------- #
# Schema round-trip
# --------------------------------------------------------------------------- #


def test_schema_round_trips_losslessly():
    intel = _sample_intel(jurisdiction_rates=("0.08375", "0.095"))
    restored = MerchantIntelligence.from_dict(intel.to_dict())
    assert restored == intel
    # Decimals survive the JSON float round-trip at validated precision.
    assert restored.tax.validated_rate == Decimal("0.0725")
    assert restored.tax.jurisdiction_rates == (Decimal("0.08375"), Decimal("0.095"))
    assert restored.catalog[0].price == Decimal("4.19")


def test_to_dict_emits_lossless_decimal_strings():
    d = _sample_intel(jurisdiction_rates=("0.08375",)).to_dict()
    # rates/prices serialize as STRINGS so Decimal round-trips losslessly.
    assert d["tax"]["validated_rate"] == "0.0725"
    assert d["tax"]["jurisdiction_rates"] == ["0.08375"]
    assert d["catalog"][0]["price"] == "4.19"
    import json

    json.dumps(d)  # must not raise


def test_string_rates_round_trip_to_exact_decimal():
    # An awkward rate that is NOT exactly representable as a short float still
    # round-trips exactly because it is stored as a string.
    awkward = "0.0123456789"
    intel = _sample_intel(validated_rate=awkward)
    restored = MerchantIntelligence.from_dict(intel.to_dict())
    assert restored.tax.validated_rate == Decimal(awkward)


def test_from_dict_tolerates_missing_optional_fields():
    intel = MerchantIntelligence.from_dict({"merchant": "X", "slug": "x"})
    assert intel.merchant == "X"
    assert intel.tax.validated_rate is None
    assert intel.tax.can_support_taxable_edits is False
    assert intel.catalog == ()
    assert intel.tax.confidence == "low"


# --------------------------------------------------------------------------- #
# Loader: read-only, never raises
# --------------------------------------------------------------------------- #


def test_loader_returns_none_for_missing_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    assert loader.load_merchant_intelligence("does_not_exist") is None
    assert loader.artifact_tax_profile("does_not_exist") is None
    assert loader.list_available_slugs() == []


def test_loader_returns_none_on_malformed_json(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    (tmp_path / "broken.json").write_text("{ not valid json", encoding="utf-8")
    # Logs a warning, returns None — does not raise.
    assert loader.load_merchant_intelligence("broken") is None
    assert loader.artifact_tax_profile("broken") is None


def test_loader_rejects_slug_mismatch(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    # vons.json that internally claims to be "target" must be rejected so it
    # cannot mis-resolve Vons to Target's tax facts.
    intel = _sample_intel(slug="target", merchant="Target")
    (tmp_path / "vons.json").write_text(json.dumps(intel.to_dict()), encoding="utf-8")
    assert loader.load_merchant_intelligence("vons") is None
    assert loader.artifact_tax_profile("vons") is None


def test_loader_round_trips_written_artifact(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    intel = _sample_intel()
    (tmp_path / "vons.json").write_text(json.dumps(intel.to_dict()), encoding="utf-8")
    loaded = loader.load_merchant_intelligence("vons")
    assert loaded == intel
    assert loader.list_available_slugs() == ["vons"]

    tax = loader.artifact_tax_profile("vons")
    assert tax["merchant"] == "Vons"
    assert tax["taxable_flags"] == frozenset({"T"})
    assert tax["nontaxable_flags"] == frozenset({"S"})
    assert tax["validated_rate"] == Decimal("0.0725")
    assert tax["can_support_taxable_edits"] is True


# --------------------------------------------------------------------------- #
# tax_config: artifact precedence + hardcoded fallback
# --------------------------------------------------------------------------- #


def test_tax_config_falls_back_to_hardcoded_when_no_artifact(tmp_path, monkeypatch):
    # Empty artifact dir → the hardcoded validated profile is used unchanged.
    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    merchant_tax_config.merchant_tax_profile.cache_clear()
    profile = merchant_tax_config.merchant_tax_profile("Vons")
    assert profile is not None
    assert profile.merchant == "Vons"
    assert profile.validated_rate == Decimal("0.0725")
    assert profile.can_support_taxable_edits is True


def test_tax_config_prefers_artifact_over_hardcoded(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    # An artifact that DISABLES edits must override the hardcoded enabled Vons.
    intel = _sample_intel(can_support=False)
    (tmp_path / "vons.json").write_text(json.dumps(intel.to_dict()), encoding="utf-8")
    merchant_tax_config.merchant_tax_profile.cache_clear()
    try:
        profile = merchant_tax_config.merchant_tax_profile("Vons")
        assert profile is not None
        assert profile.can_support_taxable_edits is False
        assert profile.validated_rate == Decimal("0.0725")
    finally:
        # Don't leak the patched artifact into other tests via the cache.
        merchant_tax_config.merchant_tax_profile.cache_clear()


def test_tax_config_ignores_malformed_artifact_and_uses_hardcoded(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(loader, "_ARTIFACT_DIR", tmp_path)
    (tmp_path / "vons.json").write_text("{ broken", encoding="utf-8")
    merchant_tax_config.merchant_tax_profile.cache_clear()
    try:
        profile = merchant_tax_config.merchant_tax_profile("Vons")
        assert profile is not None
        # Fallback to hardcoded — still the validated enabled profile.
        assert profile.can_support_taxable_edits is True
    finally:
        merchant_tax_config.merchant_tax_profile.cache_clear()
