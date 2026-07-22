#!/usr/bin/env python3.12
"""Regenerate the committed merchant-truth fixture bundle (CI/offline).

The fixture is the ONLY way a render resolves merchant truth without the dev
table: ``MERCHANT_TRUTH_MODE=fixture`` (optionally ``MERCHANT_TRUTH_FIXTURE``
pointing at a file or directory of ``{"items": [...]}`` bundle files in the
loader's low-level DynamoDB item format — exactly one SEALED/PASS manifest
plus its seven components per file). Missing S3 atlases are tolerated in this
mode BY DESIGN and marked loudly by the renderer.

Deterministic: re-running produces byte-identical JSON. Run from the repo
root:

    python3.12 scripts/fixtures/merchant_truth/build_fixture.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(REPO, "receipt_dynamo"))

from receipt_dynamo.entities.merchant_truth import (  # noqa: E402
    MerchantTruthComponent,
    MerchantTruthManifest,
    compute_bundle_hash,
    hash_payload,
)

NOW = "2026-07-21T00:00:00+00:00"
SLUG = "fixture_mart"
ATLAS_BYTES = b"fixture-mart-atlas-bytes\n"
LOGO_BYTES = b"fixture-mart-logo-bytes\n"


def payloads() -> dict:
    """Component payloads shaped exactly like a v1 migration mint."""
    atlas_hash = hashlib.sha256(ATLAS_BYTES).hexdigest()
    logo_hash = hashlib.sha256(LOGO_BYTES).hexdigest()
    catalog_items: list = []
    return {
        "identity": {
            "merchant_name": "Fixture Mart",
            "slug": SLUG,
            "aliases": ["FIXTURE MART #001"],
            "upper_slug": SLUG.upper(),
            "normalized_aliases": ["fixture mart", "fixture mart 001"],
        },
        "typography": {
            "typography": {
                "bitmap_font": {"regular": "fixture_mart.glyphs.npz"},
                "bitmap_thin": 0.2,
                "condense": 0.9,
                "pitch_ratio": 0.55,
            },
            "section_scale": {"HEADER": 0.8},
        },
        "flags": {
            "typography": {"font": "PTMONO", "dashed_separators": True},
        },
        "layout": {"available": False, "template": None},
        "assets": {
            "fonts": {
                "regular": {
                    "bucket_alias": "merchant-font-artifacts",
                    "s3_key": (
                        f"merchant_fonts/{SLUG}/"
                        f"regular-{atlas_hash[:12]}.npz"
                    ),
                    "content_hash": atlas_hash,
                    "source_commit": "fixture",
                    "compiled_at": NOW,
                    "cap_h": 58.0,
                    "advance_ratio": 0.54,
                    "pitch_check": "OK",
                    "glyph_count": 94,
                    "cache_filename": "fixture_mart.glyphs.npz",
                }
            },
            "profile": {
                "logo": "fixture_mart_logo.png",
                "logo_anchor": {"phrases": ["FIXTURE MART"], "center": True},
            },
            "logo": {
                "bucket_alias": "merchant-font-artifacts",
                "s3_key": f"merchant_fonts/{SLUG}/logo-{logo_hash[:8]}.png",
                "content_hash": logo_hash,
                "size": len(LOGO_BYTES),
            },
            "missing_merchant_font": False,
        },
        "stylemap": {
            "available": True,
            "document": {
                "version": 1,
                "source": "fixture",
                "sections": {"HEADER": {"face": "regular"}},
            },
            "source": None,
        },
        "catalog_snapshot": {
            "items": catalog_items,
            "item_count": 0,
            "catalog_hash": hash_payload(catalog_items),
            "as_of": NOW,
        },
    }


def build_items() -> list[dict]:
    provenance = {
        "source_kind": "migration",
        "written_by": {"kind": "migration", "name": "fixture", "version": "1"},
        "pipeline": "fixture",
        "provenance_completeness": "legacy",
    }
    components = [
        MerchantTruthComponent(
            slug=SLUG,
            version=1,
            name=name,
            payload=payload,
            provenance=provenance,
        )
        for name, payload in sorted(payloads().items())
    ]
    hashes = {item.name: item.content_hash for item in components}
    manifest = MerchantTruthManifest(
        slug=SLUG,
        version=1,
        component_hashes=hashes,
        bundle_hash=compute_bundle_hash(hashes),
        status="SEALED",
        provenance={"written_by": "fixture", "minted_at": NOW},
        mint_run_id="fixture-mart-v1",
        gate_status="PASS",
        sealed_at=NOW,
    )
    return [manifest.to_item(), *[item.to_item() for item in components]]


def main() -> int:
    out = os.path.join(HERE, "fixture_mart.v1.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"items": build_items()}, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
