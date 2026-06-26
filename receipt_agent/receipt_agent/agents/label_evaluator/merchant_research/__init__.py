"""Merchant intelligence research pipeline.

This package produces ``merchant_intelligence/<slug>.json`` artifacts by
triangulating three sources per merchant:

1. Real receipts (MCP receipt-tools — ground truth for tax flags, effective rate,
   catalog items, and jurisdiction).
2. Web search — corroborates the city/county sales-tax rate for the store's
   address and period.
3. Places cache (``receipt_places`` Dynamo table, already populated by OCR worker)
   — jurisdiction, address, and store-id without new API calls.

Artifacts are consumed by ``merchant_tax_config`` (tax gate) and ``online_catalogs``
(synthesis catalog), keeping the deterministic gates unchanged.

Public surface
--------------
``schema``   — locked dataclasses for MerchantIntelligence and sub-objects.
``loader``   — read artifacts from ``merchant_intelligence/<slug>.json``.
``research`` — per-merchant research agent (added in milestone 2).
"""

from .loader import artifact_tax_profile, load_merchant_intelligence
from .schema import (
    CatalogEntry,
    MerchantDetails,
    MerchantIntelligence,
    TaxArtifact,
)

__all__ = [
    "artifact_tax_profile",
    "CatalogEntry",
    "load_merchant_intelligence",
    "MerchantDetails",
    "MerchantIntelligence",
    "TaxArtifact",
]
