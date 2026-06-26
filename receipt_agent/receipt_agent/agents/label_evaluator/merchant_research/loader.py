"""Load merchant intelligence artifacts from merchant_intelligence/<slug>.json.

Artifacts live alongside this package in the ``merchant_intelligence/`` sibling
directory so they stay version-controlled with the code. The loader is read-only
and raises no exceptions on missing or malformed files — it logs a warning and
returns ``None`` so callers can fall back to the hardcoded config.

Primary entry points
--------------------
``load_merchant_intelligence(slug)``
    Parse the full artifact for a merchant slug.

``artifact_tax_profile(slug)``
    Return just the tax fields in a dict compatible with
    ``merchant_tax_config.MerchantTaxProfile`` construction.  Used by the thin
    hook in ``merchant_tax_config.merchant_tax_profile()`` to prefer a generated
    artifact over the hardcoded dict when one is available.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .review import (
    APPROVED,
    AUTO_APPROVED,
    NEEDS_REVIEW,
    ReviewBlock,
    compute_review,
    tax_block_hash,
)
from .schema import MerchantIntelligence

logger = logging.getLogger(__name__)

# Artifacts live in merchant_intelligence/ next to the label_evaluator package.
_ARTIFACT_DIR = Path(__file__).parent.parent / "merchant_intelligence"
# Human sign-offs live in a SEPARATE file (keyed by content hash) so a research
# regeneration never wipes an approval. Leading "_" keeps it out of the artifact
# glob in list_available_slugs.
_APPROVALS_FILENAME = "_approvals.json"


def _load_raw(slug: str) -> dict[str, Any] | None:
    """Load and parse the raw JSON for ``slug``, or None on any failure."""
    path = _ARTIFACT_DIR / f"{slug}.json"
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except Exception as exc:
        logger.warning(
            "merchant_research.loader: could not load %s: %s", path, exc
        )
        return None


def load_merchant_intelligence(slug: str) -> MerchantIntelligence | None:
    """Return the parsed ``MerchantIntelligence`` for ``slug``, or ``None``.

    Returns ``None`` (not an exception) when the artifact file is absent or
    cannot be parsed — callers fall back to hardcoded config.
    """
    raw = _load_raw(slug)
    if raw is None:
        return None
    try:
        intel = MerchantIntelligence.from_dict(raw)
    except Exception as exc:
        logger.warning(
            "merchant_research.loader: could not parse artifact for %s: %s",
            slug,
            exc,
        )
        return None
    # The filename slug is the lookup key; the artifact's internal ``slug`` field
    # must agree with it. A mismatch (e.g. a copied vons.json that still says
    # "slug": "target") would otherwise resolve the wrong merchant's tax facts,
    # silently corrupting slug resolution. Reject so the hardcoded fallback wins.
    if intel.slug != slug:
        logger.warning(
            "merchant_research.loader: artifact %s.json declares slug %r; "
            "rejecting to avoid mis-resolving merchant",
            slug,
            intel.slug,
        )
        return None
    return intel


# --------------------------------------------------------------------------- #
# Human-approval review gate
# --------------------------------------------------------------------------- #


def matches_validated_config(intel: MerchantIntelligence) -> bool:
    """True when ``intel``'s tax block reproduces the hand-validated config.

    Compares the GATE-RELEVANT fields against ``MERCHANT_TAX_PROFILES``. Imported
    lazily so this module never sits in an import cycle with the tax gate, and so
    a missing/broken config can never raise here.
    """
    try:
        from ..merchant_tax_config import MERCHANT_TAX_PROFILES
    except Exception:  # pragma: no cover
        return False
    prof = MERCHANT_TAX_PROFILES.get(intel.slug)
    if prof is None:
        return False
    tax = intel.tax
    artifact_taxable = frozenset({tax.taxable_flag} if tax.taxable_flag else set())
    if artifact_taxable != prof.taxable_flags:
        return False
    if frozenset(tax.nontaxable_flags) != prof.nontaxable_flags:
        return False
    if tax.can_support_taxable_edits != prof.can_support_taxable_edits:
        return False
    if prof.can_support_taxable_edits:
        if tax.validated_rate != prof.validated_rate:
            return False
        if tuple(tax.jurisdiction_rates) != tuple(prof.jurisdiction_rates):
            return False
    return True


def _load_approvals() -> list[dict[str, Any]]:
    """Load the human sign-off ledger, or [] when absent/unparseable.

    TRUST BOUNDARY: ``_approvals.json`` IS the approval authority — it is a
    version-controlled, code-review-gated ledger, not a tamper-resistant store.
    Anyone who can commit a matching ``(slug, tax_hash)`` entry can approve, the
    same trust level as committing code; review changes to this file like code.
    The content-hash binding limits a forged entry to one exact tax block (it
    cannot grant blanket enablement and reverts the moment the tax facts change).
    """
    path = _ARTIFACT_DIR / _APPROVALS_FILENAME
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(
            "merchant_research.loader: could not load approvals %s: %s", path, exc
        )
        return []
    entries = data.get("approvals") if isinstance(data, dict) else data
    return [e for e in (entries or []) if isinstance(e, dict)]


def _find_approval(
    slug: str, content_hash: str, kind: str = "tax"
) -> dict[str, Any] | None:
    """Return a sign-off matching this slug, content hash, AND kind (tax/structure).

    Back-compat: an entry without ``kind`` is treated as ``tax``, and the hash is
    read from ``content_hash`` or the legacy ``tax_hash`` field.
    """
    for entry in _load_approvals():
        if str(entry.get("slug")) != slug:
            continue
        if str(entry.get("kind") or "tax") != kind:
            continue
        entry_hash = str(entry.get("content_hash") or entry.get("tax_hash") or "")
        if entry_hash == content_hash:
            return entry
    return None


def effective_review(slug: str) -> ReviewBlock | None:
    """The authoritative review for ``slug`` (deterministic + human overlay).

    Recomputes the status from the artifact every time (never trusts a stored
    JSON field), then lifts ``needs_review`` to ``approved`` when a human sign-off
    exists for the CURRENT tax-block hash. Returns ``None`` only when no artifact
    exists.
    """
    intel = load_merchant_intelligence(slug)
    if intel is None:
        return None
    base = compute_review(
        intel, matches_validated_config=matches_validated_config(intel)
    )
    # A human sign-off lifts ONLY ``needs_review`` to ``approved``. ``rejected``
    # is a hard stop (sources contradict / no tax evidence — there is nothing
    # sound to enable), and ``auto_approved`` needs no human.
    if base.status == NEEDS_REVIEW:
        approval = _find_approval(slug, tax_block_hash(intel), kind="tax")
        if approval is not None and approval.get("status", "approved") != "revoked":
            return ReviewBlock(
                status=APPROVED,
                reasons=base.reasons + ("human-approved for current tax block",),
                approved_by=str(approval.get("approved_by") or "unknown"),
                approved_at=approval.get("approved_at") or None,
            )
    return base


def effective_structure(slug: str) -> dict[str, Any] | None:
    """The artifact's structure block, fully RECOMPUTED from ``archetype_mix``.

    Returns ``None`` when the artifact has no structure block. The structure_type,
    confidence, applicable_operations, and gate ``status`` are all re-derived from
    the raw ``archetype_mix`` (the authoritative evidence) — NONE of the derived
    JSON fields is trusted — so hand-editing ``confidence``/``structure_type``/
    ``status`` in the file cannot grant a structural prior the deterministic logic
    would park. (``archetype_mix`` itself is the evidence of record, the same
    trust model as the tax facts; committed drift is caught by the freshness
    guard.) If the mix is absent, the structure is treated as unverifiable and
    parked.
    """
    intel = load_merchant_intelligence(slug)
    if intel is None or not intel.structure:
        return None
    # lazy import: avoid an import cycle and keep structure deps off the hot path
    from .structure import structure_review_status, summarize_merchant_structure

    mix = intel.structure.get("archetype_mix") or {}
    if not mix:
        parked = dict(intel.structure)
        parked["status"] = NEEDS_REVIEW
        return parked
    try:
        ms = summarize_merchant_structure(
            {str(k): int(v) for k, v in mix.items()},
            cluster_size=intel.structure.get("cluster_size"),
        )
    except Exception:  # pragma: no cover - malformed mix -> park, never raise
        parked = dict(intel.structure)
        parked["status"] = NEEDS_REVIEW
        return parked
    structure = ms.to_dict()
    status = structure_review_status(ms.confidence)
    # A human structure sign-off lifts ONLY needs_review -> approved, keyed by the
    # archetype-mix hash so a re-fingerprint that shifts the mix reverts it.
    if status == NEEDS_REVIEW:
        from .structure import archetype_mix_hash  # lazy

        approval = _find_approval(
            slug, archetype_mix_hash(ms.archetype_mix), kind="structure"
        )
        if approval is not None and approval.get("status", "approved") != "revoked":
            status = APPROVED
            structure["approved_by"] = str(approval.get("approved_by") or "unknown")
            structure["approved_at"] = approval.get("approved_at") or None
    structure["status"] = status
    return structure


def structure_is_enabling(slug: str) -> bool:
    """True only when ``slug``'s structural assignment is approved to be trusted.

    ``auto_approved`` (high-confidence) or human-``approved`` (a sign-off for the
    current archetype mix). A parked structure must not grant a structural prior /
    service-grounding override downstream.
    """
    structure = effective_structure(slug)
    return bool(structure and structure.get("status") in (AUTO_APPROVED, APPROVED))


def artifact_tax_profile(slug: str) -> dict[str, Any] | None:
    """Tax fields for ``slug`` IF its intelligence is approved to enable edits.

    Returns ``None`` — so the caller falls back to the hardcoded
    ``MERCHANT_TAX_PROFILES`` dict (itself a prior human validation) — when no
    artifact exists OR the artifact's review status is not enabling
    (``needs_review`` / ``rejected``). Only ``auto_approved`` or human-``approved``
    intelligence is allowed to drive the tax gate. Never raises.

    Returned dict keys match the ``MerchantTaxProfile`` constructor:
        merchant, taxable_flags, nontaxable_flags, can_support_taxable_edits,
        confidence, validated_rate, jurisdiction_rates, notes.
    """
    intel = load_merchant_intelligence(slug)
    if intel is None:
        return None
    review = effective_review(slug)
    if review is None or not review.is_enabling():
        # Parked: generated but not approved. The gate does not consult it.
        return None
    tax = intel.tax
    taxable_flags = frozenset({tax.taxable_flag} if tax.taxable_flag else set())
    nontaxable_flags = frozenset(tax.nontaxable_flags)
    notes = "; ".join(tax.provenance) if tax.provenance else ""
    return {
        "merchant": intel.merchant,
        "taxable_flags": taxable_flags,
        "nontaxable_flags": nontaxable_flags,
        "can_support_taxable_edits": tax.can_support_taxable_edits,
        "confidence": tax.confidence,
        "validated_rate": tax.validated_rate,
        "jurisdiction_rates": tax.jurisdiction_rates,
        "notes": notes,
    }


def list_available_slugs() -> list[str]:
    """Return slugs for which a merchant intelligence artifact exists.

    Skips files beginning with "_" (e.g. the ``_approvals.json`` sign-off
    ledger), which are not merchant artifacts.
    """
    if not _ARTIFACT_DIR.exists():
        return []
    return sorted(
        path.stem
        for path in _ARTIFACT_DIR.glob("*.json")
        if path.is_file() and not path.stem.startswith("_")
    )


def _content_hash_for(intel: MerchantIntelligence, kind: str) -> str:
    if kind == "structure":
        from .structure import archetype_mix_hash  # lazy

        return archetype_mix_hash((intel.structure or {}).get("archetype_mix") or {})
    return tax_block_hash(intel)


def record_approval(
    slug: str,
    *,
    approved_by: str,
    approved_at: str,
    note: str = "",
    kind: str = "tax",
) -> dict[str, Any]:
    """Append a human sign-off for ``slug``'s CURRENT ``kind`` block to the ledger.

    ``kind`` is ``"tax"`` (keyed by the tax-block hash) or ``"structure"`` (keyed
    by the archetype-mix hash) so a later regeneration that changes those facts
    produces a new hash and this sign-off no longer applies. Raises ``ValueError``
    if no artifact exists for ``slug``. ``approved_at`` is passed in (not stamped
    here) to keep this deterministic.
    """
    if kind not in ("tax", "structure"):
        raise ValueError(f"unknown approval kind {kind!r}")
    intel = load_merchant_intelligence(slug)
    if intel is None:
        raise ValueError(f"no artifact for slug {slug!r}; nothing to approve")
    if kind == "structure" and not intel.structure:
        raise ValueError(f"{slug!r} has no structure block to approve")
    content_hash = _content_hash_for(intel, kind)
    entry = {
        "slug": slug,
        "merchant": intel.merchant,
        "kind": kind,
        "content_hash": content_hash,
        "approved_by": approved_by,
        "approved_at": approved_at,
        "note": note,
    }
    path = _ARTIFACT_DIR / _APPROVALS_FILENAME
    approvals = _load_approvals()
    # Replace any prior sign-off for the same (slug, kind, content_hash); keep
    # older ones for other hashes as an audit trail.
    approvals = [
        e
        for e in approvals
        if not (
            e.get("slug") == slug
            and str(e.get("kind") or "tax") == kind
            and str(e.get("content_hash") or e.get("tax_hash") or "") == content_hash
        )
    ]
    approvals.append(entry)
    path.write_text(
        json.dumps({"approvals": approvals}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return entry


def reviews_by_status() -> dict[str, list[tuple[str, ReviewBlock]]]:
    """Group ``(slug, effective_review)`` for every artifact by status."""
    grouped: dict[str, list[tuple[str, ReviewBlock]]] = {}
    for slug in list_available_slugs():
        review = effective_review(slug)
        if review is None:
            continue
        grouped.setdefault(review.status, []).append((slug, review))
    return grouped
