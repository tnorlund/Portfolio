"""Per-merchant research: cross-check sources into a locked artifact.

This module is the **deterministic** assembly + cross-check core of the research
pipeline. It consumes already-fetched source evidence — receipts (ground truth),
a web sales-tax finding, and the Places cache — and produces a
:class:`~.schema.MerchantIntelligence` artifact with a conservative
``can_support_taxable_edits`` recommendation, a ``confidence`` grade, and
human-readable ``provenance`` strings.

Organizing principle (see CHARTER): **agents PRODUCE artifacts; deterministic
gates VALIDATE and CONSUME them.** The non-deterministic part — calling MCP
receipt-tools, web search, and the Places cache — lives in a thin driver that
feeds the evidence dataclasses below. Keeping the *decision* logic here, pure
and testable, means a low-confidence or contradictory source can never silently
flip the gate: the artifact only ever *recommends* support, and the downstream
deterministic gate (``merchant_taxable_edit_rate`` /
``_consistent_validated_edit_rate`` in ``merchant_synthesis``) independently
re-validates every run's observed rate before any taxable edit is produced.

The ``can_support_taxable_edits`` recommendation is intentionally CONSERVATIVE
and mirrors the hand-validated 8-merchant decisions:

* requires a taxable class flag AND at least ``MIN_TAXED_RECEIPTS`` taxed
  receipts (Gelson's, with a single taxed receipt, stays off — single-receipt
  arithmetic is not a cross-receipt confirmation);
* a single-jurisdiction merchant is supported only at an AUTHORITATIVE web rate
  that is (a) uncontradicted by any effective rate (effective rate is a one-sided
  lower bound, so a rate ABOVE the web rate signals a higher jurisdiction) and
  (b) CONFIRMED by at least one per-item observation snapping to it (so a
  wrong-but-plausible web lookup is rejected — the receipts' own taxable-item
  arithmetic must land on the web rate) — Vons / Sprouts / Amazon Fresh;
* a multi-jurisdiction merchant is supported ONLY when an explicit, justified
  ``per_receipt_rates_reconcile`` signal says each receipt's own rate snaps to a
  known jurisdiction rate (Target reconciled 12/12; Costco / Home Depot did NOT
  because their per-item flag OCR is too sparse, so they stay off);
* a merchant with no positive tax stays off (Smith's — NV grocery exemption);
* a caller may force support off with ``block_reason`` for a judgment the
  evidence alone cannot capture (e.g. per-item OCR sparseness); support is never
  forced *on* by an override — the evidence must justify it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence

from .schema import (
    CatalogEntry,
    MerchantDetails,
    MerchantIntelligence,
    TaxArtifact,
)

# At least this many taxed receipts must carry positive tax before a merchant
# can be cleared for taxable edits: a single taxed receipt is arithmetic, not a
# cross-receipt confirmation.
MIN_TAXED_RECEIPTS = 2

# A merchant's receipt effective rate (tax / subtotal) is a one-sided LOWER
# bound on its taxable-item rate: most baskets mix taxable goods with
# non-taxable food, so the effective rate reads BELOW the true rate (a basket
# that is 30% taxable food reads ~30% of the true rate). It therefore CANNOT
# cap the rate from below — only an authoritative web/jurisdiction lookup pins
# the rate. What receipts CAN do is VETO a web rate by contradiction: if an
# observed effective rate sits ABOVE the web rate (beyond coupon/rounding
# noise), the true rate exceeds the web lookup, so the merchant is in a
# higher-tax jurisdiction and must not be treated as single-jurisdiction at the
# web rate. This tolerance is the max an effective rate may EXCEED the web rate
# before that veto fires. 0.30pp absorbs coupon effects (tax on a pre-discount
# base over a post-discount subtotal) and cent rounding while still separating
# CA jurisdictions that differ by >=0.5pp.
EFFECTIVE_OVER_WEB_TOLERANCE = Decimal("0.0030")

# A researched web rate outside this plausible US sales-tax band is treated as a
# research error and rejected rather than trusted (a belt against a garbage
# lookup, since receipts — a lower bound — cannot cap an implausibly high rate).
MAX_PLAUSIBLE_RATE = Decimal("0.15")

# How close a per-receipt OBSERVED taxable-item rate (tax / sum of taxable items,
# computed by the smart receipt-reading layer) must be to the web rate to COUNT
# as confirming it. This is the receipt-side authority check: the web lookup
# only supplies a candidate rate; at least one clean per-item observation must
# snap to it, so a wrong-but-plausible web rate (e.g. 9.5% for a 7.25% store)
# is rejected because the receipts' own taxable-item arithmetic lands on 7.25%,
# not 9.5%. Tight, because a per-item observation is a direct rate measurement.
RATE_CONFIRM_TOLERANCE = Decimal("0.0015")


def _slug(name: str) -> str:
    """Normalize a merchant name to its artifact slug.

    Mirrors ``merchant_tax_config._slug`` so artifact filenames line up with the
    tax-gate lookup key. Apostrophes are dropped BEFORE splitting so "Gelson's"
    collapses to "gelsons", not "gelson_s".
    """
    import re

    cleaned = re.sub(r"['‘’ʼ`]", "", str(name or "").lower())
    return re.sub(r"[^a-z0-9]+", "_", cleaned).strip("_")


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return (sum(values, Decimal("0")) / Decimal(len(values))).quantize(
        Decimal("0.0001")
    )


@dataclass(frozen=True)
class ReceiptEvidence:
    """Receipt-derived tax facts for one merchant (ground truth).

    ``effective_rates`` are per-receipt tax/subtotal observations from summary
    anchors (reliable as a LOWER bound — see WEB_CORROBORATION_TOLERANCE; a
    mixed basket reads below the true taxable-item rate, only a near-all-taxable
    basket approaches it). ``taxed_receipt_count`` counts receipts with positive
    tax. ``per_receipt_rates_reconcile`` is the smart receipt-reading signal a
    caller sets True only when EACH taxed receipt's own arithmetic snaps to a
    known jurisdiction rate — the difference between Target (reconciles) and
    Costco/Home Depot (sparse per-item OCR, does not).
    """

    taxable_flag: str
    nontaxable_flags: tuple[str, ...]
    effective_rates: tuple[Decimal, ...] = ()
    receipt_count: int = 0
    taxed_receipt_count: int = 0
    image_ids: tuple[str, ...] = ()
    # True when multiple distinct jurisdictions are observed for this merchant.
    multi_jurisdiction: bool = False
    # See class docstring; only meaningful for multi-jurisdiction merchants.
    per_receipt_rates_reconcile: bool = False
    # The clean per-jurisdiction rates a multi-jurisdiction merchant operates at,
    # supplied by the smart per-receipt reconciliation (NOT derived from the
    # noisy effective rate). Empty for single-jurisdiction merchants.
    reconciled_jurisdiction_rates: tuple[Decimal, ...] = ()
    # Per-receipt OBSERVED taxable-item rates (tax / sum of taxable items) the
    # smart layer could compute on clean receipts. Unlike effective rate (a
    # lower bound over the whole subtotal), this is a direct rate measurement and
    # is the receipt-side CONFIRMATION that the authoritative web rate applies
    # here (see RATE_CONFIRM_TOLERANCE). Sparse by nature — one clean observation
    # is enough to confirm a single-jurisdiction rate.
    observed_taxable_rates: tuple[Decimal, ...] = ()
    # Receipts with positive tax but NO computable effective rate (no summary
    # anchors). They are invisible to the jurisdiction veto, so for a
    # multi-jurisdiction merchant any such "blind" receipt could secretly belong
    # to a higher jurisdiction — see recommend_taxable_support.
    blind_positive_tax_count: int = 0


@dataclass(frozen=True)
class WebEvidence:
    """Web-search corroboration of the jurisdiction's published sales-tax rate."""

    jurisdiction: str = ""
    published_rate: Decimal | None = None
    urls: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class PlacesEvidence:
    """Store-identity facts from the ``receipt_places`` cache (no new API calls)."""

    address: str | None = None
    store_id: str | None = None
    category: str = "unknown"
    place_ids: tuple[str, ...] = ()
    jurisdictions: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaxRecommendation:
    """Deterministic recommendation derived from the receipt + web evidence."""

    can_support_taxable_edits: bool
    confidence: str  # "high" | "medium" | "low"
    validated_rate: Decimal | None
    jurisdiction_rates: tuple[Decimal, ...]
    reasons: tuple[str, ...]


def recommend_taxable_support(
    receipts: ReceiptEvidence,
    web: WebEvidence | None = None,
    *,
    block_reason: str | None = None,
) -> TaxRecommendation:
    """Conservatively decide whether a merchant can support taxable edits.

    Returns the recommendation plus the rate(s) and the reasons behind it. This
    is necessary-but-not-sufficient: the downstream deterministic gate still
    re-validates each run's observed rate, so a generous recommendation here
    cannot by itself produce a wrong-rate taxable edit.
    """
    reasons: list[str] = []
    rates = tuple(r for r in receipts.effective_rates if r > Decimal("0"))
    max_effective = max(rates) if rates else None

    has_flag = bool(receipts.taxable_flag)
    enough_taxed = receipts.taxed_receipt_count >= MIN_TAXED_RECEIPTS
    web_rate = web.published_rate if web else None

    # --- Cross-check: is the AUTHORITATIVE web rate consistent with receipts? - #
    # Effective rate is a one-sided LOWER bound, so receipts can only VETO the
    # web rate (when an observed effective rate EXCEEDS it => higher jurisdiction)
    # — they cannot confirm it from below. Corroboration therefore requires: a
    # plausible web rate, at least one computable effective observation (so we
    # actually saw tax of the right order), and NO effective rate exceeding the
    # web rate beyond coupon/rounding noise.
    web_plausible = web_rate is not None and Decimal("0") < web_rate <= MAX_PLAUSIBLE_RATE
    web_corroborates = (
        web_plausible
        and max_effective is not None
        and max_effective <= web_rate + EFFECTIVE_OVER_WEB_TOLERANCE
    )

    # Receipt-side AUTHORITY check: a web rate is only adopted when at least one
    # clean per-item observation snaps to it. This rejects a wrong-but-plausible
    # web lookup (e.g. 9.5% for a 7.25% store) that the lower-bound effective
    # rates cannot contradict — the receipts' own taxable-item arithmetic must
    # actually land on the web rate.
    rate_confirmed = web_rate is not None and any(
        abs(r - web_rate) <= RATE_CONFIRM_TOLERANCE
        for r in receipts.observed_taxable_rates
    )

    distinct_reconciled = tuple(sorted(set(receipts.reconciled_jurisdiction_rates)))

    # --- Hard blocks (any one keeps support OFF) ----------------------------- #
    if block_reason:
        reasons.append(f"blocked: {block_reason}")
    if not has_flag:
        reasons.append("no taxable class flag observed on receipts")
    if receipts.taxed_receipt_count == 0:
        reasons.append("no receipt carries positive tax")
    elif not enough_taxed:
        reasons.append(
            f"only {receipts.taxed_receipt_count} taxed receipt(s); "
            f"need >= {MIN_TAXED_RECEIPTS} for cross-receipt confirmation"
        )
    if receipts.multi_jurisdiction:
        if not receipts.per_receipt_rates_reconcile:
            reasons.append(
                "multi-jurisdiction merchant without per-receipt rate "
                "reconciliation (per-item flag OCR too sparse to pin a "
                "jurisdiction per receipt)"
            )
        if len(distinct_reconciled) < 2:
            # A "multi-jurisdiction" merchant with <2 distinct reconciled rates
            # would resolve as single-jurisdiction downstream (allowed_rates()
            # length 1), bypassing the per-run multi-rate safeguard. Refuse it.
            reasons.append(
                f"multi-jurisdiction merchant needs >= 2 distinct reconciled "
                f"rates; got {list(distinct_reconciled)}"
            )
    else:
        # Single-jurisdiction support requires the AUTHORITATIVE web rate to be
        # (a) plausible, (b) uncontradicted by any effective rate (no higher
        # jurisdiction), and (c) CONFIRMED by at least one per-item observation
        # snapping to it. (c) is the authority check: the noisy lower-bound
        # effective rates alone cannot pin the rate the downstream gate applies
        # directly, and a wrong-but-plausible web lookup must not be trusted on
        # web evidence alone. (Blind positive-tax receipts do not undermine a
        # confirmed single-jurisdiction rate — there is only one jurisdiction,
        # established by the single-store determination the caller sets.)
        if web_rate is None:
            reasons.append("no authoritative web rate to pin the single-jurisdiction rate")
        elif not web_plausible:
            reasons.append(
                f"web rate {web_rate} outside plausible band (0, {MAX_PLAUSIBLE_RATE}]"
            )
        elif max_effective is None:
            reasons.append(
                "no computable effective rate to confirm tax magnitude"
            )
        elif not web_corroborates:
            reasons.append(
                f"web rate {web_rate} contradicted: observed effective rate "
                f"{max_effective} exceeds it by > {EFFECTIVE_OVER_WEB_TOLERANCE} "
                f"(higher-tax jurisdiction)"
            )
        elif not rate_confirmed:
            reasons.append(
                f"web rate {web_rate} not confirmed by any per-item observation "
                f"(within {RATE_CONFIRM_TOLERANCE}); observed taxable rates "
                f"{[str(r) for r in receipts.observed_taxable_rates]}"
            )

    blocked = bool(reasons)

    # --- Rates --------------------------------------------------------------- #
    validated_rate: Decimal | None = None
    jurisdiction_rates: tuple[Decimal, ...] = ()
    if receipts.multi_jurisdiction:
        # Clean per-jurisdiction rates come from per-receipt reconciliation, NOT
        # from the noisy effective rates. The downstream gate makes a run's
        # observed rate snap to one of these.
        jurisdiction_rates = distinct_reconciled
    elif web_corroborates and rate_confirmed:
        # Authoritative clean rate (e.g. 7.25%), never the noisy effective rate,
        # and only once a per-item observation has confirmed it applies here.
        validated_rate = web_rate

    can_support = not blocked and has_flag and enough_taxed
    if receipts.multi_jurisdiction:
        can_support = (
            can_support
            and receipts.per_receipt_rates_reconcile
            and len(jurisdiction_rates) >= 2
        )
    else:
        can_support = (
            can_support
            and web_corroborates
            and rate_confirmed
            and validated_rate is not None
        )

    # --- Confidence ---------------------------------------------------------- #
    if not can_support:
        # "high" only when we are confidently OFF for a structural reason (no
        # tax at all, e.g. a grocery-exempt jurisdiction); otherwise medium.
        confidence = "high" if receipts.taxed_receipt_count == 0 else "medium"
        if receipts.taxed_receipt_count == 0:
            reasons.append("confidently off: no positive tax to derive a rate")
    elif receipts.multi_jurisdiction:
        # Multi-jurisdiction confidence rests on per-receipt reconciliation
        # (already required for can_support), not on a single web rate. Blind
        # positive-tax receipts (positive tax, no computable effective rate) can
        # hide an unobserved jurisdiction; we do NOT hard-block the capability
        # over them — that is the per-run gate's job (merchant_synthesis'
        # _has_unobserved_positive_tax_receipt skips taxable edits for any RUN
        # containing one) — but we cap confidence at medium and record them so a
        # reviewer sees the caveat.
        if receipts.blind_positive_tax_count > 0:
            confidence = "medium"
            reasons.append(
                f"{receipts.blind_positive_tax_count} blind positive-tax "
                f"receipt(s) (no computable effective rate); per-run gate must "
                f"exclude any run containing one"
            )
        else:
            confidence = "high" if receipts.taxed_receipt_count >= 3 else "medium"
        reasons.append(
            f"per-receipt reconciliation across {receipts.taxed_receipt_count} "
            f"taxed receipts to jurisdiction rates {list(jurisdiction_rates)}"
        )
    elif receipts.taxed_receipt_count >= 3:
        confidence = "high"
        reasons.append(
            f"web rate {web_rate} uncontradicted by {receipts.taxed_receipt_count} "
            f"taxed receipts (highest effective {max_effective})"
        )
    else:
        confidence = "medium"
        reasons.append(
            f"web rate {web_rate} uncontradicted, but only "
            f"{receipts.taxed_receipt_count} taxed receipts (highest effective "
            f"{max_effective})"
        )

    return TaxRecommendation(
        can_support_taxable_edits=can_support,
        confidence=confidence,
        validated_rate=validated_rate,
        jurisdiction_rates=jurisdiction_rates,
        reasons=tuple(reasons),
    )


def _build_provenance(
    receipts: ReceiptEvidence,
    web: WebEvidence | None,
    places: PlacesEvidence | None,
    recommendation: TaxRecommendation,
) -> tuple[str, ...]:
    prov: list[str] = []
    if receipts.receipt_count:
        rate_note = ""
        if receipts.effective_rates:
            mean = _mean(receipts.effective_rates)
            rate_note = f", effective rate ~{mean}"
        prov.append(
            f"receipts: {receipts.receipt_count} receipt(s), "
            f"{receipts.taxed_receipt_count} taxed{rate_note}"
        )
    if web is not None and web.published_rate is not None:
        prov.append(
            f"web: {web.jurisdiction or 'jurisdiction'} published rate "
            f"{web.published_rate}"
            + (f" ({web.note})" if web.note else "")
        )
    elif web is not None and web.note:
        prov.append(f"web: {web.note}")
    if places is not None and (places.address or places.place_ids):
        loc = places.address or ", ".join(places.jurisdictions)
        prov.append(f"places: {loc}".strip())
    # Always append the decisive reasons so a reviewer can see WHY.
    prov.extend(recommendation.reasons)
    return tuple(prov)


def assemble_merchant_intelligence(
    merchant_name: str,
    *,
    receipts: ReceiptEvidence,
    web: WebEvidence | None = None,
    places: PlacesEvidence | None = None,
    catalog: Sequence[CatalogEntry] = (),
    generated_at: str,
    block_reason: str | None = None,
) -> MerchantIntelligence:
    """Cross-check the three sources into a locked MerchantIntelligence artifact.

    ``generated_at`` is passed in (not stamped here) so callers control the
    timestamp and the assembly stays deterministic / replayable.
    """
    recommendation = recommend_taxable_support(
        receipts, web, block_reason=block_reason
    )
    jurisdiction = (
        "multi"
        if receipts.multi_jurisdiction
        else (web.jurisdiction if web else "")
        or (places.jurisdictions[0] if places and places.jurisdictions else "")
    )
    provenance = _build_provenance(receipts, web, places, recommendation)

    tax = TaxArtifact(
        taxable_flag=receipts.taxable_flag,
        nontaxable_flags=receipts.nontaxable_flags,
        jurisdiction=jurisdiction,
        validated_rate=recommendation.validated_rate,
        jurisdiction_rates=recommendation.jurisdiction_rates,
        can_support_taxable_edits=recommendation.can_support_taxable_edits,
        confidence=recommendation.confidence,
        provenance=provenance,
    )
    details = MerchantDetails(
        address=(places.address if places else None),
        store_id=(places.store_id if places else None),
        category=(places.category if places else "unknown"),
    )
    sources: dict = {}
    if receipts.receipt_count:
        sources["receipts"] = {
            "count": receipts.receipt_count,
            "taxed_count": receipts.taxed_receipt_count,
            "image_ids": list(receipts.image_ids),
        }
    if web is not None:
        sources["web"] = {
            "jurisdiction": web.jurisdiction,
            "published_rate": str(web.published_rate)
            if web.published_rate is not None
            else None,
            "urls": list(web.urls),
        }
    if places is not None:
        sources["places"] = {"place_ids": list(places.place_ids)}

    return MerchantIntelligence(
        merchant=merchant_name,
        slug=_slug(merchant_name),
        tax=tax,
        catalog=tuple(catalog),
        details=details,
        generated_at=generated_at,
        sources=sources,
    )
