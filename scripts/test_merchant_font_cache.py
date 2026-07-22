"""Fail-closed contracts for the renderer's MerchantTruth asset cache.

The renderer resolves every per-merchant profile and binary artifact through
the merchant's ACTIVE truth bundle (``MerchantTruthLoader``). These tests pin
the fail-closed behavior that replaced the legacy MerchantFont fetch:

* fetched and cached bytes are verified against the bundle's content hash —
  a tampered cache or a mismatched download RAISES, never renders;
* a NAMED merchant without an ACTIVE bundle errors clearly (no silent
  legacy-path fallback); a missing merchant (None) stays the generic render;
* alias variants (declared and normalized) resolve to the canonical record;
* the explicit degraded modes (fixture / offline-fallback) may render
  without atlases BY DESIGN, loudly marked — normal runs may not.
"""

from __future__ import annotations

import hashlib
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "receipt_dynamo"))
import render_synthetic_receipts as rsr  # noqa: E402

from receipt_dynamo.data.shared_exceptions import (  # noqa: E402
    MerchantTruthIntegrityError,
)

FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "merchant_truth",
    "fixture_mart.v1.json",
)
ATLAS_BYTES = b"fixture-mart-atlas-bytes\n"
LOGO_BYTES = b"fixture-mart-logo-bytes\n"


def _load_fixture_artifact(cache_dir: Path):
    from receipt_dynamo.merchant_truth_loader import (
        MerchantTruthLoader,
        TruthResolutionMode,
    )

    return MerchantTruthLoader(None, cache_dir).load(
        "", TruthResolutionMode.FIXTURE, fixture_path=Path(FIXTURE)
    )


@pytest.fixture
def fixture_mode(monkeypatch, tmp_path):
    """Registry built from the committed fixture, in explicit fixture mode."""
    monkeypatch.setenv("MERCHANT_TRUTH_MODE", "fixture")
    monkeypatch.setenv("MERCHANT_TRUTH_FIXTURE", FIXTURE)
    monkeypatch.setenv(
        "MERCHANT_TRUTH_CACHE_DIR", str(tmp_path / "truth_cache")
    )
    monkeypatch.setattr(rsr, "_BITMATRIX_DIR", str(tmp_path / "bitmatrix"))
    rsr._reset_merchant_truth_registry()
    yield tmp_path
    rsr._reset_merchant_truth_registry()


@pytest.fixture
def online_mode(monkeypatch, tmp_path):
    """Same fixture bundle, but the registry claims normal online mode so the
    fail-closed fetch/verify paths are exercised (S3 is faked per-test)."""
    monkeypatch.delenv("MERCHANT_TRUTH_MODE", raising=False)
    monkeypatch.setattr(rsr, "_BITMATRIX_DIR", str(tmp_path / "bitmatrix"))
    artifact = _load_fixture_artifact(tmp_path / "truth_cache")
    registry = rsr._MerchantTruthRegistry(
        "online-active", {artifact.slug: artifact}, []
    )
    monkeypatch.setattr(rsr, "_MERCHANT_TRUTH_REGISTRY", registry)
    yield tmp_path
    rsr._reset_merchant_truth_registry()


def _fake_s3(monkeypatch, payload: bytes):
    calls: list[tuple[str, str]] = []

    class FakeS3:
        def download_file(self, bucket, key, dest):
            calls.append((bucket, key))
            with open(dest, "wb") as fh:
                fh.write(payload)

    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda *a, **k: FakeS3()
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    return calls


def _local(tmp_path: Path, filename: str) -> str:
    return str(tmp_path / "bitmatrix" / filename)


# ---------------------------------------------------------------------------
# Hash-verified, fail-closed artifact fetches (online mode).
# ---------------------------------------------------------------------------


def test_online_fetch_downloads_and_verifies_hash(online_mode, monkeypatch):
    calls = _fake_s3(monkeypatch, ATLAS_BYTES)

    out = rsr._ensure_font_cached(
        "fixture_mart.glyphs.npz", "Fixture Mart", "regular"
    )

    assert calls and calls[0][0] == "raw-image-bucket-c779c32"
    with open(out, "rb") as fh:
        assert fh.read() == ATLAS_BYTES


def test_online_fetch_hash_mismatch_fails_closed(online_mode, monkeypatch):
    _fake_s3(monkeypatch, b"NOT-THE-PUBLISHED-ATLAS")

    with pytest.raises(MerchantTruthIntegrityError, match="does not match"):
        rsr._ensure_font_cached(
            "fixture_mart.glyphs.npz", "Fixture Mart", "regular"
        )

    local = _local(online_mode, "fixture_mart.glyphs.npz")
    assert not os.path.exists(local)
    assert not os.path.exists(local + ".fetch")


def test_tampered_cached_atlas_refuses_to_render(online_mode, monkeypatch):
    """The required tamper test: corrupt one cached npz byte -> raise."""
    calls = _fake_s3(monkeypatch, ATLAS_BYTES)
    out = rsr._ensure_font_cached(
        "fixture_mart.glyphs.npz", "Fixture Mart", "regular"
    )
    assert len(calls) == 1

    corrupted = bytearray(ATLAS_BYTES)
    corrupted[0] ^= 0xFF
    with open(out, "wb") as fh:
        fh.write(bytes(corrupted))

    with pytest.raises(MerchantTruthIntegrityError, match="Refusing"):
        rsr._ensure_font_cached(
            "fixture_mart.glyphs.npz", "Fixture Mart", "regular"
        )
    # And it never fell back to a re-download that would mask the tamper.
    assert len(calls) == 1


def test_valid_cached_atlas_is_verified_then_trusted(online_mode, monkeypatch):
    calls = _fake_s3(monkeypatch, ATLAS_BYTES)
    local = _local(online_mode, "fixture_mart.glyphs.npz")
    os.makedirs(os.path.dirname(local), exist_ok=True)
    with open(local, "wb") as fh:
        fh.write(ATLAS_BYTES)

    out = rsr._ensure_font_cached(
        "fixture_mart.glyphs.npz", "Fixture Mart", "regular"
    )

    assert out == local
    assert calls == []  # verified cache hit, no fetch


def test_logo_fetch_is_hash_verified(online_mode, monkeypatch):
    calls = _fake_s3(monkeypatch, LOGO_BYTES)

    out = rsr._ensure_font_cached(
        "fixture_mart_logo.png", "Fixture Mart", "logo"
    )

    assert calls and calls[0][1].endswith(".png")
    with open(out, "rb") as fh:
        assert fh.read() == LOGO_BYTES


def test_undeclared_filename_fails_closed(online_mode, monkeypatch):
    _fake_s3(monkeypatch, ATLAS_BYTES)

    with pytest.raises(MerchantTruthIntegrityError, match="no regular"):
        rsr._ensure_font_cached(
            "studio-masquerade.glyphs.npz", "Fixture Mart", "regular"
        )


def test_face_may_map_onto_another_faces_file(monkeypatch, tmp_path):
    """Italia Deli / Neighborly: the profile deliberately renders ``heavy``
    with the regular atlas file; resolution is by filename, still hashed."""
    monkeypatch.setattr(rsr, "_BITMATRIX_DIR", str(tmp_path / "bitmatrix"))
    atlas_hash = hashlib.sha256(ATLAS_BYTES).hexdigest()
    artifact = SimpleNamespace(
        slug="italia_deli___bakery",
        version=1,
        incomplete=False,
        components={
            "identity": {
                "merchant_name": "Italia Deli & Bakery",
                "slug": "italia_deli___bakery",
                "aliases": [],
                "normalized_aliases": ["italia deli bakery"],
            },
            "typography": {
                "typography": {
                    "bitmap_font": {
                        "regular": "italiadeli.glyphs.npz",
                        "heavy": "italiadeli.glyphs.npz",
                    }
                },
                "section_scale": {},
            },
            "flags": {},
            "layout": {"available": False, "template": None},
            "stylemap": {"available": False, "document": None},
            "assets": {
                "fonts": {
                    "regular": {
                        "bucket_alias": "merchant-font-artifacts",
                        "s3_key": "merchant_fonts/italia/regular.npz",
                        "content_hash": atlas_hash,
                        "cache_filename": "italiadeli.glyphs.npz",
                    },
                    "heavy": {
                        "bucket_alias": "merchant-font-artifacts",
                        "s3_key": "merchant_fonts/italia/heavy.npz",
                        "content_hash": "f" * 64,
                        "cache_filename": "italiadeli-heavy.glyphs.npz",
                    },
                },
                "profile": {},
                "logo": None,
            },
            "catalog_snapshot": {"items": []},
        },
    )
    registry = rsr._MerchantTruthRegistry(
        "online-active", {artifact.slug: artifact}, []
    )
    monkeypatch.setattr(rsr, "_MERCHANT_TRUTH_REGISTRY", registry)
    calls = _fake_s3(monkeypatch, ATLAS_BYTES)

    out = rsr._ensure_font_cached(
        "italiadeli.glyphs.npz", "Italia Deli & Bakery", "heavy"
    )

    # Resolved to the REGULAR pointer (the file the profile asked for) and
    # verified against ITS hash — not the heavy pointer's.
    assert calls == [
        ("raw-image-bucket-c779c32", "merchant_fonts/italia/regular.npz")
    ]
    with open(out, "rb") as fh:
        assert fh.read() == ATLAS_BYTES
    rsr._reset_merchant_truth_registry()


# ---------------------------------------------------------------------------
# Merchant resolution: aliases resolve, missing bundles error clearly.
# ---------------------------------------------------------------------------


def test_canonical_alias_and_normalized_variants_resolve(fixture_mode):
    canonical, profile = rsr.get_merchant_profile_key("Fixture Mart")
    assert canonical == "Fixture Mart"
    assert profile["typography"]["condense"] == 0.9

    by_alias = rsr.get_merchant_profile_key("FIXTURE MART #001")
    assert by_alias[0] == "Fixture Mart"
    assert by_alias[1] is profile

    normalized = rsr.get_merchant_profile_key("fixture-MART!!!")
    assert normalized[0] == "Fixture Mart"
    assert normalized[1] is profile


def test_merchant_without_active_bundle_errors_clearly(fixture_mode):
    with pytest.raises(
        MerchantTruthIntegrityError,
        match="no ACTIVE merchant-truth bundle .*'Dollar Tree'",
    ):
        rsr.get_merchant_profile("Dollar Tree")


def test_missing_merchant_stays_the_generic_render(fixture_mode):
    assert rsr.get_merchant_profile(None) == {}
    assert rsr.get_merchant_profile("") == {}
    assert rsr.section_scale_for_merchant(None) == {"HEADER": 0.80}


# ---------------------------------------------------------------------------
# Explicit degraded modes: CI renders without atlases BY DESIGN, marked.
# ---------------------------------------------------------------------------


def test_fixture_mode_missing_atlas_drops_marked(fixture_mode, capsys):
    typography = rsr.merchant_typography("Fixture Mart")

    # Atlas is not cached and fixture mode never fetches: the bitmap font is
    # dropped (the legacy CI-degraded render) but LOUDLY, never silently.
    assert "bitmap_font" not in typography
    assert typography["dashed_separators"] is True
    out = capsys.readouterr().out
    assert "FIXTURE mode" in out
    assert "DEGRADED (fixture)" in out


def test_fixture_mode_uses_verified_cached_atlas(fixture_mode):
    local = _local(fixture_mode, "fixture_mart.glyphs.npz")
    os.makedirs(os.path.dirname(local), exist_ok=True)
    with open(local, "wb") as fh:
        fh.write(ATLAS_BYTES)

    typography = rsr.merchant_typography("Fixture Mart")

    assert typography["bitmap_font"]["regular"] == local


def test_fixture_mode_still_refuses_tampered_cache(fixture_mode):
    """Degraded mode tolerates ABSENT artifacts, never WRONG ones."""
    local = _local(fixture_mode, "fixture_mart.glyphs.npz")
    os.makedirs(os.path.dirname(local), exist_ok=True)
    with open(local, "wb") as fh:
        fh.write(b"tampered-atlas")

    with pytest.raises(MerchantTruthIntegrityError, match="Refusing"):
        rsr.merchant_typography("Fixture Mart")


def test_stylemap_comes_inline_from_the_bundle(fixture_mode):
    """No file fetch: the stylemap document is the bundle's verified copy."""
    profile = rsr.get_merchant_profile("Fixture Mart")
    assert "stylemap" not in profile.get("typography", {})

    # The fixture bundle carries an available stylemap but no
    # profile-declared filename, mirroring Costco; declare one to exercise
    # the inline read.
    profile["typography"]["stylemap"] = "fixture_mart.stylemap.json"
    typography = rsr.merchant_typography("Fixture Mart")
    assert typography["stylemap"]["sections"] == {
        "HEADER": {"face": "regular"}
    }


def test_stylemap_declared_but_unavailable_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(rsr, "_BITMATRIX_DIR", str(tmp_path / "bitmatrix"))
    artifact = SimpleNamespace(
        slug="fixture_mart",
        version=1,
        incomplete=False,
        components={
            "identity": {
                "merchant_name": "Fixture Mart",
                "slug": "fixture_mart",
                "aliases": [],
                "normalized_aliases": ["fixture mart"],
            },
            "typography": {"typography": {}, "section_scale": {}},
            "flags": {},
            "layout": {"available": False, "template": None},
            "stylemap": {"available": False, "document": None},
            "assets": {
                "fonts": {},
                "profile": {"stylemap_filename": "fixture_mart.stylemap.json"},
                "logo": None,
            },
            "catalog_snapshot": {"items": []},
        },
    )
    registry = rsr._MerchantTruthRegistry(
        "online-active", {artifact.slug: artifact}, []
    )
    monkeypatch.setattr(rsr, "_MERCHANT_TRUTH_REGISTRY", registry)

    with pytest.raises(
        MerchantTruthIntegrityError, match="no stylemap document"
    ):
        rsr.merchant_typography("Fixture Mart")
    rsr._reset_merchant_truth_registry()


def test_missing_asset_pointer_online_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(rsr, "_BITMATRIX_DIR", str(tmp_path / "bitmatrix"))
    artifact = SimpleNamespace(
        slug="fixture_mart",
        version=1,
        incomplete=False,
        components={
            "identity": {
                "merchant_name": "Fixture Mart",
                "slug": "fixture_mart",
                "aliases": [],
                "normalized_aliases": ["fixture mart"],
            },
            "typography": {"typography": {}, "section_scale": {}},
            "flags": {},
            "layout": {"available": False, "template": None},
            "stylemap": {"available": False, "document": None},
            "assets": {"fonts": {}, "profile": {}, "logo": None},
            "catalog_snapshot": {"items": []},
        },
    )
    registry = rsr._MerchantTruthRegistry(
        "online-active", {artifact.slug: artifact}, []
    )
    monkeypatch.setattr(rsr, "_MERCHANT_TRUTH_REGISTRY", registry)

    with pytest.raises(
        MerchantTruthIntegrityError, match="no logo asset pointer"
    ):
        rsr._ensure_font_cached("some_logo.png", "Fixture Mart", "logo")
    rsr._reset_merchant_truth_registry()


# ---------------------------------------------------------------------------
# v1 section_scale fidelity shim.
# ---------------------------------------------------------------------------


def _shim_artifact(slug: str, version: int = 1):
    return SimpleNamespace(
        slug=slug,
        version=version,
        incomplete=False,
        components={
            "identity": {
                "merchant_name": slug,
                "slug": slug,
                "aliases": [],
                "normalized_aliases": [slug.replace("_", " ")],
            },
            "typography": {"typography": {}, "section_scale": {}},
            "flags": {},
            "layout": {"available": False, "template": None},
            "stylemap": {"available": False, "document": None},
            "assets": {"fonts": {}, "profile": {}, "logo": None},
            "catalog_snapshot": {"items": []},
        },
    )


def test_v1_empty_section_scale_shim_distinguishes_explicit_from_absent():
    # Sprouts' legacy profile had an EXPLICIT {} (uniform, no shrink).
    _, explicit = rsr._profile_from_truth(
        _shim_artifact("sprouts_farmers_market")
    )
    assert explicit["section_scale"] == {}

    # Vons' legacy profile had NO key (default HEADER shrink applies).
    _, absent = rsr._profile_from_truth(_shim_artifact("vons"))
    assert "section_scale" not in absent

    # The shim is a v1-migration artifact only: a v2 bundle must carry the
    # distinction itself.
    _, v2 = rsr._profile_from_truth(
        _shim_artifact("sprouts_farmers_market", version=2)
    )
    assert "section_scale" not in v2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
