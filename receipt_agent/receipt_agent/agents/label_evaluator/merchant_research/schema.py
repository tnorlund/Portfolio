"""Locked artifact schema for merchant intelligence.

``merchant_intelligence/<slug>.json`` files conform to this schema.
The ``MerchantIntelligence`` dataclass is the canonical in-memory form;
``to_dict`` / ``from_dict`` handle JSON round-trips.

Schema is intentionally locked at this shape so the synthesis-orchestration
branch (branch 3) can rely on stable field names and types.

JSON shape::

    {
      "merchant": "Vons",
      "slug": "vons",
      "tax": {
        "taxable_flag": "T",
        "nontaxable_flags": ["S"],
        "jurisdiction": "CA-Ventura",
        "validated_rate": "0.0725",
        "jurisdiction_rates": [],
        "can_support_taxable_edits": true,
        "confidence": "high",
        "provenance": ["receipts: 5 receipts at 7.25%", "web: CA Ventura 7.25%"]
      },
      "catalog": [
        {"name": "SPROUTS ORG EGGS 12CT", "price": "4.19", "taxable": false, "source": "receipt"}
      ],
      "details": {
        "address": "2734 Townsgate Rd, Westlake Village, CA 91361",
        "store_id": "2187",
        "category": "grocery"
      },
      "generated_at": "2026-06-26T00:00:00+00:00",
      "sources": {
        "receipts": {"count": 5, "image_ids": [...]},
        "web": {"urls": [...], "fetched_at": "..."},
        "places": {"place_ids": [...]}
      }
    }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class TaxArtifact:
    """Tax facts derived from cross-source research for one merchant."""

    taxable_flag: str
    nontaxable_flags: tuple[str, ...]
    jurisdiction: str
    validated_rate: Decimal | None
    jurisdiction_rates: tuple[Decimal, ...]
    can_support_taxable_edits: bool
    confidence: str
    provenance: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        # Rates serialize as STRINGS, not floats: these feed the deterministic
        # tax gate, so the JSON must round-trip Decimal losslessly by
        # construction (a float would bind us to repr-exact values only).
        return {
            "taxable_flag": self.taxable_flag,
            "nontaxable_flags": list(self.nontaxable_flags),
            "jurisdiction": self.jurisdiction,
            "validated_rate": (
                str(self.validated_rate) if self.validated_rate is not None else None
            ),
            "jurisdiction_rates": [str(r) for r in self.jurisdiction_rates],
            "can_support_taxable_edits": self.can_support_taxable_edits,
            "confidence": self.confidence,
            "provenance": list(self.provenance),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TaxArtifact:
        return cls(
            taxable_flag=str(d.get("taxable_flag") or ""),
            nontaxable_flags=tuple(str(f) for f in d.get("nontaxable_flags") or []),
            jurisdiction=str(d.get("jurisdiction") or ""),
            validated_rate=(
                Decimal(str(d["validated_rate"]))
                if d.get("validated_rate") is not None
                else None
            ),
            jurisdiction_rates=tuple(
                Decimal(str(r)) for r in (d.get("jurisdiction_rates") or [])
            ),
            can_support_taxable_edits=bool(d.get("can_support_taxable_edits", False)),
            confidence=str(d.get("confidence") or "low"),
            provenance=tuple(str(p) for p in (d.get("provenance") or [])),
        )


@dataclass(frozen=True)
class CatalogEntry:
    """A single product from a merchant's catalog."""

    name: str
    price: Decimal
    taxable: bool
    source: str  # "receipt" | "web" | "online_catalog" | "artifact"

    def to_dict(self) -> dict[str, Any]:
        # price as a STRING for the same lossless-Decimal reason as tax rates.
        return {
            "name": self.name,
            "price": str(self.price),
            "taxable": self.taxable,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CatalogEntry:
        return cls(
            name=str(d.get("name") or ""),
            price=Decimal(str(d.get("price") or "0")),
            taxable=bool(d.get("taxable", True)),
            source=str(d.get("source") or "artifact"),
        )


@dataclass(frozen=True)
class MerchantDetails:
    """Store location and category for one merchant."""

    address: str | None
    store_id: str | None
    category: str  # e.g. "grocery", "home_improvement", "warehouse_club"

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "store_id": self.store_id,
            "category": self.category,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MerchantDetails:
        return cls(
            address=d.get("address") or None,
            store_id=d.get("store_id") or None,
            category=str(d.get("category") or "unknown"),
        )


@dataclass(frozen=True)
class MerchantIntelligence:
    """Full merchant intelligence artifact, keyed by slug."""

    merchant: str
    slug: str
    tax: TaxArtifact
    catalog: tuple[CatalogEntry, ...]
    details: MerchantDetails
    generated_at: str
    sources: dict[str, Any] = field(default_factory=dict)
    # Structural taxonomy (M7): primary_archetype, archetype_mix, structure_type
    # (line_item|service|hybrid), applicable_operations, cluster_id/size,
    # confidence, provenance, status. Stored as a raw dict so this schema stays
    # decoupled from the structure module; ``merchant_research.structure`` builds
    # and parses it. Empty/absent when structure has not been derived.
    structure: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "merchant": self.merchant,
            "slug": self.slug,
            "tax": self.tax.to_dict(),
            "catalog": [e.to_dict() for e in self.catalog],
            "details": self.details.to_dict(),
            "generated_at": self.generated_at,
            "sources": self.sources,
        }
        if self.structure:
            out["structure"] = self.structure
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MerchantIntelligence:
        return cls(
            merchant=str(d.get("merchant") or ""),
            slug=str(d.get("slug") or ""),
            tax=TaxArtifact.from_dict(d.get("tax") or {}),
            catalog=tuple(
                CatalogEntry.from_dict(e) for e in (d.get("catalog") or [])
            ),
            details=MerchantDetails.from_dict(d.get("details") or {}),
            generated_at=str(d.get("generated_at") or ""),
            sources=d.get("sources") or {},
            structure=d.get("structure") or {},
        )
