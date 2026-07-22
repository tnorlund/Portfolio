"""Moto coverage for the W-J v2 mint driver (scripts/mint_merchant_truth_v2.py).

The driver is a top-level script (not a package module); it is loaded from its
path and it inserts ``scripts/`` + repo root onto ``sys.path`` for its sibling
imports (merchant_truth_diff, the renderer shim registry). These tests exercise
the whole contract-section-7 ceremony against a real moto partition:

- full assembly with a populated catalog + a live PASS seal;
- carried-component hash + provenance faithfulness, and the mutation refusal;
- the section-7.3 typography encoding in both directions;
- proposal-resolution idempotency;
- the empty-catalog dry-run warning (no writes, no hard block);
- a FAIL gate leaving the version OPEN with the gate record as the work list;
- the live banner + prod refusal.

No live writes and no real fidelity eval run: the eval is injected.
"""

from __future__ import annotations

import importlib.util
import os
from types import SimpleNamespace

import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.merchant_catalog_item import MerchantCatalogItem
from receipt_dynamo.entities.merchant_truth import (
    COMPONENT_NAMES,
    MerchantTruthComponent,
    MerchantTruthProposal,
)
from receipt_dynamo.entities.merchant_truth_gate import (
    MerchantTruthGateRecord,
)

pytestmark = pytest.mark.integration

_HERE = os.path.dirname(__file__)
_REPO = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_DRIVER_PATH = os.path.join(_REPO, "scripts", "mint_merchant_truth_v2.py")


def _load_driver():
    spec = importlib.util.spec_from_file_location(
        "mint_merchant_truth_v2", _DRIVER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mint = _load_driver()

SLUG = "costco_wholesale"
MERCHANT = "Costco Wholesale"
NOW = "2026-07-22T16:00:00+00:00"
PROPOSAL = "self-checkout-layout-variant"

LEGACY_PROVENANCE = {
    "source_kind": "migration",
    "provenance_completeness": "legacy",
    "written_by": {
        "kind": "migration",
        "name": "merchant_truth_v1",
        "version": "1",
    },
    "source_path": "scripts/merchant_profiles.json",
    "git_sha": "v1sha",
    "measured_at": None,
    "source_receipt_keys": [],
}


def _v1_payload(name: str) -> dict:
    if name == "typography":
        return {
            "typography": {"cap_h": 12, "advance_ratio": 0.5},
            "section_scale": {},
        }
    if name == "layout":
        return {"available": False, "template": None}
    if name == "catalog_snapshot":
        return {
            "items": [],
            "item_count": 0,
            "catalog_hash": "0",
            "as_of": NOW,
        }
    if name == "identity":
        return {
            "slug": SLUG,
            "merchant_name": MERCHANT,
            "normalized_aliases": [SLUG],
        }
    return {"component": name, "v": 1}


def _v1_components() -> list[MerchantTruthComponent]:
    return [
        MerchantTruthComponent(
            slug=SLUG,
            version=1,
            name=name,
            payload=_v1_payload(name),
            provenance=dict(LEGACY_PROVENANCE),
        )
        for name in sorted(COMPONENT_NAMES)
    ]


def _seal_v1(client: DynamoClient, table: str) -> None:
    client.mint_version(
        SLUG,
        1,
        _v1_components(),
        {"written_by": {"kind": "migration", "name": "v1", "version": "1"}},
        "run-v1",
        table,
        created_at=NOW,
    )
    client.seal_version(
        SLUG,
        1,
        {"status": "PASS", "passed": True},
        [],
        table,
        sealed_at=NOW,
    )


def _seed_catalog(client: DynamoClient, table: str, *, count: int = 3) -> None:
    items = [
        MerchantCatalogItem(
            merchant_name=MERCHANT,
            product_text=f"KS ITEM {i}",
            price=f"{i}.99",
            category="GROCERY",
            taxable=False,
            source="observed",
            observed_count=i + 1,
            source_receipt_keys=[f"img{i}#0000{i}"],
            last_updated=NOW,
        )
        for i in range(1, count + 1)
    ]
    client.add_merchant_catalog_items(items)


def _seed_proposal(client: DynamoClient, table: str) -> None:
    client.add_proposal(
        MerchantTruthProposal(
            slug=SLUG,
            created_at="2026-07-01T00:00:00+00:00",
            claim_slug=PROPOSAL,
            claim="register vs self-checkout formatting",
        ),
        table,
    )


VARIANT_PAYLOAD = {
    "template": {
        "version": 1,
        "columns": {"items": [{"role": "name", "anchor": "left", "x": 0.1}]},
        "sections": [{"name": "items", "pos_frac_med": 0.5, "support": 20}],
        "separators": [],
        "variant_id": "default",
        "support": 24,
        "source_receipt_keys": [
            "IMAGE#a#RECEIPT#00001",
            "IMAGE#b#RECEIPT#00001",
        ],
        "variants": [],
    },
    "verdict": {
        "proposal": PROPOSAL,
        "status": "REFUTE",
        "reason": "single stable cluster",
        "clusters": [
            {
                "variant_id": "default",
                "support": 24,
                "receipt_refs": [
                    "IMAGE#a#RECEIPT#00001",
                    "IMAGE#b#RECEIPT#00001",
                ],
            }
        ],
    },
    "provenance": {
        "source_kind": "measurement",
        "pipeline": "build_variant_layout",
        "pipeline_version": "1",
        "git_sha": "wgsha",
        "measured_at": "2026-07-22T07:00:00+00:00",
        "source_receipt_keys": [
            "IMAGE#a#RECEIPT#00001",
            "IMAGE#b#RECEIPT#00001",
        ],
        "excluded_receipt_keys": [
            {"receipt_key": "IMAGE#c", "reason": "PHOTO"}
        ],
        "params": {"threshold": 0.18},
    },
}


def _args(table: str, **overrides) -> SimpleNamespace:
    base = dict(
        merchant=MERCHANT,
        slug=SLUG,
        layout_payload=None,  # set per-test via a temp file
        from_version=None,
        proposal=PROPOSAL,
        table_name=table,
        git_sha="v2sha",
        generated_at=NOW,
        eval_image_id=None,
        eval_receipt_id=None,
        eval_out_root=".out",
        allow_dirty=False,
        live=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def layout_file(tmp_path):
    import json

    path = tmp_path / "variant.json"
    path.write_text(json.dumps(VARIANT_PAYLOAD))
    return str(path)


def _pass_eval(**kwargs):
    return {
        "columns": {"verdict": "PASS"},
        "tokens": {"verdict": "PASS"},
        "separators": {"verdict": "PASS"},
        "logo": {"verdict": "PASS"},
        "arithmetic": {"verdict": "PASS"},
        "overall": "PASS",
        "coverage_gaps": [],
    }


def _gaps_eval(**kwargs):
    return {
        "columns": {"verdict": "PASS"},
        "tokens": {"verdict": "PASS_WITH_GAPS", "detail": "1 token missing"},
        "overall": "PASS_WITH_GAPS",
        "coverage_gaps": ["logo"],
    }


def _fail_eval_factory(table: str):
    def runner(*, components, slug, version, **kwargs):
        # Simulate `full_fidelity_eval --write-gate-record`: persist the gate
        # record for the failing run BEFORE returning the checks, so the driver
        # bridge finds a durable work list.
        client = DynamoClient(table)
        client.add_gate_record(
            MerchantTruthGateRecord(
                slug=slug,
                run_at="2026-07-22T18:00:00+00:00",
                version=version,
                bundle_hash=mint.bundle_hash_of(components),
                eval_git_sha="evalsha",
                overall="FAIL",
                per_metric=[
                    {"metric": "columns", "verdict": "FAIL"},
                    {"metric": "tokens", "verdict": "PASS"},
                ],
                gaps=[
                    {
                        "metric": "columns",
                        "verdict": "FAIL",
                        "detail": {"note": "lane drift"},
                    }
                ],
                coverage=["logo"],
                evidence_refs=["s3://eval/costco.checks.json"],
                receipt_tested={"merchant": MERCHANT},
            ),
            table,
        )
        return {
            "columns": {"verdict": "FAIL", "note": "lane drift"},
            "tokens": {"verdict": "PASS"},
            "overall": "FAIL",
            "coverage_gaps": ["logo"],
        }

    return runner


# --------------------------------------------------------------------------


def test_full_assembly_and_pass_seal(dynamodb_table, layout_file):
    client = DynamoClient(dynamodb_table)
    _seal_v1(client, dynamodb_table)
    _seed_catalog(client, dynamodb_table, count=3)
    _seed_proposal(client, dynamodb_table)

    args = _args(
        dynamodb_table,
        layout_payload=layout_file,
        live=True,
        eval_image_id="IMG",
        eval_receipt_id=1,
    )
    rc = mint.run(args, eval_runner=_pass_eval)
    assert rc == 0

    v2 = client.get_merchant_truth_manifest(SLUG, 2, consistent_read=True)
    assert v2 is not None and v2.status == "SEALED"
    components = {
        c.name: c
        for c in client.list_merchant_truth_components(
            SLUG, 2, consistent_read=True
        )
    }
    assert set(components) == COMPONENT_NAMES

    # Carried components are hash-identical to v1 and carry the marker.
    v1 = {c.name: c for c in _v1_components()}
    for name in mint.CARRIED_COMPONENTS:
        assert components[name].content_hash == v1[name].content_hash
        assert components[name].provenance["carried_forward_from"] == "v1"

    # Measured catalog snapshot inlines the live rows.
    snap = components["catalog_snapshot"].payload
    assert snap["item_count"] == 3
    assert (
        components["catalog_snapshot"].provenance["source_kind"]
        == "measurement"
    )

    # Layout carries the variant template + the resolved verdict evidence.
    assert components["layout"].payload["template"]["variant_id"] == "default"
    assert components["layout"].provenance["verdict"]["status"] == "REFUTE"

    # Proposal resolved to MEASURED_IN_CANDIDATE by v2.
    props = {
        p.claim_slug: p for p in client.list_merchant_truth_proposals(SLUG)
    }
    assert props[PROPOSAL].status == "MEASURED_IN_CANDIDATE"
    assert props[PROPOSAL].resolution == "refuted"
    assert props[PROPOSAL].resolved_by_version == 2


def test_carry_mutation_rejected():
    src = MerchantTruthComponent(
        slug=SLUG,
        version=1,
        name="identity",
        payload={"slug": SLUG},
        provenance=dict(LEGACY_PROVENANCE),
    )
    faithful = mint.carry_component(src, 2)
    mint.assert_carry_faithful(faithful, src)  # no raise

    # A carry that stamps a fresh measured_at is the carry-that-lies hole.
    lying = MerchantTruthComponent(
        slug=SLUG,
        version=2,
        name="identity",
        payload={"slug": SLUG},
        provenance={
            **dict(LEGACY_PROVENANCE),
            "carried_forward_from": "v1",
            "measured_at": NOW,
        },
    )
    with pytest.raises(mint.MintError, match="carry-that-lies"):
        mint.assert_carry_faithful(lying, src)

    # A payload edit under a carry marker is also refused.
    edited = MerchantTruthComponent(
        slug=SLUG,
        version=2,
        name="identity",
        payload={"slug": SLUG, "extra": 1},
        provenance={**dict(LEGACY_PROVENANCE), "carried_forward_from": "v1"},
    )
    with pytest.raises(mint.MintError, match="hash-preserving"):
        mint.assert_carry_faithful(edited, src)

    # The marker must name the EXACT source version, not merely be v-prefixed.
    wrong_version = MerchantTruthComponent(
        slug=SLUG,
        version=2,
        name="identity",
        payload={"slug": SLUG},
        provenance={**dict(LEGACY_PROVENANCE), "carried_forward_from": "v2"},
    )
    with pytest.raises(mint.MintError, match="exact source version"):
        mint.assert_carry_faithful(wrong_version, src)


def test_typography_section_scale_both_cases():
    v1typ = MerchantTruthComponent(
        slug=SLUG,
        version=1,
        name="typography",
        payload={"typography": {"cap_h": 12}, "section_scale": {}},
        provenance=dict(LEGACY_PROVENANCE),
    )
    # Costco is NOT in the shim registry -> absent (default HEADER shrink).
    comp, rule = mint.build_typography_component(
        SLUG,
        2,
        v1typ,
        explicit_empty=False,
        corpus_source_receipt_keys=["IMAGE#a#RECEIPT#00001"],
        git_sha="s",
        generated_at=NOW,
    )
    assert rule == "absent_default"
    assert "section_scale" not in comp.payload
    # Not the legacy escape: fresh measurement provenance recording v1's hash.
    assert comp.provenance["written_by"]["kind"] == "measurement_pipeline"
    assert comp.provenance["source_kind"] == "measurement"
    assert comp.provenance.get("provenance_completeness") != "legacy"
    assert (
        comp.provenance["derived_from"]["payload_hash"] == v1typ.content_hash
    )
    assert comp.provenance["source_receipt_keys"] == ["IMAGE#a#RECEIPT#00001"]

    # A registry merchant -> explicit uniform {} present.
    comp2, rule2 = mint.build_typography_component(
        "in_n_out_burger",
        2,
        v1typ,
        explicit_empty=True,
        corpus_source_receipt_keys=["IMAGE#a#RECEIPT#00001"],
        git_sha="s",
        generated_at=NOW,
    )
    assert rule2 == "explicit_uniform"
    assert comp2.payload["section_scale"] == {}


def test_proposal_resolution_idempotent(dynamodb_table):
    client = DynamoClient(dynamodb_table)
    _seed_proposal(client, dynamodb_table)
    verdict = VARIANT_PAYLOAD["verdict"]

    first = mint.resolve_layout_proposal(
        client,
        dynamodb_table,
        SLUG,
        verdict=verdict,
        version=2,
        proposal_claim=PROPOSAL,
    )
    assert "refuted" in first

    second = mint.resolve_layout_proposal(
        client,
        dynamodb_table,
        SLUG,
        verdict=verdict,
        version=2,
        proposal_claim=PROPOSAL,
    )
    assert "idempotent skip" in second

    # An absent proposal is reported, not an error.
    absent = mint.resolve_layout_proposal(
        client,
        dynamodb_table,
        SLUG,
        verdict=verdict,
        version=2,
        proposal_claim="no-such-proposal",
    )
    assert "nothing to resolve" in absent


def test_empty_catalog_dry_run_warns_no_writes(
    dynamodb_table, layout_file, capsys
):
    client = DynamoClient(dynamodb_table)
    _seal_v1(client, dynamodb_table)
    # No catalog seeded -> empty partition.
    args = _args(dynamodb_table, layout_payload=layout_file, live=False)
    rc = mint.run(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "partition for 'costco_wholesale' is EMPTY" in out
    assert "DRY RUN" in out
    # No v2 written.
    assert (
        client.get_merchant_truth_manifest(SLUG, 2, consistent_read=True)
        is None
    )


def test_empty_catalog_live_refused_by_governance(dynamodb_table, layout_file):
    """--live with an empty catalog partition is refused (governance-correct).

    An empty ``catalog_snapshot`` is ``source_kind=measurement`` with no
    ``source_receipt_keys``; ``mint_version`` validates every component BEFORE
    any write, so the mint is rejected with zero writes. The owner must run
    ``ingest_merchant_catalog --apply`` first (the dry-run warns loudly).
    """
    from receipt_dynamo.data.shared_exceptions import (
        MerchantTruthIntegrityError,
    )

    client = DynamoClient(dynamodb_table)
    _seal_v1(client, dynamodb_table)
    # No catalog seeded -> empty partition.
    args = _args(
        dynamodb_table,
        layout_payload=layout_file,
        live=True,
        eval_image_id="IMG",
        eval_receipt_id=1,
    )
    with pytest.raises(
        MerchantTruthIntegrityError, match="source_receipt_keys"
    ):
        mint.run(args, eval_runner=_pass_eval)

    # Zero writes: no OPEN v2 manifest landed.
    assert (
        client.get_merchant_truth_manifest(SLUG, 2, consistent_read=True)
        is None
    )


def test_fail_gate_leaves_open(dynamodb_table, layout_file, capsys):
    client = DynamoClient(dynamodb_table)
    _seal_v1(client, dynamodb_table)
    _seed_catalog(client, dynamodb_table, count=2)
    _seed_proposal(client, dynamodb_table)

    args = _args(
        dynamodb_table,
        layout_payload=layout_file,
        live=True,
        eval_image_id="IMG",
        eval_receipt_id=1,
    )
    rc = mint.run(args, eval_runner=_fail_eval_factory(dynamodb_table))
    assert rc == 3  # documented mintable-when-fixed state

    v2 = client.get_merchant_truth_manifest(SLUG, 2, consistent_read=True)
    assert v2 is not None and v2.status == "OPEN"  # left OPEN, not sealed

    # The gate record persists as the work list.
    gate_records = client.list_gate_records(SLUG)
    assert any(r.overall == "FAIL" and r.version == 2 for r in gate_records)
    out = capsys.readouterr().out
    assert "GATE FAILED" in out and "stays OPEN" in out


def test_pass_with_gaps_seals(dynamodb_table, layout_file):
    client = DynamoClient(dynamodb_table)
    _seal_v1(client, dynamodb_table)
    _seed_catalog(client, dynamodb_table, count=1)
    _seed_proposal(client, dynamodb_table)

    args = _args(
        dynamodb_table,
        layout_payload=layout_file,
        live=True,
        eval_image_id="IMG",
        eval_receipt_id=1,
    )
    rc = mint.run(args, eval_runner=_gaps_eval)
    assert rc == 0
    v2 = client.get_merchant_truth_manifest(SLUG, 2, consistent_read=True)
    assert v2.status == "SEALED"
    assert v2.gate_results["overall"] == "PASS_WITH_GAPS"
    assert len(v2.gate_results["gaps"]) == 1  # gaps recorded verbatim


def test_live_banner_and_prod_refusal(dynamodb_table, layout_file, capsys):
    from receipt_dynamo.migrations.merchant_truth_v1_live import (
        PROD_TABLE_NAME,
    )

    # Prod is refused unconditionally by marker substring on EVERY path.
    prod_args = _args(
        PROD_TABLE_NAME,
        layout_payload=layout_file,
        live=True,
        eval_image_id="IMG",
        eval_receipt_id=1,
    )
    with pytest.raises(SystemExit, match="prod table"):
        mint.run(prod_args, eval_runner=_pass_eval)

    # ... including the dry-run read path, and a prod-suffixed variant.
    prod_dry = _args(f"ReceiptsTable-{mint._PROD_MARKER}-shadow", live=False)
    with pytest.raises(SystemExit, match="prod table"):
        mint.run(prod_dry, eval_runner=_pass_eval)

    # The live banner is printed on the real ceremony.
    client = DynamoClient(dynamodb_table)
    _seal_v1(client, dynamodb_table)
    _seed_catalog(client, dynamodb_table, count=1)
    _seed_proposal(client, dynamodb_table)
    ok_args = _args(
        dynamodb_table,
        layout_payload=layout_file,
        live=True,
        eval_image_id="IMG",
        eval_receipt_id=1,
    )
    mint.run(ok_args, eval_runner=_pass_eval)
    out = capsys.readouterr().out
    assert "LIVE MERCHANT-TRUTH V2 MINT" in out
    assert dynamodb_table in out


def test_live_requires_eval_target(dynamodb_table, layout_file):
    client = DynamoClient(dynamodb_table)
    _seal_v1(client, dynamodb_table)
    _seed_catalog(client, dynamodb_table, count=1)
    args = _args(dynamodb_table, layout_payload=layout_file, live=True)
    with pytest.raises(SystemExit, match="eval-image-id"):
        mint.run(args, eval_runner=_pass_eval)
