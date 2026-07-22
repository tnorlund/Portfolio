"""Round-trip parity: truth-bundle profiles == the legacy registry.

The renderer no longer reads ``scripts/merchant_profiles.json``; it maps each
merchant's ACTIVE truth bundle back into the legacy in-memory profile shape
(``rsr._profile_from_truth``). This test drives the REAL registry file
through the v1 migration payload builder (the exact code that minted the live
bundles) and asserts the reconstruction reproduces every legacy profile —
byte-for-byte at the JSON level, for all 16 merchants — including the
explicit-empty vs absent ``section_scale`` distinction the v1 payload
collapsed (the ``_V1_EXPLICIT_EMPTY_SECTION_SCALE`` shim).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, os.path.join(REPO, "receipt_dynamo"))

import render_synthetic_receipts as rsr  # noqa: E402

from receipt_dynamo.entities.merchant_font import MerchantFont  # noqa: E402
from receipt_dynamo.entities.merchant_truth import (  # noqa: E402
    MerchantTruthComponent,
)
from receipt_dynamo.migrations.merchant_truth_v1 import (  # noqa: E402
    EXPECTED_MISSING_FONT_SLUGS,
    build_v1_payloads,
    slugify_merchant,
)

PROFILES_PATH = os.path.join(REPO, "scripts", "merchant_profiles.json")
NOW = "2026-07-21T00:00:00+00:00"
GIT_SHA = "a" * 40


def _merchant_font(merchant_name: str) -> MerchantFont:
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
    """Read-only source with fonts for every non-blocked merchant."""

    def __init__(self, merchants: list[str]) -> None:
        self.fonts = [
            _merchant_font(name)
            for name in merchants
            if slugify_merchant(name) not in EXPECTED_MISSING_FONT_SLUGS
        ]

    def list_merchant_font_items(self) -> list[dict[str, Any]]:
        return [font.to_item() for font in self.fonts]

    def list_catalog_items(self, slug: str) -> list[dict[str, Any]]:
        del slug
        return []

    def read_object(self, _bucket: str, key: str) -> bytes:
        if key.endswith("stylemap.json"):
            return b'{"version":1,"sections":{}}'
        return b"logo-bytes"


def _strip_private(value: Any) -> Any:
    """Drop the ``_``-prefixed comment leaves the migration discards."""
    if isinstance(value, dict):
        return {
            key: _strip_private(item)
            for key, item in value.items()
            if not str(key).startswith("_")
        }
    return value


def _artifact(payload_document: dict[str, Any], slug: str) -> SimpleNamespace:
    components = {
        item["component"]["S"]: MerchantTruthComponent.from_item(item).payload
        for item in payload_document["items"]
        if item.get("TYPE", {}).get("S") == "MERCHANT_TRUTH_COMPONENT"
    }
    return SimpleNamespace(
        slug=slug, version=1, incomplete=False, components=components
    )


@pytest.fixture(scope="module")
def registry_document() -> dict[str, Any]:
    with open(PROFILES_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def minted(registry_document: dict[str, Any]):
    source = FakeSource(sorted(registry_document["profiles"]))
    payloads, _ = build_v1_payloads(
        registry_document,
        source,  # type: ignore[arg-type]
        profiles_source_path="scripts/merchant_profiles.json",
        git_sha=GIT_SHA,
        generated_at=NOW,
    )
    return payloads


def test_every_legacy_profile_reconstructs_byte_identically(
    registry_document, minted
) -> None:
    profiles = registry_document["profiles"]
    assert len(minted) == len(profiles) == 16
    for payload in minted:
        artifact = _artifact(payload.to_document(), payload.slug)
        name, reconstructed = rsr._profile_from_truth(artifact)
        assert name == payload.merchant_name
        expected = _strip_private(profiles[name])
        assert json.dumps(reconstructed, sort_keys=True) == json.dumps(
            expected, sort_keys=True
        ), f"profile round-trip drifted for {name}"


def test_section_scale_shim_matches_the_registry(registry_document) -> None:
    """The shim set must equal the profiles with an EXPLICIT empty map."""
    explicit_empty = {
        slugify_merchant(name)
        for name, profile in registry_document["profiles"].items()
        if "section_scale" in profile and profile["section_scale"] == {}
    }
    assert explicit_empty == set(rsr._V1_EXPLICIT_EMPTY_SECTION_SCALE)
