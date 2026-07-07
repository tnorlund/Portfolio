"""Regression tests for the MerchantFont cache fetch in render_synthetic_receipts.

Covers two cold-cache bugs:

A. Alias merchant names ("CVS pharmacy", "TRADER JOE'S") must resolve to the
   canonical profile key before the Dynamo pointer lookup, because pointers are
   published under the canonical name ("CVS", "Trader Joe's").
B. Logo (and stylemap) faces ride on the REGULAR pointer under their own key
   fields; the atlas ``cache_filename`` guard must not reject them, or the logo
   PNG never downloads on a fresh host.
"""

import hashlib
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_synthetic_receipts as rsr  # noqa: E402


class _FakePtr:
    def __init__(self, **kw):
        self.s3_bucket = kw.get("s3_bucket", "raw-bucket")
        self.s3_key = kw.get("s3_key")
        self.content_hash = kw.get("content_hash")
        self.cache_filename = kw.get("cache_filename")
        self.logo_s3_key = kw.get("logo_s3_key")
        self.stylemap_s3_key = kw.get("stylemap_s3_key")


def _install_fakes(monkeypatch, tmp_path, ptr, payload):
    """Point the cache dir at tmp, and fake DynamoClient + boto3 S3."""
    monkeypatch.setattr(rsr, "_BITMATRIX_DIR", str(tmp_path))
    calls = []

    class FakeClient:
        def __init__(self, table):
            pass

        def get_merchant_font(self, merchant, face):
            calls.append((merchant, face))
            return ptr

    fake_dynamo = types.ModuleType("receipt_dynamo")
    fake_dynamo.DynamoClient = FakeClient
    monkeypatch.setitem(sys.modules, "receipt_dynamo", fake_dynamo)

    class FakeS3:
        def download_file(self, bucket, key, dest):
            with open(dest, "wb") as fh:
                fh.write(payload)

    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda *a, **k: FakeS3()
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    return calls


def test_alias_resolves_to_canonical_pointer(monkeypatch, tmp_path):
    """Finding A: a receipt alias fetches the pointer under the canonical name."""
    payload = b"ATLAS-BYTES"
    ptr = _FakePtr(
        s3_key="merchant_fonts/cvs/regular-abc.npz",
        content_hash=hashlib.sha256(payload).hexdigest(),
        cache_filename="cvs.glyphs.npz",
    )
    calls = _install_fakes(monkeypatch, tmp_path, ptr, payload)

    out = rsr._ensure_font_cached("cvs.glyphs.npz", "CVS pharmacy", "regular")

    # The Dynamo lookup used the canonical key, not the receipt's alias.
    assert calls == [("CVS", "regular")]
    # And the font actually landed in the cache (fetch happened, not dropped).
    assert os.path.exists(out)
    with open(out, "rb") as fh:
        assert fh.read() == payload


def test_alias_trader_joes_uppercase(monkeypatch, tmp_path):
    """Finding A: the SCREAMING-CASE Trader Joe's variant resolves too."""
    payload = b"TJ-ATLAS"
    ptr = _FakePtr(
        s3_key="merchant_fonts/trader_joe_s/regular-def.npz",
        content_hash=hashlib.sha256(payload).hexdigest(),
        cache_filename="tj.glyphs.npz",
    )
    calls = _install_fakes(monkeypatch, tmp_path, ptr, payload)

    rsr._ensure_font_cached("tj.glyphs.npz", "TRADER JOE'S", "regular")

    assert calls == [("Trader Joe's", "regular")]


def test_logo_downloads_despite_atlas_cache_filename(monkeypatch, tmp_path):
    """Finding B: a logo PNG downloads even though the regular pointer's
    cache_filename names the atlas npz, not the logo file."""
    payload = b"\x89PNG-LOGO"
    ptr = _FakePtr(
        s3_key="merchant_fonts/cvs/regular-abc.npz",
        content_hash="unused-for-logo",
        cache_filename="cvs.glyphs.npz",  # atlas name != the logo filename
        logo_s3_key="merchant_fonts/cvs/logo-1234.png",
    )
    calls = _install_fakes(monkeypatch, tmp_path, ptr, payload)

    out = rsr._ensure_font_cached("cvs_logo.png", "CVS", "logo")

    assert calls == [("CVS", "regular")]  # logo rides the regular pointer
    assert os.path.exists(out)  # previously dropped by the atlas guard
    with open(out, "rb") as fh:
        assert fh.read() == payload


def test_atlas_guard_still_rejects_mismatched_filename(monkeypatch, tmp_path):
    """The guard must still protect regular atlases from masquerading builds."""
    payload = b"WRONG-ATLAS"
    ptr = _FakePtr(
        s3_key="merchant_fonts/costco/regular-abc.npz",
        content_hash=hashlib.sha256(payload).hexdigest(),
        cache_filename="costco-chart.glyphs.npz",  # != requested filename
    )
    _install_fakes(monkeypatch, tmp_path, ptr, payload)

    out = rsr._ensure_font_cached(
        "costco-studio.glyphs.npz", "Costco Wholesale", "regular"
    )
    # Guard rejects: nothing downloaded, local (missing) path returned.
    assert not os.path.exists(out)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
