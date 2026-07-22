#!/usr/bin/env python3.12
"""Regenerate the committed §7.2 variant-aware merchant-truth fixture.

Sibling of ``build_fixture.py``. Where that mints a variant-BLIND bundle
(``fixture_mart``), this mints ``variant_selftest`` -- a SEALED/PASS bundle
whose C#layout component carries a ``template.variants[]`` block, so the
corpus regression gate can exercise ``full_fidelity_eval.profile_columns``'s
variant-selection path (``columns_source="profile"``) end to end. A
regression in ``profile_columns`` / ``merchant_truth_variants.select_variant``
that stops honoring the matching variant's lane flips the corpus receipt's
columns verdict.

The template's DEFAULT ``items`` amount lane sits at x=0.86; two variants
carry text-marker hints -- a low-support ``self`` variant (x=0.60, support 2)
and a high-support ``checkout`` variant (x=0.70, support 7). A receipt whose
words contain BOTH markers matches both; the §7.2 tie-break (highest support,
then smallest variant_id) must select ``checkout`` -> x=0.70, which is where
the corpus receipt's amounts are actually inked. If selection regresses to the
DEFAULT (x=0.86) or the low-support variant (x=0.60), the profile lane no
longer sits on the ink and the columns metric goes UNTESTED instead of PASS.

Deterministic: re-running writes byte-identical JSON. Run from the repo root:

    python3.12 tests/fixtures/merchant_truth/build_variant_fixture.py
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
SLUG = "variant_selftest"
ATLAS_BYTES = b"variant-selftest-atlas-bytes\n"


def layout_template() -> dict:
    """A schema-v1 layout template with two text-marker variants (§7.2)."""
    return {
        "version": 1,
        "columns": {
            "items": [
                {"role": "amount", "anchor": "right", "x": 0.86, "support": 9}
            ]
        },
        "sections": [{"name": "items"}],
        "separators": [{"char": "-"}],
        "variants": [
            {
                "variant_id": "self-low",
                "classifier_hint": {
                    "type": "text_marker",
                    "tokens": ["SELF"],
                },
                "columns": {
                    "items": [
                        {
                            "role": "amount",
                            "anchor": "right",
                            "x": 0.60,
                            "support": 2,
                        }
                    ]
                },
                "sections": [{"name": "items"}],
                "separators": [{"char": "-"}],
                "support": 2,
                "source_receipt_keys": ["IMAGE#self#RECEIPT#00001"],
            },
            {
                "variant_id": "checkout-high",
                "classifier_hint": {
                    "type": "text_marker",
                    "tokens": ["CHECKOUT"],
                },
                "columns": {
                    "items": [
                        {
                            "role": "amount",
                            "anchor": "right",
                            "x": 0.70,
                            "support": 7,
                        }
                    ]
                },
                "sections": [{"name": "items"}],
                "separators": [{"char": "-"}],
                "support": 7,
                "source_receipt_keys": ["IMAGE#checkout#RECEIPT#00001"],
            },
        ],
    }


def payloads() -> dict:
    atlas_hash = hashlib.sha256(ATLAS_BYTES).hexdigest()
    catalog_items: list = []
    return {
        "identity": {
            "merchant_name": "Variant Selftest",
            "slug": SLUG,
            "aliases": ["VARIANT SELFTEST #001"],
            "upper_slug": SLUG.upper(),
            "normalized_aliases": ["variant selftest", "variant selftest 001"],
        },
        "typography": {
            "typography": {
                "bitmap_font": {"regular": "variant_selftest.glyphs.npz"},
                "bitmap_thin": 0.2,
                "condense": 0.9,
                "pitch_ratio": 0.55,
            },
            "section_scale": {"HEADER": 0.8},
        },
        "flags": {
            "typography": {"font": "PTMONO", "dashed_separators": True},
        },
        "layout": {"available": True, "template": layout_template()},
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
                    "cache_filename": "variant_selftest.glyphs.npz",
                }
            },
            # wordmark-as-text merchant (no logo asset): the storefront band's
            # logo metric is a presence-only check.
            "profile": {},
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
        mint_run_id="variant-selftest-v1",
        gate_status="PASS",
        sealed_at=NOW,
    )
    return [manifest.to_item(), *[item.to_item() for item in components]]


def main() -> int:
    out = os.path.join(HERE, "variant_selftest.v1.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"items": build_items()}, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
