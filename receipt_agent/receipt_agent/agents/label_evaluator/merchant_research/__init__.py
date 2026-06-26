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
    effective_structure,
    load_merchant_intelligence,
    record_approval,
    reviews_by_status,
    structure_is_enabling,
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
from .structure import (
    ArchetypeAssignment,
    ArchetypeCluster,
    MerchantStructure,
    StructureFingerprint,
    archetype_mix_hash,
    classify_archetype,
    cluster_fingerprints,
    fingerprint_from_labeled_words,
    structure_review_status,
    summarize_merchant_structure,
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
    "archetype_mix_hash",
    "ArchetypeAssignment",
    "ArchetypeCluster",
    "artifact_tax_profile",
    "assemble_merchant_intelligence",
    "AUTO_APPROVED",
    "classify_archetype",
    "cluster_fingerprints",
    "effective_structure",
    "fingerprint_from_labeled_words",
    "MerchantStructure",
    "structure_is_enabling",
    "structure_review_status",
    "StructureFingerprint",
    "summarize_merchant_structure",
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
