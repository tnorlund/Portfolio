"""Receipt-validated per-merchant tax configuration.

This module records, per merchant, the tax-class flags and sales-tax rate(s)
that were *validated against the merchant's own real receipts* — not inferred
from a single run's geometry. It is the source of truth that gates **taxable
item edits** (add/remove of a taxable line, which must recompute TAX):

  * ``taxable_flags`` / ``nontaxable_flags`` — the exact 1-2 char trailing
    class letters that merchant prints (e.g. Vons ``T``/``S``, Target
    ``T``/``NF``, Costco ``A``). Scoped per merchant because the same letter
    means different things across merchants and a global allow-list of letters
    would mis-class items (a global ``A`` would flag countless product words).
  * ``validated_rate`` — a single jurisdiction-pinned taxable-item rate when the
    merchant operates in one taxing jurisdiction.
  * ``jurisdiction_rates`` — the set of plausible rates when the merchant spans
    several jurisdictions (Target: NV 8.375% vs CA ~9.5%). A run's observed
    rate must match one of these to be trusted.
  * ``can_support_taxable_edits`` — the hard gate. ``True`` only when receipts
    show BOTH a flag that reliably separates taxable items from food AND a
    stable per-item rate the model can apply when recomputing tax. Costco and
    Home Depot are receipt-stable at the *receipt* level but their per-item
    flag OCR is too sparse to trust, so they stay ``False``.

The validation that produced these values came from a multi-agent workflow that
read each merchant's real receipts (see the tax-profile workflow). Re-run that
workflow and update these values if the corpus or OCR coverage changes; do not
hand-tune a merchant ON without receipt evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache


# A run's observed taxable-item rate must land within this of a validated rate
# to be trusted. Kept BELOW half the closest validated-rate separation so a
# single observation can never sit inside two rates' tolerance bands at once:
# Target's CA rates 9.50% and 9.75% are 0.25pp apart, so 0.10pp keeps their
# bands ([9.40,9.60] / [9.65,9.85]) disjoint. Clean per-receipt rates reconcile
# exactly, and validated grocer medians cluster within ~0.05pp of 7.25%, so this
# still absorbs real OCR noise. An observation that matches two validated rates
# is rejected outright (see merchant_taxable_edit_rate) as a second guard.
RATE_MATCH_TOLERANCE = Decimal("0.0010")


@dataclass(frozen=True)
class MerchantTaxProfile:
    """Receipt-validated tax facts for one merchant."""

    merchant: str
    taxable_flags: frozenset[str]
    nontaxable_flags: frozenset[str]
    can_support_taxable_edits: bool
    confidence: str
    # Exactly one of these carries the rate(s) for an edit-enabled merchant.
    validated_rate: Decimal | None = None
    jurisdiction_rates: tuple[Decimal, ...] = field(default_factory=tuple)
    notes: str = ""

    def allowed_rates(self) -> tuple[Decimal, ...]:
        rates: list[Decimal] = []
        if self.validated_rate is not None:
            rates.append(self.validated_rate)
        rates.extend(self.jurisdiction_rates)
        return tuple(rates)


# Keyed by the merchant slug (see ``_slug``). Values are the validated profiles
# from reading each merchant's real receipts. Merchants absent from this map get
# no taxable-edit support (conservative default).
MERCHANT_TAX_PROFILES: dict[str, MerchantTaxProfile] = {
    "vons": MerchantTaxProfile(
        merchant="Vons",
        taxable_flags=frozenset({"T"}),
        nontaxable_flags=frozenset({"S"}),
        validated_rate=Decimal("0.0725"),
        can_support_taxable_edits=True,
        confidence="high",
        notes=(
            "T validated 7.25-7.30% on 5 receipts (median 0.0726, stdev "
            "0.0005); single CA (Ventura County) store; S confirmed non-taxable."
        ),
    ),
    "sprouts_farmers_market": MerchantTaxProfile(
        merchant="Sprouts Farmers Market",
        taxable_flags=frozenset({"T"}),
        nontaxable_flags=frozenset({"F"}),
        validated_rate=Decimal("0.0725"),
        can_support_taxable_edits=True,
        confidence="medium",
        notes=(
            "T median 7.25% (stdev 0.03pp) across 5 clean receipts; effective "
            "tax/subtotal 7.22-7.26%; F confirmed non-taxable food."
        ),
    ),
    "amazon_fresh": MerchantTaxProfile(
        merchant="Amazon Fresh",
        taxable_flags=frozenset({"T"}),
        nontaxable_flags=frozenset({"F"}),
        validated_rate=Decimal("0.0725"),
        can_support_taxable_edits=True,
        confidence="medium",
        notes=(
            "T validated 7.23-7.25% on the taxed receipts; tax applies only to "
            "the taxable item, not food; single CA (Ventura County) store."
        ),
    ),
    "target": MerchantTaxProfile(
        merchant="Target",
        taxable_flags=frozenset({"T"}),
        nontaxable_flags=frozenset({"NF"}),
        jurisdiction_rates=(
            Decimal("0.08375"),  # NV (Clark County)
            Decimal("0.0950"),  # CA (Westlake Village / LA County)
            Decimal("0.0975"),  # CA (some Westlake Village receipts)
        ),
        can_support_taxable_edits=True,
        confidence="high",
        notes=(
            "Two-state merchant. base*printed_rate==tax reconciled 12/12 within "
            "$0.02. Rate is per-state (NV 8.375% vs CA ~9.5%); a run's observed "
            "rate must match one jurisdiction. NF confirmed non-taxable food."
        ),
    ),
    "costco_wholesale": MerchantTaxProfile(
        merchant="Costco Wholesale",
        taxable_flags=frozenset({"A"}),
        nontaxable_flags=frozenset(),
        can_support_taxable_edits=False,
        confidence="medium",
        notes=(
            "Multi-jurisdiction (8.375%/9.5%/9.75%) AND OCR drops some A-flagged "
            "items, so the implied per-flag rate does not cluster. Receipt-level "
            "stable, per-item not trustable. OFF until A-flag OCR improves."
        ),
    ),
    "the_home_depot": MerchantTaxProfile(
        merchant="The Home Depot",
        taxable_flags=frozenset({"A"}),
        nontaxable_flags=frozenset(),
        can_support_taxable_edits=False,
        confidence="medium",
        notes=(
            "Receipt-level rate stable per store (9.50% West Hills / 7.25% "
            "Thousand Oaks) but only ~4 A line totals captured corpus-wide; "
            "per-item flag OCR too sparse. OFF until per-item coverage improves."
        ),
    ),
    "gelsons": MerchantTaxProfile(
        merchant="Gelson's",
        taxable_flags=frozenset({"T"}),
        nontaxable_flags=frozenset({"F"}),
        validated_rate=Decimal("0.0725"),
        can_support_taxable_edits=False,
        confidence="medium",
        notes=(
            "T validates to 0.0724 but only ONE physical receipt carries tax — "
            "single-receipt arithmetic, not a cross-receipt cluster. OFF until a "
            "second taxed receipt lands (data-volume gate, not a config gap)."
        ),
    ),
    "smiths": MerchantTaxProfile(
        merchant="Smith's",
        taxable_flags=frozenset(),
        nontaxable_flags=frozenset({"F"}),
        can_support_taxable_edits=False,
        confidence="high",
        notes=(
            "Nevada exempts groceries; every TAX row is 0.00 and no T flag "
            "exists. No positive tax to derive a rate from. Correctly OFF."
        ),
    ),
}


def _slug(name: str) -> str:
    # Drop apostrophes/quotes BEFORE splitting on non-alphanumerics so "Gelson's"
    # and "Smith's" collapse to "gelsons"/"smiths" rather than "gelson_s".
    cleaned = re.sub(r"['‘’ʼ`]", "", str(name or "").lower())
    return re.sub(r"[^a-z0-9]+", "_", cleaned).strip("_")


@lru_cache(maxsize=256)
def merchant_tax_profile(merchant_name: str) -> MerchantTaxProfile | None:
    """Return the validated tax profile for ``merchant_name``, or ``None``.

    Matches on the normalized slug, then on a brand-prefix so store-specific
    names ("Gelson's Westlake Village", "Sprouts Farmers Market #123") resolve
    to their brand profile.
    """
    slug = _slug(merchant_name)
    if not slug:
        return None
    profile = MERCHANT_TAX_PROFILES.get(slug)
    if profile is not None:
        return profile
    # Brand-prefix match on a TOKEN boundary only: a store-specific name extends
    # the configured brand slug with a "_<suffix>" ("gelsons_westlake_village",
    # "sprouts_farmers_market_123"). Requiring the "_" separator means a merchant
    # like "Targeted Coupons" / "Amazon Freshly" / "Vonsmart" can NOT collide
    # with an enabled profile — a false enable is the dangerous direction here.
    # Pick the longest matching brand key so the most specific config wins.
    best: MerchantTaxProfile | None = None
    best_len = 0
    for key, candidate in MERCHANT_TAX_PROFILES.items():
        if slug.startswith(key + "_") and len(key) > best_len:
            best, best_len = candidate, len(key)
    return best


def merchant_supports_taxable_edits(merchant_name: str) -> bool:
    """True only when this merchant is receipt-validated for taxable edits."""
    profile = merchant_tax_profile(merchant_name)
    return bool(profile and profile.can_support_taxable_edits)


def merchant_taxable_edit_rate(
    merchant_name: str,
    observed_rate: Decimal | None,
    *,
    tolerance: Decimal = RATE_MATCH_TOLERANCE,
) -> Decimal | None:
    """Validated rate to use for a taxable edit, or ``None`` to disallow it.

    Gates on the receipt-validated config: the merchant must be cleared for
    taxable edits AND the run's ``observed_rate`` (the per-run taxable-item rate
    derived from this merchant's receipts) must match one of its validated
    rate(s). Returns the *validated* rate (ground truth) so a slightly noisy
    observation snaps to the verified value rather than propagating OCR drift
    into recomputed tax. Returns ``None`` for any unvalidated merchant, which
    keeps taxable edits off for everyone outside the cleared set.
    """
    profile = merchant_tax_profile(merchant_name)
    if profile is None or not profile.can_support_taxable_edits:
        return None
    if observed_rate is None:
        return None
    matches = [
        rate
        for rate in profile.allowed_rates()
        if abs(rate - observed_rate) <= tolerance
    ]
    # No match, or an ambiguous one between two validated jurisdiction rates:
    # disallow rather than guess. A batch median that lands between (say) two CA
    # rates means the run mixes jurisdictions — snapping all edits to one rate
    # would corrupt the others, so the safe outcome is no taxable edit.
    if len(matches) != 1:
        return None
    return matches[0]
