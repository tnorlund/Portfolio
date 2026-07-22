"""Live-mint mode of the MerchantTruth v1 migration (moto only).

Covers: live mint+seal with read-back verification, unconditional prod-table
refusal, blocked-merchant exclusion, unmapped-leaf failure before any write,
and the dry-run default remaining write-free.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    MerchantTruthTableMismatchError,
)
from receipt_dynamo.entities.merchant_font import MerchantFont
from receipt_dynamo.entities.merchant_truth import MerchantTruthComponent
from receipt_dynamo.migrations.merchant_truth_v1 import (
    DEV_TABLE_NAME,
    EXPECTED_MISSING_FONT_SLUGS,
    UnmappedMerchantTruthLeafError,
    build_v1_payloads,
    slugify_merchant,
    write_dry_run_payloads,
)
from receipt_dynamo.migrations.merchant_truth_v1_live import (
    PROD_TABLE_NAME,
    bootstrap_gate_results,
    filter_payloads,
    run_live_mint,
    validate_live_table,
    verify_live_against_dry_run,
)

pytestmark = pytest.mark.integration
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
STYLEMAP_BYTES = b'{"version":1,"sections":{}}'
LOGO_BYTES = b"logo-bytes"


def profile_document() -> dict[str, Any]:
    profiles: dict[str, Any] = {
        merchant: {
            "_comment": "legacy note",
            "aliases": [merchant.upper()],
            "typography": {"condense": 0.9},
        }
        for merchant in MERCHANTS
    }
    profiles["Sprouts Farmers Market"]["typography"].update(
        {
            "bitmap_font": {"regular": "sprouts.npz"},
            "stylemap": "sprouts.stylemap.json",
            "font": "OCRB",
        }
    )
    profiles["Sprouts Farmers Market"]["section_scale"] = {"HEADER": 1.1}
    profiles["Sprouts Farmers Market"]["layout_template"] = {
        "version": 1,
        "columns": {"items": [{"x": 0.5}]},
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
    """Read-only source stand-in matching the dry-run unit fixtures."""

    def __init__(self) -> None:
        self.fonts = [
            merchant_font(name)
            for name in MERCHANTS
            if slugify_merchant(name) not in EXPECTED_MISSING_FONT_SLUGS
        ]

    def list_merchant_font_items(self) -> list[dict[str, Any]]:
        return [font.to_item() for font in self.fonts]

    def list_catalog_items(self, slug: str) -> list[dict[str, Any]]:
        return [catalog_item(slug)]

    def read_object(self, _bucket: str, key: str) -> bytes:
        return STYLEMAP_BYTES if key.endswith("stylemap.json") else LOGO_BYTES


def catalog_item(slug: str) -> dict[str, Any]:
    return {
        "PK": {"S": f"MERCHANT_CATALOG#{slug}"},
        "SK": {"S": "ITEM#GROCERY#MILK"},
        "TYPE": {"S": "MERCHANT_CATALOG_ITEM"},
        "product_text": {"S": "MILK"},
        "price": {"S": "3.99"},
        "category": {"S": "GROCERY"},
        "taxable": {"BOOL": False},
    }


def built_payloads(git_sha: str = GIT_SHA, generated_at: str = NOW):
    return build_v1_payloads(
        profile_document(),
        FakeSource(),  # type: ignore[arg-type]
        profiles_source_path="scripts/merchant_profiles.json",
        git_sha=git_sha,
        generated_at=generated_at,
    )


def gate_results(payload_dir: Path) -> dict[str, Any]:
    return bootstrap_gate_results(
        git_sha=GIT_SHA, generated_at=NOW, payload_dir=payload_dir
    )


def mint_all(client: DynamoClient, table: str, payload_dir: Path):
    payloads, crosswalk = built_payloads()
    write_dry_run_payloads(
        payload_dir, payloads, crosswalk, generated_at=NOW, git_sha=GIT_SHA
    )
    results = run_live_mint(
        client,
        payloads,
        table_name=table,
        gate_results=gate_results(payload_dir),
        generated_at=NOW,
        explicit_table=True,
    )
    return payloads, results


def audit_actions(table: str, slug: str) -> list[str]:
    response = boto3.client("dynamodb", region_name="us-east-1").query(
        TableName=table,
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={
            ":pk": {"S": f"MERCHANT_TRUTH#{slug}"},
            ":sk": {"S": "AUDIT#"},
        },
    )
    return sorted(item["action"]["S"] for item in response["Items"])


def test_live_mint_seals_unblocked_and_excludes_blocked(
    dynamodb_table: str, tmp_path: Path
) -> None:
    client = DynamoClient(dynamodb_table)

    _, results = mint_all(client, dynamodb_table, tmp_path)

    minted = [r for r in results if r.action == "MINTED_SEALED"]
    excluded = [r for r in results if r.action == "EXCLUDED"]
    assert len(minted) == 13
    assert {r.slug for r in excluded} == EXPECTED_MISSING_FONT_SLUGS
    for result in excluded:
        assert result.blockers
        assert "asset-blocked" in result.report_line
        assert client.get_merchant_truth_manifest(result.slug, 1) is None
    for result in minted:
        manifest = client.get_merchant_truth_manifest(
            result.slug, 1, consistent_read=True
        )
        assert manifest is not None
        assert manifest.status == "SEALED"
        assert manifest.gate_status == "PASS"
        assert manifest.bundle_hash == result.bundle_hash
        assert manifest.gate_results["kind"] == "migration-bootstrap-seal"
        assert audit_actions(dynamodb_table, result.slug) == ["MINT", "SEAL"]


def test_live_bundles_byte_match_dry_run_payloads(
    dynamodb_table: str, tmp_path: Path
) -> None:
    client = DynamoClient(dynamodb_table)
    _, results = mint_all(client, dynamodb_table, tmp_path)
    minted_slugs = [r.slug for r in results if r.action == "MINTED_SEALED"]

    verify_results = verify_live_against_dry_run(
        client, tmp_path, minted_slugs, work_dir=tmp_path / "_live_verify"
    )

    assert len(verify_results) == 13
    assert all(result.ok for result in verify_results)
    by_slug = {result.slug: result for result in verify_results}
    for result in results:
        if result.action == "MINTED_SEALED":
            assert by_slug[result.slug].bundle_hash == result.bundle_hash


def test_verify_reports_mismatch_and_stays_fail_closed_on_tamper(
    dynamodb_table: str, tmp_path: Path
) -> None:
    client = DynamoClient(dynamodb_table)
    _, results = mint_all(client, dynamodb_table, tmp_path)
    minted_slugs = sorted(
        r.slug for r in results if r.action == "MINTED_SEALED"
    )
    victim = minted_slugs[0]
    tampered = MerchantTruthComponent(
        slug=victim,
        version=1,
        name="flags",
        payload={"tampered": True},
        provenance={"source_kind": "migration"},
    )
    boto3.client("dynamodb", region_name="us-east-1").put_item(
        TableName=dynamodb_table, Item=tampered.to_item()
    )

    verify_results = verify_live_against_dry_run(
        client, tmp_path, minted_slugs, work_dir=tmp_path / "_live_verify"
    )

    by_slug = {result.slug: result for result in verify_results}
    assert not by_slug[victim].ok
    assert "loader rejected live bundle" in by_slug[victim].report_line
    assert all(result.ok for slug, result in by_slug.items() if slug != victim)


def test_prod_table_is_refused_unconditionally(tmp_path: Path) -> None:
    for explicit in (True, False):
        with pytest.raises(
            MerchantTruthTableMismatchError, match="unconditional"
        ):
            validate_live_table(PROD_TABLE_NAME, explicit=explicit)

    payloads, _ = built_payloads()

    class ExplodingClient:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError("prod refusal must precede any client call")

    with pytest.raises(MerchantTruthTableMismatchError, match="prod table"):
        run_live_mint(
            ExplodingClient(),  # type: ignore[arg-type]
            payloads,
            table_name=PROD_TABLE_NAME,
            gate_results=gate_results(tmp_path),
            generated_at=NOW,
            explicit_table=True,
        )


def test_non_dev_table_requires_explicit_opt_in() -> None:
    validate_live_table(DEV_TABLE_NAME, explicit=False)
    validate_live_table("SomeTestTable", explicit=True)
    with pytest.raises(MerchantTruthTableMismatchError, match="--table"):
        validate_live_table("SomeTestTable", explicit=False)
    with pytest.raises(MerchantTruthTableMismatchError):
        validate_live_table("", explicit=True)


def test_unmapped_leaf_fails_before_any_payload_is_built() -> None:
    document = profile_document()
    document["profiles"]["CVS"]["unknown_runtime_knob"] = True

    with pytest.raises(UnmappedMerchantTruthLeafError, match="unmapped"):
        build_v1_payloads(
            document,
            FakeSource(),  # type: ignore[arg-type]
            profiles_source_path="scripts/merchant_profiles.json",
            git_sha=GIT_SHA,
            generated_at=NOW,
        )


OTHER_GIT_SHA = "b" * 40
LATER = "2026-07-21T16:00:00+00:00"


def live(client, payloads, payload_dir, generated_at: str = NOW):
    return run_live_mint(
        client,
        payloads,
        table_name=client.table_name,
        gate_results=gate_results(payload_dir),
        generated_at=generated_at,
        explicit_table=True,
    )


def test_full_replay_skips_sealed_merchants_instead_of_aborting(
    dynamodb_table: str, tmp_path: Path
) -> None:
    """Reviewer finding on #1205: run_id is git-sha-dependent, so a replay
    under a newer HEAD used to raise MerchantTruthConflictError on the
    first already-sealed merchant. It must skip them and mint the rest."""
    client = DynamoClient(dynamodb_table)
    payloads_a, _ = built_payloads()
    live(client, filter_payloads(payloads_a, ["cvs", "vons"]), tmp_path)

    payloads_b, _ = built_payloads(git_sha=OTHER_GIT_SHA)
    results = live(client, payloads_b, tmp_path)

    by_action: dict[str, set[str]] = {}
    for result in results:
        by_action.setdefault(result.action, set()).add(result.slug)
    assert by_action["SKIPPED_SEALED"] == {"cvs", "vons"}
    assert len(by_action["MINTED_SEALED"]) == 11
    assert by_action["EXCLUDED"] == set(EXPECTED_MISSING_FONT_SLUGS)
    assert "CONFLICT_SEALED" not in by_action
    skipped = next(r for r in results if r.action == "SKIPPED_SEALED")
    assert (
        f"SKIPPED (already sealed, bundle={skipped.bundle_hash[:12]})"
        in skipped.report_line
    )
    # Every unblocked merchant ends up sealed and read-back-verifiable.
    payload_dir = tmp_path / "payloads"
    payloads_c, crosswalk = built_payloads(git_sha=OTHER_GIT_SHA)
    write_dry_run_payloads(
        payload_dir,
        payloads_c,
        crosswalk,
        generated_at=NOW,
        git_sha=OTHER_GIT_SHA,
    )
    verify_results = verify_live_against_dry_run(
        client,
        payload_dir,
        sorted(p.slug for p in payloads_c if not p.blockers),
        work_dir=tmp_path / "_live_verify",
    )
    assert all(result.ok for result in verify_results)


def test_sealed_bundle_differing_from_source_reports_conflict(
    dynamodb_table: str, tmp_path: Path
) -> None:
    client = DynamoClient(dynamodb_table)
    payloads_a, _ = built_payloads()
    live(client, filter_payloads(payloads_a, ["cvs"]), tmp_path)
    sealed = client.get_merchant_truth_manifest("cvs", 1, consistent_read=True)
    assert sealed is not None

    # A later generated_at changes catalog_snapshot.as_of, so the source
    # would now produce a different bundle: drift, not an idempotent skip.
    payloads_b, _ = built_payloads(generated_at=LATER)
    results = live(
        client,
        filter_payloads(payloads_b, ["cvs", "vons"]),
        tmp_path,
        generated_at=LATER,
    )

    conflict = next(r for r in results if r.slug == "cvs")
    assert conflict.action == "CONFLICT_SEALED"
    assert conflict.bundle_hash == sealed.bundle_hash
    assert conflict.source_bundle_hash != sealed.bundle_hash
    assert (
        "CONFLICT (sealed bundle differs from source)" in conflict.report_line
    )
    # The conflict did not write: the sealed manifest is untouched.
    after = client.get_merchant_truth_manifest("cvs", 1, consistent_read=True)
    assert after is not None
    assert after.bundle_hash == sealed.bundle_hash
    assert after.sealed_at == sealed.sealed_at
    # And the loop continued past the conflict.
    minted = next(r for r in results if r.slug == "vons")
    assert minted.action == "MINTED_SEALED"


def test_filter_payloads_validates_slugs_against_the_payload_set() -> None:
    payloads, _ = built_payloads()

    filtered = filter_payloads(payloads, ["vons", "cvs"])
    assert [p.slug for p in filtered] == ["cvs", "vons"]

    with pytest.raises(ValueError, match="unknown merchant slugs: wallmart"):
        filter_payloads(payloads, ["cvs", "wallmart"])
    with pytest.raises(ValueError, match="contains no slugs"):
        filter_payloads(payloads, [" ", ""])


def _create_dev_named_table() -> None:
    boto3.client("dynamodb", region_name="us-east-1").create_table(
        TableName=DEV_TABLE_NAME,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "TYPE", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
        GlobalSecondaryIndexes=[
            {
                "IndexName": "GSITYPE",
                "KeySchema": [{"AttributeName": "TYPE", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )


def _seed_dev_sources() -> None:
    dynamodb = boto3.client("dynamodb", region_name="us-east-1")
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="font-bucket")
    for merchant in MERCHANTS:
        slug = slugify_merchant(merchant)
        dynamodb.put_item(TableName=DEV_TABLE_NAME, Item=catalog_item(slug))
        if slug in EXPECTED_MISSING_FONT_SLUGS:
            continue
        font = merchant_font(merchant)
        dynamodb.put_item(TableName=DEV_TABLE_NAME, Item=font.to_item())
        s3.put_object(
            Bucket="font-bucket",
            Key=font.stylemap_s3_key,
            Body=STYLEMAP_BYTES,
        )
        s3.put_object(
            Bucket="font-bucket", Key=font.logo_s3_key, Body=LOGO_BYTES
        )


@pytest.fixture
def seeded_dev_environment(tmp_path: Path):
    with mock_aws():
        _create_dev_named_table()
        _seed_dev_sources()
        profiles_path = tmp_path / "profiles.json"
        profiles_path.write_text(
            json.dumps(profile_document()), encoding="utf-8"
        )
        yield profiles_path


def _script_main(argv: list[str]) -> int:
    import importlib.util
    import sys

    script_path = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "migrate_merchant_truth_v1.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migrate_merchant_truth_v1", script_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.main(argv)


def test_script_live_verify_end_to_end(
    seeded_dev_environment: Path, tmp_path: Path, capsys
) -> None:
    output_dir = tmp_path / "payloads"

    exit_code = _script_main(
        [
            "--profiles",
            str(seeded_dev_environment),
            "--output-dir",
            str(output_dir),
            "--git-sha",
            GIT_SHA,
            "--generated-at",
            NOW,
            "--live",
            "--verify",
        ]
    )

    captured = capsys.readouterr().out
    assert exit_code == 0
    assert "LIVE MERCHANT-TRUTH V1 MINT" in captured
    assert DEV_TABLE_NAME in captured
    assert "merchants: 13" in captured
    assert GIT_SHA in captured
    assert captured.count("MINTED+SEALED v1") == 13
    assert captured.count("EXCLUDED (asset-blocked)") == 3
    assert "VERIFY OK: 13 live bundles byte-match" in captured
    assert len(list(output_dir.glob("*.json"))) == 18
    client = DynamoClient(DEV_TABLE_NAME)
    manifest = client.get_merchant_truth_manifest(
        "sprouts_farmers_market", 1, consistent_read=True
    )
    assert manifest is not None and manifest.status == "SEALED"


def test_script_default_stays_dry_run_and_write_free(
    seeded_dev_environment: Path, tmp_path: Path, capsys
) -> None:
    output_dir = tmp_path / "payloads"

    exit_code = _script_main(
        [
            "--profiles",
            str(seeded_dev_environment),
            "--output-dir",
            str(output_dir),
            "--git-sha",
            GIT_SHA,
            "--generated-at",
            NOW,
        ]
    )

    captured = capsys.readouterr().out
    assert exit_code == 0
    assert "DRY RUN: wrote 16 merchant payloads" in captured
    assert "No DynamoDB or S3 writes were performed." in captured
    assert "LIVE" not in captured
    assert len(list(output_dir.glob("*.json"))) == 18
    truth_rows = boto3.client("dynamodb", region_name="us-east-1").query(
        TableName=DEV_TABLE_NAME,
        IndexName="GSITYPE",
        KeyConditionExpression="#type = :type",
        ExpressionAttributeNames={"#type": "TYPE"},
        ExpressionAttributeValues={":type": {"S": "MERCHANT_TRUTH_MANIFEST"}},
    )
    assert truth_rows["Items"] == []


def test_script_merchants_filter_applies_to_dry_run(
    seeded_dev_environment: Path, tmp_path: Path, capsys
) -> None:
    output_dir = tmp_path / "payloads"

    exit_code = _script_main(
        [
            "--profiles",
            str(seeded_dev_environment),
            "--output-dir",
            str(output_dir),
            "--git-sha",
            GIT_SHA,
            "--generated-at",
            NOW,
            "--merchants",
            "cvs,vons",
        ]
    )

    captured = capsys.readouterr().out
    assert exit_code == 0
    assert "DRY RUN: wrote 2 merchant payloads" in captured
    payload_files = sorted(
        p.name for p in output_dir.glob("*.json") if not p.name.startswith("_")
    )
    assert payload_files == ["cvs.json", "vons.json"]


def test_script_merchants_filter_rejects_unknown_slug(
    seeded_dev_environment: Path, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="unknown merchant slugs"):
        _script_main(
            [
                "--profiles",
                str(seeded_dev_environment),
                "--output-dir",
                str(tmp_path / "payloads"),
                "--git-sha",
                GIT_SHA,
                "--generated-at",
                NOW,
                "--merchants",
                "cvs,wallmart",
                "--live",
            ]
        )


def test_script_replay_after_partial_mint_skips_and_verifies(
    seeded_dev_environment: Path, tmp_path: Path, capsys
) -> None:
    """End-to-end reviewer scenario: partial mint at one git SHA, then a
    full --live --verify replay at a different SHA skips the sealed
    merchants, mints the rest, and exits 0."""
    first = _script_main(
        [
            "--profiles",
            str(seeded_dev_environment),
            "--output-dir",
            str(tmp_path / "payloads-1"),
            "--git-sha",
            GIT_SHA,
            "--generated-at",
            NOW,
            "--merchants",
            "cvs,vons",
            "--live",
            "--verify",
        ]
    )
    assert first == 0
    capsys.readouterr()

    exit_code = _script_main(
        [
            "--profiles",
            str(seeded_dev_environment),
            "--output-dir",
            str(tmp_path / "payloads-2"),
            "--git-sha",
            OTHER_GIT_SHA,
            "--generated-at",
            NOW,
            "--live",
            "--verify",
        ]
    )

    captured = capsys.readouterr().out
    assert exit_code == 0
    assert captured.count("MINTED+SEALED v1") == 11
    assert captured.count("SKIPPED (already sealed, bundle=") == 2
    assert captured.count("EXCLUDED (asset-blocked)") == 3
    assert "CONFLICT" not in captured
    assert "skipped 2 already-sealed" in captured
    assert "VERIFY OK: 13 live bundles byte-match" in captured


@pytest.mark.parametrize(
    "mode_flags",
    [
        pytest.param([], id="dry-run-default"),
        pytest.param(["--live"], id="live"),
        pytest.param(["--verify"], id="verify"),
    ],
)
def test_script_refuses_prod_table_before_reading_anything(
    tmp_path: Path, mode_flags: list[str]
) -> None:
    """Prod refusal precedes profile reads on every path.

    The profiles path does not exist, so reaching json.load (or boto3
    client construction) would raise FileNotFoundError instead of the
    table-mismatch error asserted here.
    """
    with pytest.raises(MerchantTruthTableMismatchError, match="prod table"):
        _script_main(
            [
                "--profiles",
                str(tmp_path / "missing.json"),
                "--output-dir",
                str(tmp_path / "payloads"),
                "--table",
                PROD_TABLE_NAME,
                *mode_flags,
            ]
        )
