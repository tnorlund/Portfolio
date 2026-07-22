"""Integration coverage for the crosswalk-notes import (W-I).

Drives the CLI end-to-end against a moto-backed table: the v1 mint git_sha is
read from a seeded v1 manifest, dedupe runs against a seeded self-checkout
proposal through the governed ``add_proposal`` accessor, and a second --apply
proves idempotency. The ``git show`` read is injected so the test is git-free.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Any

import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.merchant_truth import (
    COMPONENT_NAMES,
    MerchantTruthManifest,
    MerchantTruthProposal,
    compute_bundle_hash,
)

pytestmark = pytest.mark.integration

SHA = "eb9d3617122be21d0e0e956e04f3208aec701c29"

COSTCO_COMMENT = (
    "Real font extracted from the bitMatrix-C2 chart. SELF-CHECKOUT prints "
    "as a large bold heading."
)
SELF_CHECKOUT_CLAIM = (
    "Costco receipts differ from self-checkout receipts; measurement should "
    "cluster by variant. Owner observation."
)


def _document() -> dict[str, Any]:
    return {
        "profiles": {
            "Costco Wholesale": {
                "_comment": COSTCO_COMMENT,
                "layout_template": {"_comment": "Measured layout data."},
                "typography": {"_face_source_comment": "M4 pilot flag."},
            },
            "Vons": {"_comment": "First studio-native merchant."},
        }
    }


def _seed_v1_manifest(client: DynamoClient, table: str) -> None:
    hashes = {
        name: hashlib.sha256(name.encode()).hexdigest()
        for name in COMPONENT_NAMES
    }
    manifest = MerchantTruthManifest(
        slug="costco_wholesale",
        version=1,
        component_hashes=hashes,
        bundle_hash=compute_bundle_hash(hashes),
        status="SEALED",
        provenance={"git_sha": SHA, "written_by": "test"},
        mint_run_id="merchant-truth-v1-costco_wholesale-test",
    )
    client._client.put_item(TableName=table, Item=manifest.to_item())


def _load_cli():
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "import_crosswalk_notes.py"
    )
    spec = importlib.util.spec_from_file_location(
        "import_crosswalk_notes", path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _claim_slugs(client: DynamoClient, slug: str) -> set[str]:
    return {p.claim_slug for p in client.list_merchant_truth_proposals(slug)}


def test_apply_resolves_git_sha_dedupes_and_is_idempotent(
    dynamodb_table: str, monkeypatch
) -> None:
    client = DynamoClient(dynamodb_table)
    _seed_v1_manifest(client, dynamodb_table)
    # A prior owner proposal touching the self-checkout observation.
    client.add_proposal(
        MerchantTruthProposal(
            slug="costco_wholesale",
            created_at="2026-07-21T23:18:30.430210+00:00",
            claim_slug="self-checkout-layout-variant",
            claim=SELF_CHECKOUT_CLAIM,
        ),
        dynamodb_table,
    )

    module = _load_cli()
    monkeypatch.setattr(
        module, "_git_show_document", lambda repo_root, git_sha: _document()
    )

    # No --git-sha: resolved from the seeded v1 manifest provenance.
    rc = module.main(["--table", dynamodb_table, "--apply"])
    assert rc == 0

    costco = _claim_slugs(client, "costco_wholesale")
    # Every Costco leaf persists: the _comment is annotated, NOT suppressed.
    assert costco == {
        "self-checkout-layout-variant",
        "profile-comment",
        "layout-template-comment",
        "typography-face-source-comment",
    }
    assert _claim_slugs(client, "vons") == {"profile-comment"}

    import json

    proposals = client.list_merchant_truth_proposals("costco_wholesale")
    # The written Costco layout note carries the resolved git_sha in its claim.
    layout = next(
        p for p in proposals if p.claim_slug == "layout-template-comment"
    )
    assert json.loads(layout.claim)["provenance"]["git_sha"] == SHA
    # The full _comment persists verbatim, cross-referencing the owner proposal.
    profile = next(p for p in proposals if p.claim_slug == "profile-comment")
    profile_env = json.loads(profile.claim)
    assert profile_env["text"] == COSTCO_COMMENT
    assert profile_env["related_to"] == "self-checkout-layout-variant"

    # Second apply writes nothing new (idempotent).
    rc2 = module.main(["--table", dynamodb_table, "--apply"])
    assert rc2 == 0
    assert _claim_slugs(client, "costco_wholesale") == costco
    assert _claim_slugs(client, "vons") == {"profile-comment"}
