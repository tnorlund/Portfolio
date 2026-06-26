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

from .loader import (
    artifact_tax_profile,
    effective_review,
    load_merchant_intelligence,
    record_approval,
    reviews_by_status,
)
from .review import (
    APPROVED,
    AUTO_APPROVED,
    NEEDS_REVIEW,
    REJECTED,
    ReviewBlock,
    compute_review,
    tax_block_hash,
)
from .research import (
    PlacesEvidence,
    ReceiptEvidence,
    TaxRecommendation,
    WebEvidence,
    assemble_merchant_intelligence,
    recommend_taxable_support,
)
from .schema import (
    CatalogEntry,
    MerchantDetails,
    MerchantIntelligence,
    TaxArtifact,
)

__all__ = [
    "APPROVED",
    "artifact_tax_profile",
    "assemble_merchant_intelligence",
    "AUTO_APPROVED",
    "CatalogEntry",
    "compute_review",
    "effective_review",
    "load_merchant_intelligence",
    "MerchantDetails",
    "MerchantIntelligence",
    "NEEDS_REVIEW",
    "PlacesEvidence",
    "ReceiptEvidence",
    "recommend_taxable_support",
    "record_approval",
    "REJECTED",
    "ReviewBlock",
    "reviews_by_status",
    "tax_block_hash",
    "TaxArtifact",
    "TaxRecommendation",
    "WebEvidence",
]
