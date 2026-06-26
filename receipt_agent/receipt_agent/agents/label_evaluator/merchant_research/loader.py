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
from decimal import Decimal
from pathlib import Path
from typing import Any

from .schema import MerchantIntelligence

logger = logging.getLogger(__name__)

# Artifacts live in merchant_intelligence/ next to the label_evaluator package.
_ARTIFACT_DIR = Path(__file__).parent.parent / "merchant_intelligence"


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


def artifact_tax_profile(slug: str) -> dict[str, Any] | None:
    """Tax fields for ``slug`` in a dict compatible with ``MerchantTaxProfile``.

    Returns ``None`` when no artifact exists or the tax section is incomplete,
    so the caller can safely fall back to the hardcoded ``MERCHANT_TAX_PROFILES``
    dict.  Never raises.

    Returned dict keys match the ``MerchantTaxProfile`` constructor:
        merchant, taxable_flags, nontaxable_flags, can_support_taxable_edits,
        confidence, validated_rate, jurisdiction_rates, notes.
    """
    intel = load_merchant_intelligence(slug)
    if intel is None:
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
    """Return slugs for which a merchant intelligence artifact exists."""
    if not _ARTIFACT_DIR.exists():
        return []
    return sorted(
        path.stem
        for path in _ARTIFACT_DIR.glob("*.json")
        if path.is_file()
    )
