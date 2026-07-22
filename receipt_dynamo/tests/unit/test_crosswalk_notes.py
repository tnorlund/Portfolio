"""Unit coverage for the crosswalk-notes import (W-I).

Pure logic is exercised with injected file content (git-free); the CLI is
exercised with an injected ``git show`` read and a fake governed client so no
AWS or git access is required.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from receipt_dynamo.data.shared_exceptions import (
    MerchantTruthTableMismatchError,
)
from receipt_dynamo.entities.merchant_truth import MerchantTruthProposal
from receipt_dynamo.migrations.crosswalk_notes import (
    build_claim,
    derive_claim_slug,
    extract_comment_leaves,
    extract_text_from_claim,
    find_related,
    plan_import,
    verify_persistence,
)
from receipt_dynamo.migrations.merchant_truth_v1_live import PROD_TABLE_NAME

pytestmark = pytest.mark.unit

NOW = "2026-07-22T18:00:00+00:00"
SHA = "eb9d3617122be21d0e0e956e04f3208aec701c29"

COSTCO_COMMENT = (
    "Real font extracted from the bitMatrix-C2 chart. SELF-CHECKOUT prints "
    "as a large bold heading; the grand TOTAL prints reverse-video."
)
LAYOUT_COMMENT = (
    "Measured layout data (#1188 P2): columns/sections/separators from real "
    "receipts via glyphstudio.layout_template."
)
# The real Costco owner proposal - carries the hyphenated compound
# "self-checkout" that links it to the Costco _comment.
SELF_CHECKOUT_CLAIM = (
    "Costco receipts have distinct formatting variants: warehouse register "
    "receipts differ from self-checkout receipts. v1 layout_template pools "
    "all 12 measured receipts into one template; measurement should cluster "
    "by variant and either confirm bimodal layout (variant-aware truth in "
    "v2) or refute. Owner observation, 2026-07-21."
)


def _document() -> dict[str, Any]:
    """A miniature profile document with per-merchant + registry comments."""
    return {
        # Registry-level docs are OUT of scope and must be ignored.
        "_comment": "top-level registry note, not a merchant leaf",
        "_section_scale_note": "registry documentation only",
        "profiles": {
            "Costco Wholesale": {
                "_comment": COSTCO_COMMENT,
                "layout_template": {"_comment": LAYOUT_COMMENT, "columns": 3},
                "typography": {
                    "_face_source_comment": "M4 pilot flag.",
                    "condense": 0.93,
                },
            },
            "Vons": {
                "_comment": "First studio-native merchant.",
            },
        },
    }


def _seeded_proposal(
    slug: str, claim_slug: str, claim: str
) -> MerchantTruthProposal:
    return MerchantTruthProposal(
        slug=slug,
        created_at="2026-07-21T23:18:30.430210+00:00",
        claim_slug=claim_slug,
        claim=claim,
    )


# --------------------------------------------------------------------------
# extraction (git-free, injected content)
# --------------------------------------------------------------------------


def test_extract_comment_leaves_from_injected_document() -> None:
    leaves = extract_comment_leaves(_document())
    by_path = {leaf.leaf_path: leaf for leaf in leaves}

    # Registry-level docs are excluded; only merchant-owned comments captured.
    assert "_comment" not in by_path
    assert set(by_path) == {
        "profiles.Costco Wholesale._comment",
        "profiles.Costco Wholesale.layout_template._comment",
        "profiles.Costco Wholesale.typography._face_source_comment",
        "profiles.Vons._comment",
    }

    costco = by_path["profiles.Costco Wholesale._comment"]
    assert costco.slug == "costco_wholesale"
    assert costco.text == COSTCO_COMMENT  # verbatim
    assert costco.claim_slug == "profile-comment"

    layout = by_path["profiles.Costco Wholesale.layout_template._comment"]
    assert layout.claim_slug == "layout-template-comment"

    face = by_path["profiles.Costco Wholesale.typography._face_source_comment"]
    assert face.claim_slug == "typography-face-source-comment"


def test_derive_claim_slug_cases() -> None:
    assert derive_claim_slug("_comment") == "profile-comment"
    assert (
        derive_claim_slug("layout_template._comment")
        == "layout-template-comment"
    )
    assert (
        derive_claim_slug("typography._face_source_comment")
        == "typography-face-source-comment"
    )


def test_build_claim_envelope_roundtrips_text_and_carries_provenance() -> None:
    claim = build_claim(
        COSTCO_COMMENT, "profiles.Costco Wholesale._comment", SHA
    )
    envelope = json.loads(claim)
    assert extract_text_from_claim(claim) == COSTCO_COMMENT  # byte-identical
    assert envelope["provenance"] == {
        "source": "profile-comment",
        "leaf_path": "profiles.Costco Wholesale._comment",
        "git_sha": SHA,
    }
    assert "related_to" not in envelope


def test_build_claim_envelope_carries_related_to_without_touching_text() -> (
    None
):
    claim = build_claim(
        COSTCO_COMMENT,
        "profiles.Costco Wholesale._comment",
        SHA,
        related_to="self-checkout-layout-variant",
    )
    envelope = json.loads(claim)
    assert envelope["related_to"] == "self-checkout-layout-variant"
    assert envelope["text"] == COSTCO_COMMENT  # byte-identical, untouched


# --------------------------------------------------------------------------
# relatedness is a NON-suppressing annotation (mutation-checked)
# --------------------------------------------------------------------------


def test_related_leaf_still_writes_full_record_with_reference() -> None:
    """The multi-topic Costco _comment is NOT dropped: it writes its own full
    record, annotated with related_to referencing the self-checkout proposal.
    The neighbouring notes are not falsely annotated."""
    existing = [
        _seeded_proposal(
            "costco_wholesale",
            "self-checkout-layout-variant",
            SELF_CHECKOUT_CLAIM,
        )
    ]
    leaves = extract_comment_leaves(_document())
    plan = plan_import(
        leaves,
        {"costco_wholesale": existing},
        git_sha=SHA,
        created_at=NOW,
    )
    actions = {d.leaf.leaf_path: d for d in plan.decisions}

    costco = actions["profiles.Costco Wholesale._comment"]
    assert costco.action == "WRITE"  # written, never suppressed
    assert costco.related_to == "self-checkout-layout-variant"
    assert costco.matched_phrase == "self-checkout"
    # The full note persists verbatim, plus the cross-reference.
    assert costco.proposal is not None
    envelope = json.loads(costco.proposal.claim)
    assert envelope["text"] == COSTCO_COMMENT
    assert envelope["related_to"] == "self-checkout-layout-variant"

    # Neighbours: written, and NOT falsely related.
    for path in (
        "profiles.Costco Wholesale.layout_template._comment",
        "profiles.Costco Wholesale.typography._face_source_comment",
        "profiles.Vons._comment",
    ):
        assert actions[path].action == "WRITE"
        assert actions[path].related_to is None

    # Nothing suppressed: every leaf is a WRITE here.
    assert len(plan.to_write) == 4
    assert len(plan.related) == 1


def test_common_bigram_does_not_trigger_relatedness() -> None:
    """A shared common bigram (e.g. 'font size') must NOT relate leaves - the
    threshold is three contiguous tokens (or a distinctive hyphenated
    compound), which guards against the reviewer's false-positive class."""
    leaf = extract_comment_leaves(
        {"profiles": {"Vons": {"_comment": "the font size is small"}}}
    )[0]
    existing = [
        _seeded_proposal("vons", "type-scale", "increase font size slightly")
    ]
    assert find_related(leaf, existing) is None


def test_find_related_returns_none_without_shared_signal() -> None:
    existing = [
        _seeded_proposal("vons", "self-checkout-layout-variant", "irrelevant")
    ]
    leaves = extract_comment_leaves(_document())
    vons = next(leaf for leaf in leaves if leaf.slug == "vons")
    assert find_related(vons, existing) is None


# --------------------------------------------------------------------------
# idempotent re-run
# --------------------------------------------------------------------------


def test_idempotent_rerun_skips_already_imported_claim_slug() -> None:
    """A prior import of the same leaf (matching claim_slug) is skipped, and no
    duplicate proposal is built."""
    already = [
        _seeded_proposal(
            "vons",
            "profile-comment",
            build_claim("First studio-native merchant.", "x", SHA),
        )
    ]
    leaves = [
        leaf
        for leaf in extract_comment_leaves(_document())
        if leaf.slug == "vons"
    ]
    plan = plan_import(leaves, {"vons": already}, git_sha=SHA, created_at=NOW)
    assert [d.action for d in plan.decisions] == ["SKIP_EXISTS"]
    assert plan.to_write == []


# --------------------------------------------------------------------------
# persistence / extraction-faithfulness (planless + mutated leaf go red)
# --------------------------------------------------------------------------


def test_verify_persistence_clean_when_every_leaf_writes() -> None:
    document = _document()
    plan = plan_import(
        extract_comment_leaves(document), {}, git_sha=SHA, created_at=NOW
    )
    assert all(result.ok for result in verify_persistence(plan, document))


def test_verify_persistence_flags_a_planless_leaf() -> None:
    """Dropping a leaf's decision (a suppression) is structurally caught: the
    extraction-driven check reports the missing leaf as planless."""
    from receipt_dynamo.migrations.crosswalk_notes import ImportPlan

    document = _document()
    full = plan_import(
        extract_comment_leaves(document), {}, git_sha=SHA, created_at=NOW
    )
    dropped = ImportPlan(
        decisions=[
            d
            for d in full.decisions
            if d.leaf.leaf_path != "profiles.Costco Wholesale._comment"
        ]
    )
    results = {r.leaf_path: r for r in verify_persistence(dropped, document)}
    assert results["profiles.Costco Wholesale._comment"].ok is False
    assert "planless" in results["profiles.Costco Wholesale._comment"].detail


def test_verify_persistence_red_on_mutated_source_leaf() -> None:
    document = _document()
    plan = plan_import(
        extract_comment_leaves(document), {}, git_sha=SHA, created_at=NOW
    )
    mutated = _document()
    mutated["profiles"]["Costco Wholesale"]["_comment"] = COSTCO_COMMENT + " X"
    reds = verify_persistence(plan, mutated)
    failed = {r.leaf_path for r in reds if not r.ok}
    assert failed == {"profiles.Costco Wholesale._comment"}


# --------------------------------------------------------------------------
# CLI (injected git read + fake governed client)
# --------------------------------------------------------------------------


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


class _FakeManifest:
    def __init__(self, git_sha: str) -> None:
        self.provenance = {"git_sha": git_sha}


class _FakeClient:
    def __init__(self, seeded: dict[str, list] | None = None) -> None:
        self.store: dict[str, list] = {
            slug: list(items) for slug, items in (seeded or {}).items()
        }
        self.writes: list[MerchantTruthProposal] = []

    def get_merchant_truth_manifest(self, slug: str, version: int):
        return _FakeManifest(SHA)

    def list_merchant_truth_proposals(self, slug: str):
        return list(self.store.get(slug, []))

    def add_proposal(
        self, proposal: MerchantTruthProposal, table: str
    ) -> None:
        self.writes.append(proposal)
        self.store.setdefault(proposal.slug, []).append(proposal)


def _patch_cli(module, monkeypatch, client: _FakeClient) -> None:
    monkeypatch.setattr(
        module, "_git_show_document", lambda repo_root, git_sha: _document()
    )
    monkeypatch.setattr(module, "DynamoClient", lambda *a, **k: client)


def test_cli_dry_run_is_write_free(monkeypatch) -> None:
    module = _load_cli()
    client = _FakeClient()
    _patch_cli(module, monkeypatch, client)

    rc = module.main(["--git-sha", SHA])

    assert rc == 0
    assert client.writes == []  # dry-run gate: nothing written


def test_cli_prod_refusal_precedes_client(monkeypatch) -> None:
    module = _load_cli()

    def _explode(*_a, **_k):
        raise AssertionError("client constructed before prod refusal")

    monkeypatch.setattr(module, "DynamoClient", _explode)
    monkeypatch.setattr(
        module, "_git_show_document", lambda *a, **k: _document()
    )

    with pytest.raises(MerchantTruthTableMismatchError):
        module.main(["--table", PROD_TABLE_NAME])


def test_cli_apply_writes_every_leaf_then_idempotent(monkeypatch) -> None:
    module = _load_cli()
    client = _FakeClient()
    _patch_cli(module, monkeypatch, client)

    rc = module.main(["--git-sha", SHA, "--apply"])
    assert rc == 0
    # 4 leaves, none pre-existing -> 4 writes.
    assert len(client.writes) == 4
    assert {p.claim_slug for p in client.writes} == {
        "profile-comment",
        "layout-template-comment",
        "typography-face-source-comment",
    }

    # Second apply against the now-populated store writes nothing new.
    client.writes.clear()
    rc2 = module.main(["--git-sha", SHA, "--apply"])
    assert rc2 == 0
    assert client.writes == []


def test_cli_apply_writes_related_leaf_with_reference(monkeypatch) -> None:
    module = _load_cli()
    seeded = {
        "costco_wholesale": [
            _seeded_proposal(
                "costco_wholesale",
                "self-checkout-layout-variant",
                SELF_CHECKOUT_CLAIM,
            )
        ]
    }
    client = _FakeClient(seeded)
    _patch_cli(module, monkeypatch, client)

    rc = module.main(
        ["--git-sha", SHA, "--apply", "--merchants", "costco_wholesale"]
    )
    assert rc == 0
    # ALL three Costco leaves write (the _comment is annotated, not dropped).
    written = {p.claim_slug for p in client.writes}
    assert written == {
        "profile-comment",
        "layout-template-comment",
        "typography-face-source-comment",
    }
    profile = next(
        p for p in client.writes if p.claim_slug == "profile-comment"
    )
    envelope = json.loads(profile.claim)
    assert envelope["related_to"] == "self-checkout-layout-variant"
    assert envelope["text"] == COSTCO_COMMENT  # full note preserved verbatim
