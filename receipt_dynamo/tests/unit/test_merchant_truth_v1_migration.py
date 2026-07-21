"""Dry-run and crosswalk contracts for the MerchantTruth v1 migration."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from receipt_dynamo.entities.merchant_font import MerchantFont
from receipt_dynamo.entities.merchant_truth import MerchantTruthComponent
from receipt_dynamo.migrations.merchant_truth_v1 import (
    DEV_TABLE_NAME,
    EXPECTED_MISSING_FONT_SLUGS,
    ReadOnlyMerchantTruthSource,
    UnmappedMerchantTruthLeafError,
    build_crosswalk,
    build_v1_payloads,
    slugify_merchant,
    write_dry_run_payloads,
)

pytestmark = pytest.mark.unit
NOW = "2026-07-20T16:00:00+00:00"
GIT_SHA = "a" * 40
MERCHANTS = [
    "Amazon Fresh",
    "CVS",
    "Costco Wholesale",
    "Dollar Tree",
    "Gelson's Westlake Village",
    "In-N-Out Burger",
    "Italia Deli & Bakery",
    "Neighborly",
    "Smith's",
    "Sprouts Farmers Market",
    "Target",
    "The Home Depot",
    "The Stand - American Classics Redefined",
    "Trader Joe's",
    "Vons",
    "Wild Fork",
]


def profile_document() -> dict[str, Any]:
    profiles = {
        merchant: {
            "_comment": "legacy note",
            "aliases": [merchant.upper()],
            "typography": {"condense": 0.9},
        }
        for merchant in MERCHANTS
    }
    profiles["Sprouts Farmers Market"] = {
        "_comment": "legacy note",
        "aliases": ["Sprouts"],
        "typography": {
            "bitmap_font": {"regular": "sprouts.npz"},
            "bitmap_thin": 0.1,
            "condense": 0.9,
            "ocr_font_sizing": True,
            "bitmap_cap_ratio": 1.0,
            "ocr_cap_height_ratio": 1.0,
            "pitch_ratio": 0.55,
            "ink": [0, 0, 0],
            "bitmap_glyph_vscale": 1.0,
            "font": "OCRB",
            "stroke": 1,
            "display_headings": {"THANK YOU": "heavy"},
            "reverse_date_after_items": True,
            "reverse_date_anchor": "right",
            "dash_after_amount_date": True,
            "dash_around_phrases": ["TOTAL"],
            "dashed_separators": True,
            "face_source": "profile",
            "heading_bleed_phrase": "THANK YOU",
            "reverse_total": True,
            "mixed_layout": False,
            "condense_glyphs": True,
            "stylemap": "sprouts.stylemap.json",
            "_face_source_comment": "legacy",
        },
        "section_scale": {"HEADER": 1.1},
        "header": {"header_exact": ["SPROUTS"]},
        "graphics": {"inbody_barcode": {"max_count": 1}},
        "compose": True,
        "logo": "sprouts_logo.png",
        "logo_anchor": {"center": True},
        "logo_subtitle": "MARKET",
        "logo_reserve_subtitle": True,
        "layout_template": {
            "_comment": "legacy",
            "version": 1,
            "columns": {"items": [{"x": 0.5}]},
        },
    }
    return {
        "_comment": "registry",
        "_section_scale_note": "calibration",
        "profiles": profiles,
    }


def merchant_font(merchant_name: str) -> MerchantFont:
    token = slugify_merchant(merchant_name)
    return MerchantFont(
        merchant_name=merchant_name,
        face="regular",
        s3_bucket="font-bucket",
        s3_key=f"merchant_fonts/{token}/regular-{'1' * 8}.npz",
        content_hash="1" * 64,
        source_commit="abc123",
        compiled_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        cap_h=0.7,
        advance_ratio=0.54,
        pitch_check="OK",
        glyph_count=96,
        stylemap_s3_key=f"merchant_fonts/{token}/stylemap.json",
        logo_s3_key=f"merchant_fonts/{token}/logo.png",
        cache_filename=f"{token}.npz",
    )


class FakeSource:
    def __init__(self) -> None:
        self.fonts = [
            merchant_font(name)
            for name in MERCHANTS
            if slugify_merchant(name) not in EXPECTED_MISSING_FONT_SLUGS
        ]
        self.stylemap_bytes = b'{"version":1,"sections":{}}'
        self.logo_bytes = b"logo-bytes"

    def list_merchant_font_items(self) -> list[dict[str, Any]]:
        return [font.to_item() for font in self.fonts]

    def list_catalog_items(self, slug: str) -> list[dict[str, Any]]:
        return [
            {
                "PK": {"S": f"MERCHANT_CATALOG#{slug}"},
                "SK": {"S": "ITEM#GROCERY#MILK"},
                "TYPE": {"S": "MERCHANT_CATALOG_ITEM"},
                "product_text": {"S": "MILK"},
                "price": {"S": "3.99"},
                "category": {"S": "GROCERY"},
                "taxable": {"BOOL": False},
            }
        ]

    def read_object(self, _bucket: str, key: str) -> bytes:
        return (
            self.stylemap_bytes
            if key.endswith("stylemap.json")
            else self.logo_bytes
        )


def component_from_payload(
    document: dict[str, Any], name: str
) -> MerchantTruthComponent:
    item = next(
        item
        for item in document["items"]
        if item.get("component", {}).get("S") == name
    )
    return MerchantTruthComponent.from_item(item)


def test_crosswalk_maps_every_known_leaf_and_fails_on_unknown() -> None:
    document = profile_document()

    crosswalk = build_crosswalk(document)

    assert crosswalk
    assert {item.classification for item in crosswalk} == {
        "measured",
        "decided",
        "asset",
        "identity",
        "discarded-with-reason",
    }
    assert all(
        item.destination is not None or item.reason is not None
        for item in crosswalk
    )
    document["profiles"]["CVS"]["unknown_runtime_knob"] = True
    with pytest.raises(UnmappedMerchantTruthLeafError, match="unmapped"):
        build_crosswalk(document)


def test_migration_emits_exact_nine_items_for_all_sixteen_merchants() -> None:
    source = FakeSource()

    payloads, _ = build_v1_payloads(
        profile_document(),
        source,  # type: ignore[arg-type]
        profiles_source_path="scripts/merchant_profiles.json",
        git_sha=GIT_SHA,
        generated_at=NOW,
    )

    assert len(payloads) == 16
    assert all(len(payload.items) == 9 for payload in payloads)
    assert {
        payload.slug
        for payload in payloads
        if any("missing MerchantFont" in item for item in payload.blockers)
    } == EXPECTED_MISSING_FONT_SLUGS
    assert all(
        payload.items[0]["status"] == {"S": "OPEN"} for payload in payloads
    )


def test_stylemap_and_logo_hashes_are_computed_from_their_own_bytes() -> None:
    source = FakeSource()
    payloads, _ = build_v1_payloads(
        profile_document(),
        source,  # type: ignore[arg-type]
        profiles_source_path="scripts/merchant_profiles.json",
        git_sha=GIT_SHA,
        generated_at=NOW,
    )
    document = next(
        payload.to_document()
        for payload in payloads
        if payload.slug == "sprouts_farmers_market"
    )

    stylemap = component_from_payload(document, "stylemap").payload
    assets = component_from_payload(document, "assets").payload

    assert (
        stylemap["source"]["content_hash"]
        == hashlib.sha256(source.stylemap_bytes).hexdigest()
    )
    assert (
        assets["logo"]["content_hash"]
        == hashlib.sha256(source.logo_bytes).hexdigest()
    )
    assert stylemap["source"]["content_hash"] != "1" * 64
    assert assets["logo"]["content_hash"] != "1" * 64


def test_dry_run_writer_outputs_payload_crosswalk_and_summary(
    tmp_path: Path,
) -> None:
    payloads, crosswalk = build_v1_payloads(
        profile_document(),
        FakeSource(),  # type: ignore[arg-type]
        profiles_source_path="scripts/merchant_profiles.json",
        git_sha=GIT_SHA,
        generated_at=NOW,
    )

    write_dry_run_payloads(
        tmp_path,
        payloads,
        crosswalk,
        generated_at=NOW,
        git_sha=GIT_SHA,
    )

    assert len(list(tmp_path.glob("*.json"))) == 18
    summary = json.loads((tmp_path / "_summary.json").read_text())
    assert summary["dry_run"] is True
    assert summary["merchant_count"] == 16
    assert (
        set(summary["missing_merchant_font_slugs"])
        == EXPECTED_MISSING_FONT_SLUGS
    )


class QueryOnlyDynamo:
    def query(self, **_kwargs: Any) -> dict[str, Any]:
        return {"Items": []}


class GetOnlyS3:
    def get_object(self, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("not used")


def test_source_adapter_is_dev_read_only_and_has_no_write_surface() -> None:
    source = ReadOnlyMerchantTruthSource(
        QueryOnlyDynamo(),  # type: ignore[arg-type]
        GetOnlyS3(),  # type: ignore[arg-type]
        DEV_TABLE_NAME,
    )
    assert source.list_merchant_font_items() == []
    assert not hasattr(source, "put_item")
    assert not hasattr(source, "transact_write_items")

    with pytest.raises(ValueError, match="only reads exact dev table"):
        ReadOnlyMerchantTruthSource(
            QueryOnlyDynamo(),  # type: ignore[arg-type]
            GetOnlyS3(),  # type: ignore[arg-type]
            "ReceiptsTable-d7ff76a",
        )
