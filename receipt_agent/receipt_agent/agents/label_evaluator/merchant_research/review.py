"""Deterministic human-approval review status for merchant intelligence.

Every artifact carries a review status that decides whether its tax intelligence
is allowed to ENABLE taxable edits / parameterize synthesis. The status is
computed **deterministically from the artifact** (never read from an editable
JSON field), so the gate cannot be tricked by hand-editing a file:

* ``auto_approved`` — safe to use without a human. Requires HIGH confidence AND
  at least ``MIN_INDEPENDENT_SOURCES`` independent sources cross-checking the tax
  facts AND (the tax facts match an existing validated config OR it is a
  single-jurisdiction merchant with at least ``MIN_CORROBORATING_TAXED`` taxed
  receipts).
* ``needs_review`` — generated but PARKED: medium confidence, a new
  merchant/jurisdiction, too few sources, multi-jurisdiction, or too few taxed
  receipts. It does not enable anything until a human approves it.
* ``rejected`` — sources contradict each other / the tax evidence is unreliable,
  or there is no tax evidence at all.

A human sign-off (stored separately, keyed by the content hash of the tax block)
lifts ``needs_review`` to ``approved`` — see ``loader.effective_review``.
``rejected`` is a hard stop: it carries no usable rate, so there is nothing to
enable.

This module is intentionally PURE — it imports nothing from the tax gate — so the
status is trivially testable and free of import cycles. The one external input,
``matches_validated_config``, is computed by the caller (which may read the
hardcoded config) and passed in.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from .schema import MerchantIntelligence

# Status constants (also the values written into the artifact JSON).
AUTO_APPROVED = "auto_approved"
NEEDS_REVIEW = "needs_review"
REJECTED = "rejected"
APPROVED = "approved"  # a human lifted needs_review (set only by the loader)

ENABLING_STATUSES = frozenset({AUTO_APPROVED, APPROVED})

# Auto-approval thresholds.
MIN_INDEPENDENT_SOURCES = 2
MIN_CORROBORATING_TAXED = 3

# Provenance markers for a contradiction the research itself found. Matched on
# WORD BOUNDARIES so the positive evidence string "uncontradicted by N receipts"
# (emitted for an enabled merchant) never trips the "contradicted" marker.
_CONTRADICTION_RE = re.compile(r"\b(?:contradicted|mislabeled)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ReviewBlock:
    """The review verdict for one artifact."""

    status: str
    reasons: tuple[str, ...]
    approved_by: str | None = None
    approved_at: str | None = None

    def is_enabling(self) -> bool:
        """True only when this status is allowed to enable taxable edits."""
        return self.status in ENABLING_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReviewBlock:
        return cls(
            status=str(d.get("status") or NEEDS_REVIEW),
            reasons=tuple(str(r) for r in (d.get("reasons") or [])),
            approved_by=d.get("approved_by") or None,
            approved_at=d.get("approved_at") or None,
        )


def tax_block_hash(intel: MerchantIntelligence) -> str:
    """Stable content hash of the artifact's TAX block.

    A human sign-off is keyed by this hash, so any change to the tax facts (rate,
    flags, can_support, jurisdiction, …) yields a new hash and the old approval no
    longer matches — the merchant reverts to ``needs_review`` automatically.
    Hashes the tax block only (not provenance/catalog/timestamp) so cosmetic
    provenance edits do not needlessly revoke an approval.
    """
    payload = json.dumps(
        _hashable_tax(intel.tax.to_dict()), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _hashable_tax(tax: dict[str, Any]) -> dict[str, Any]:
    # Provenance is human-readable narration, not a tax FACT — exclude it so
    # rewording the evidence trail does not revoke a sign-off, while any change
    # to a rate/flag/jurisdiction/can_support still does.
    return {k: v for k, v in tax.items() if k != "provenance"}


def _taxed_count(intel: MerchantIntelligence) -> int:
    receipts = intel.sources.get("receipts") or {}
    try:
        return int(receipts.get("taxed_count") or 0)
    except (TypeError, ValueError):
        return 0


def _count_independent_sources(intel: MerchantIntelligence) -> tuple[int, tuple[str, ...]]:
    """Count independent sources that cross-check the TAX facts."""
    present: list[str] = []
    if _taxed_count(intel) >= 1:
        present.append("receipts")
    web = intel.sources.get("web") or {}
    if web.get("published_rate") is not None:
        present.append("web")
    places = intel.sources.get("places") or {}
    if (places.get("place_ids") or []) or (intel.details.address or "").strip():
        present.append("places")
    return len(present), tuple(present)


def _first_contradiction(intel: MerchantIntelligence) -> str | None:
    for p in intel.tax.provenance:
        if _CONTRADICTION_RE.search(p):
            return p
    return None


def compute_review(
    intel: MerchantIntelligence,
    *,
    matches_validated_config: bool = False,
    min_sources: int = MIN_INDEPENDENT_SOURCES,
    min_taxed: int = MIN_CORROBORATING_TAXED,
) -> ReviewBlock:
    """Deterministically classify an artifact's tax intelligence.

    ``matches_validated_config`` is supplied by the caller (True when the tax
    block reproduces an existing hand-validated config); this module never reads
    the config itself.
    """
    tax = intel.tax
    taxed = _taxed_count(intel)
    has_rate = tax.validated_rate is not None or len(tax.jurisdiction_rates) > 0
    multi = len(tax.jurisdiction_rates) > 0 or tax.jurisdiction.strip().lower() == "multi"
    n_sources, source_names = _count_independent_sources(intel)

    # --- REJECTED: contradiction or no tax evidence at all -------------------- #
    contradiction = _first_contradiction(intel)
    if contradiction is not None:
        return ReviewBlock(
            REJECTED,
            (f"sources contradict / tax evidence unreliable: {contradiction}",),
        )
    if taxed == 0 and not has_rate:
        return ReviewBlock(
            REJECTED,
            ("no tax evidence: no taxed receipts and no validated rate",),
        )

    # --- AUTO-APPROVAL -------------------------------------------------------- #
    single_jurisdiction_ok = (not multi) and taxed >= min_taxed
    if (
        tax.confidence == "high"
        and n_sources >= min_sources
        and (matches_validated_config or single_jurisdiction_ok)
    ):
        basis = (
            "matches existing validated config"
            if matches_validated_config
            else f"single-jurisdiction with {taxed} corroborating taxed receipts"
        )
        return ReviewBlock(
            AUTO_APPROVED,
            (
                f"high confidence; {n_sources} independent sources "
                f"({', '.join(source_names)}); {basis}",
            ),
            approved_by="auto",
            approved_at=intel.generated_at or None,
        )

    # --- NEEDS_REVIEW: collect the specific blockers -------------------------- #
    reasons: list[str] = []
    if tax.confidence != "high":
        reasons.append(f"confidence is '{tax.confidence}', not high")
    if multi:
        reasons.append("multi-jurisdiction merchant (per-run jurisdiction safety needed)")
    if n_sources < min_sources:
        reasons.append(
            f"only {n_sources} independent source(s) cross-check the tax facts "
            f"({', '.join(source_names) or 'none'})"
        )
    if taxed < min_taxed:
        reasons.append(
            f"only {taxed} corroborating taxed receipt(s); need >= {min_taxed}"
        )
    if not matches_validated_config:
        reasons.append("new merchant or jurisdiction (no matching validated config)")
    if not reasons:
        reasons.append("does not meet auto-approval criteria")
    return ReviewBlock(NEEDS_REVIEW, tuple(reasons))
