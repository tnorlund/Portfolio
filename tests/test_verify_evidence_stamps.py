"""Tests for committed evidence provenance verification."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import verify_evidence_stamps as verifier

FIXTURES = Path(__file__).parent / "fixtures" / "evidence"
COSTCO_V1_HASH = (
    "c5cd31202f7a52cee5f5b492c675ae4ed1d94241529bdc3d16d85b18bf7fd064"
)


def _costco_active() -> verifier.ActiveFleet:
    return {"costco_wholesale": (1, COSTCO_V1_HASH)}


def test_costco_v2_fixture_is_rejected_with_both_bundles_named():
    path = FIXTURES / "costco_v2_fixture_stamp.json"
    document = json.loads(path.read_text(encoding="utf-8"))

    findings = verifier.verify_document(
        path,
        document,
        _costco_active(),
        "HEAD",
        is_ancestor=lambda _sha, _head: True,
    )

    assert any("mode is 'fixture'" in finding for finding in findings)
    mismatch = next(
        finding
        for finding in findings
        if "evidence describes a bundle nobody uses" in finding
    )
    assert "costco_wholesale v2 (6b709eb0…)" in mismatch
    assert "ACTIVE is v1 (c5cd3120…)" in mismatch


def test_clean_online_active_stamp_passes(tmp_path):
    path = tmp_path / "after.json"
    document = {
        "stamp": {
            "dirty": False,
            "git_sha": "abc123",
            "merchant_truth": {
                "bundle_hash": COSTCO_V1_HASH,
                "mode": "online-active",
                "slug": "costco_wholesale",
                "version": 1,
            },
        }
    }

    assert (
        verifier.verify_document(
            path,
            document,
            _costco_active(),
            "pr-head",
            is_ancestor=lambda sha, head: (sha, head) == ("abc123", "pr-head"),
        )
        == []
    )


def test_dirty_and_non_ancestor_stamp_fail(tmp_path):
    path = tmp_path / "after.json"
    document = {
        "stamp": {
            "dirty": True,
            "git_sha": "branch-only",
            "merchant_truth": {
                "bundle_hash": COSTCO_V1_HASH,
                "mode": "online-active",
                "slug": "costco_wholesale",
                "version": 1,
            },
        }
    }

    findings = verifier.verify_document(
        path,
        document,
        _costco_active(),
        "pr-head",
        is_ancestor=lambda _sha, _head: False,
    )

    assert any("dirty worktree" in finding for finding in findings)
    assert any("not an ancestor" in finding for finding in findings)


def test_legacy_receipt_slug_is_accepted(tmp_path):
    path = tmp_path / "before.json"
    document = {
        "receipt": {"slug": "costco_wholesale"},
        "stamp": {
            "git_sha": "abc123",
            "merchant_truth": {
                "bundle_hash": COSTCO_V1_HASH,
                "mode": "online-active",
                "version": 1,
            },
        },
    }

    assert (
        verifier.verify_document(
            path,
            document,
            _costco_active(),
            "HEAD",
            is_ancestor=lambda _sha, _head: True,
        )
        == []
    )


def test_snapshot_refresh_uses_strong_reads():
    class Active:
        def __init__(self, slug, version, bundle_hash):
            self.slug = slug
            self.version = version
            self.bundle_hash = bundle_hash

    class Reader:
        def __init__(self):
            self.strong_reads = []

        def list_active_merchant_truth(self):
            return [Active("costco_wholesale", 1, "eventual")]

        def get_active_merchant_truth(self, slug, *, consistent_read=False):
            self.strong_reads.append((slug, consistent_read))
            return Active(slug, 1, COSTCO_V1_HASH)

    reader = Reader()
    document = verifier._snapshot_document(reader, "ReceiptsTable-dev")

    assert reader.strong_reads == [("costco_wholesale", True)]
    assert document["active"]["costco_wholesale"] == {
        "bundle_hash": COSTCO_V1_HASH,
        "version": 1,
    }
