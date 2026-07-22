"""Contract tests for scripts/activate_merchant_truth.py (G2 flip tool).

Two layers, matching the sibling suites:

* Stub-client tests (pattern: tests/test_fleet_status.py) prove the
  read-only dry-run contract and the prod refusal — including the two
  mutation-honesty guards:

  - ``test_dry_run_performs_zero_writes`` goes red if the ``--flip`` gate
    is removed (the stub raises on any write in dry-run).
  - ``test_prod_table_refused_before_any_read_or_write`` goes red if the
    prod guard is removed (the poisoned DynamoClient constructor and the
    poisoned stub both raise the moment the guard stops short-circuiting).

* moto tests (pattern: receipt_dynamo/tests/integration) drive the real
  write path end-to-end: initial activation, idempotent convergence,
  flip with correct expected state, and a stale-expected conflict.
"""

from __future__ import annotations

from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    MerchantTruthConflictError,
    MerchantTruthIntegrityError,
)
from receipt_dynamo.entities.merchant_truth import (
    COMPONENT_NAMES,
    MerchantTruthActive,
    MerchantTruthComponent,
    MerchantTruthManifest,
    compute_bundle_hash,
)
from scripts import activate_merchant_truth as amt

NOW = "2026-07-21T12:00:00+00:00"
SEALED_AT = "2026-07-21T05:00:00+00:00"
TABLE = "MyMockedTable"

LEGACY_PROVENANCE = {
    "source_kind": "migration",
    "provenance_completeness": "legacy",
    "written_by": {
        "kind": "migration",
        "name": "merchant_truth_v1",
        "version": "1",
    },
}
MINT_PROVENANCE = {
    "written_by": {
        "kind": "migration",
        "name": "merchant_truth_v1",
        "version": "1",
    }
}


@pytest.fixture(autouse=True)
def mock_aws_services():
    """Override the root infrastructure stub so moto owns boto3 here."""
    yield


# ---------------------------------------------------------------------------
# Entity builders (real entities, so every hash is the canonical one)
# ---------------------------------------------------------------------------


def component_payload(
    slug: str,
    name: str,
    version: int,
    aliases: list[str] | None = None,
) -> Any:
    if name == "identity":
        return {
            "merchant_name": slug.title(),
            "slug": slug,
            "normalized_aliases": aliases or [slug, f"{slug} store"],
        }
    return {"component": name, "v": version}


def build_components(
    slug: str,
    version: int,
    aliases: list[str] | None = None,
) -> list[MerchantTruthComponent]:
    return [
        MerchantTruthComponent(
            slug=slug,
            version=version,
            name=name,
            payload=component_payload(slug, name, version, aliases),
            provenance=LEGACY_PROVENANCE,
        )
        for name in sorted(COMPONENT_NAMES)
    ]


def build_sealed(
    slug: str,
    version: int,
    *,
    gate_status: str = "PASS",
) -> tuple[MerchantTruthManifest, list[MerchantTruthComponent]]:
    components = build_components(slug, version)
    hashes = {item.name: item.content_hash for item in components}
    manifest = MerchantTruthManifest(
        slug=slug,
        version=version,
        component_hashes=hashes,
        bundle_hash=compute_bundle_hash(hashes),
        status="SEALED",
        provenance=MINT_PROVENANCE,
        mint_run_id=f"run-{slug}-{version}",
        gate_status=gate_status,
        gate_results={"status": gate_status},
        sealed_at=SEALED_AT,
    )
    return manifest, components


def make_active(
    slug: str, version: int, bundle_hash: str, *, activated_by: str = "owner"
) -> MerchantTruthActive:
    return MerchantTruthActive(
        slug=slug,
        version=version,
        bundle_hash=bundle_hash,
        normalized_aliases=[slug],
        activated_at=NOW,
        activated_by=activated_by,
    )


# ---------------------------------------------------------------------------
# Stub client (reader shape mirrors tests/test_fleet_status.py; writes are
# poisoned unless a test explicitly allows them)
# ---------------------------------------------------------------------------


class StubClient:
    def __init__(
        self,
        bundles: list[
            tuple[MerchantTruthManifest, list[MerchantTruthComponent]]
        ],
        active_records: list[MerchantTruthActive] | None = None,
        *,
        allow_writes: bool = False,
    ) -> None:
        self.manifests = {
            (manifest.slug, manifest.version): manifest
            for manifest, _ in bundles
        }
        self.components = {
            (manifest.slug, manifest.version): components
            for manifest, components in bundles
        }
        self.active_records = list(active_records or [])
        self.allow_writes = allow_writes
        self.read_calls = 0
        self.write_calls: list[tuple[str, ...]] = []

    # -- reads ------------------------------------------------------------
    def get_merchant_truth_manifest(
        self, slug: str, version: int, *, consistent_read: bool = False
    ) -> MerchantTruthManifest | None:
        del consistent_read
        self.read_calls += 1
        return self.manifests.get((slug, version))

    def list_merchant_truth_components(
        self, slug: str, version: int, *, consistent_read: bool = False
    ) -> list[MerchantTruthComponent]:
        del consistent_read
        self.read_calls += 1
        return list(self.components.get((slug, version), []))

    def list_merchant_truth_manifests(self) -> list[MerchantTruthManifest]:
        self.read_calls += 1
        return list(self.manifests.values())

    def list_active_merchant_truth(self) -> list[MerchantTruthActive]:
        self.read_calls += 1
        return list(self.active_records)

    def get_active_merchant_truth(
        self, slug: str, *, consistent_read: bool = False
    ) -> MerchantTruthActive | None:
        del consistent_read
        self.read_calls += 1
        return next(
            (item for item in self.active_records if item.slug == slug),
            None,
        )

    # -- writes -----------------------------------------------------------
    def initial_activate(
        self, active: MerchantTruthActive, expected_table_name: str
    ) -> MerchantTruthActive:
        if not self.allow_writes:
            raise AssertionError("dry-run must never call initial_activate")
        self.write_calls.append(("initial", active.slug, str(active.version)))
        self.active_records = [
            a for a in self.active_records if a.slug != active.slug
        ] + [active]
        return active

    def flip_active(
        self,
        active: MerchantTruthActive,
        expected_version: int,
        expected_bundle_hash: str,
        expected_table_name: str,
    ) -> MerchantTruthActive:
        if not self.allow_writes:
            raise AssertionError("dry-run must never call flip_active")
        self.write_calls.append(
            (
                "flip",
                active.slug,
                str(active.version),
                str(expected_version),
                expected_bundle_hash,
            )
        )
        self.active_records = [
            a for a in self.active_records if a.slug != active.slug
        ] + [active]
        return active


class BrokenFlipClient(StubClient):
    """A writer whose flip always loses the race."""

    def flip_active(self, *args: Any, **kwargs: Any) -> MerchantTruthActive:
        raise MerchantTruthConflictError(
            "ACTIVE no longer matches the expected prior state"
        )


# ---------------------------------------------------------------------------
# Dry-run: zero writes (mutation-honesty guard for the --flip gate)
# ---------------------------------------------------------------------------


def test_dry_run_performs_zero_writes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest, components = build_sealed("vons", 1)
    client = StubClient([(manifest, components)], allow_writes=False)

    exit_code = amt.main(["--slug", "vons", "--version", "v1"], client=client)

    assert exit_code == 0
    assert client.write_calls == []
    output = capsys.readouterr().out
    assert "DRY-RUN: no writes performed" in output
    assert "--flip" in output
    assert "INITIAL ACTIVATION" in output
    assert f"bundle_hash: `{manifest.bundle_hash}`" in output
    assert "gate_status: PASS" in output
    # Component hashes are part of the condensed decision summary.
    assert manifest.component_hashes["identity"][:12] in output


def test_dry_run_for_upgrade_names_flip_expected_state(
    capsys: pytest.CaptureFixture[str],
) -> None:
    v1, c1 = build_sealed("vons", 1)
    v2, c2 = build_sealed("vons", 2)
    client = StubClient(
        [(v1, c1), (v2, c2)],
        [make_active("vons", 1, v1.bundle_hash)],
        allow_writes=False,
    )

    exit_code = amt.main(["--slug", "vons", "--version", "2"], client=client)

    assert exit_code == 0
    assert client.write_calls == []
    output = capsys.readouterr().out
    assert "FLIP: ACTIVE currently points at v1" in output
    assert "flip_active" in output
    assert f"{{version: 1, bundle_hash: `{v1.bundle_hash[:12]}`}}" in output


def test_all_sealed_dry_run_performs_zero_writes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    alpha, alpha_components = build_sealed("alpha", 1)
    beta, beta_components = build_sealed("beta", 1)
    client = StubClient(
        [(alpha, alpha_components), (beta, beta_components)],
        allow_writes=False,
    )

    exit_code = amt.main(["--all-sealed"], client=client)

    assert exit_code == 0
    assert client.write_calls == []
    output = capsys.readouterr().out
    assert "2 SEALED pending version(s)" in output
    assert "| alpha | 1 | DRY-RUN |" in output
    assert "| beta | 1 | DRY-RUN |" in output
    assert "DRY-RUN: no writes performed" in output


def test_gate_fail_bundle_is_not_activatable() -> None:
    manifest, components = build_sealed("vons", 1, gate_status="FAIL")
    client = StubClient([(manifest, components)], allow_writes=True)

    exit_code = amt.main(
        ["--slug", "vons", "--version", "1", "--flip"], client=client
    )

    assert exit_code == 1
    assert client.write_calls == []


# ---------------------------------------------------------------------------
# Prod refusal (mutation-honesty guard for the prod table guard)
# ---------------------------------------------------------------------------


def test_prod_table_refused_before_any_read_or_write(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A poisoned constructor: if the guard is removed, main() constructs a
    # client (client=None path) and this blows up before any AWS call.
    import receipt_dynamo.data.dynamo_client as dynamo_client_module

    def poisoned(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(
            "prod guard must refuse before any client construction"
        )

    monkeypatch.setattr(dynamo_client_module, "DynamoClient", poisoned)
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "ReceiptsTable-d7ff76a")

    exit_code = amt.main(
        ["--slug", "vons", "--version", "1", "--flip"], client=None
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "REFUSED" in captured.err
    assert captured.out == ""

    # Explicit --table with the exact prod name and with the marker
    # substring are both refused before a single read or write.
    manifest, components = build_sealed("vons", 1)
    stub = StubClient([(manifest, components)], allow_writes=False)
    for prod_name in ("ReceiptsTable-d7ff76a", "ReceiptsTable-d7ff76a-copy"):
        assert (
            amt.main(
                [
                    "--slug",
                    "vons",
                    "--version",
                    "1",
                    "--flip",
                    "--table",
                    prod_name,
                ],
                client=stub,
            )
            == 2
        )
    assert stub.read_calls == 0
    assert stub.write_calls == []


def test_default_table_is_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DYNAMODB_TABLE_NAME", raising=False)
    assert amt.resolve_table(None) == "ReceiptsTable-dc5be22"


# ---------------------------------------------------------------------------
# moto write path (pattern: receipt_dynamo integration tests)
# ---------------------------------------------------------------------------


@pytest.fixture
def dynamodb_table():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "TYPE", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSITYPE",
                    "KeySchema": [
                        {"AttributeName": "TYPE", "KeyType": "HASH"}
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield TABLE


def seal_live(
    client: DynamoClient,
    table: str,
    slug: str,
    version: int,
    aliases: list[str] | None = None,
) -> str:
    """Mint + seal one version through the real accessor; return its hash."""
    client.mint_version(
        slug,
        version,
        build_components(slug, version, aliases),
        MINT_PROVENANCE,
        f"run-{slug}-{version}",
        table,
        created_at=NOW,
    )
    sealed = client.seal_version(
        slug,
        version,
        {"status": "PASS", "report": f"s3://eval/{slug}-v{version}.json"},
        [],
        table,
        sealed_at=NOW,
    )
    return sealed.bundle_hash


def activation_audits(table: str, slug: str) -> list[str]:
    """Actions of every activation audit row for a merchant, oldest first."""
    response = boto3.client("dynamodb", region_name="us-east-1").query(
        TableName=table,
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={
            ":pk": {"S": f"MERCHANT_TRUTH#{slug}"},
            ":sk": {"S": "AUDIT#"},
        },
    )
    return [
        item["action"]["S"]
        for item in response["Items"]
        if item["action"]["S"] in {"INITIAL_ACTIVATE", "FLIP_ACTIVE"}
    ]


def test_initial_activation_creates_active_pointer_and_audit(
    dynamodb_table: str, capsys: pytest.CaptureFixture[str]
) -> None:
    client = DynamoClient(dynamodb_table)
    bundle_hash = seal_live(client, dynamodb_table, "vons", 1)

    exit_code = amt.main(
        [
            "--slug",
            "vons",
            "--version",
            "1",
            "--flip",
            "--table",
            dynamodb_table,
        ],
        client=client,
    )

    assert exit_code == 0
    active = client.get_active_merchant_truth("vons", consistent_read=True)
    assert active is not None
    assert (active.version, active.bundle_hash) == (1, bundle_hash)
    assert active.activated_by == "owner-cli"  # the default identity
    assert active.normalized_aliases == ["vons", "vons store"]
    assert activation_audits(dynamodb_table, "vons") == ["INITIAL_ACTIVATE"]
    output = capsys.readouterr().out
    # The banner precedes the write and names table + slug + version.
    assert "FLIP: about to write ACTIVE pointer(s)" in output
    assert f"table: {dynamodb_table}" in output
    assert "vons -> v1" in output
    assert "Result: ACTIVATED" in output
    assert f"ACTIVE: vons -> v1 (`{bundle_hash}`)" in output
    assert "audit row written in the same" in output


def test_second_activation_converges_idempotently(
    dynamodb_table: str, capsys: pytest.CaptureFixture[str]
) -> None:
    client = DynamoClient(dynamodb_table)
    bundle_hash = seal_live(client, dynamodb_table, "vons", 1)
    argv = [
        "--slug",
        "vons",
        "--version",
        "1",
        "--flip",
        "--table",
        dynamodb_table,
    ]

    assert amt.main(argv, client=client) == 0
    capsys.readouterr()
    assert amt.main(argv, client=client) == 0

    output = capsys.readouterr().out
    assert "ALREADY ACTIVE" in output
    assert "Result: ALREADY-ACTIVE" in output
    active = client.get_active_merchant_truth("vons", consistent_read=True)
    assert active is not None
    assert (active.version, active.bundle_hash) == (1, bundle_hash)
    # Convergence writes nothing: still exactly one activation audit.
    assert activation_audits(dynamodb_table, "vons") == ["INITIAL_ACTIVATE"]


def test_flip_with_correct_expected_moves_pointer(
    dynamodb_table: str, capsys: pytest.CaptureFixture[str]
) -> None:
    client = DynamoClient(dynamodb_table)
    seal_live(client, dynamodb_table, "vons", 1)
    hash_v2 = seal_live(client, dynamodb_table, "vons", 2)
    assert (
        amt.main(
            [
                "--slug",
                "vons",
                "--version",
                "1",
                "--flip",
                "--table",
                dynamodb_table,
            ],
            client=client,
        )
        == 0
    )
    capsys.readouterr()

    exit_code = amt.main(
        [
            "--slug",
            "vons",
            "--version",
            "2",
            "--flip",
            "--table",
            dynamodb_table,
            "--activated-by",
            "tyler",
        ],
        client=client,
    )

    assert exit_code == 0
    active = client.get_active_merchant_truth("vons", consistent_read=True)
    assert active is not None
    assert (active.version, active.bundle_hash) == (2, hash_v2)
    assert active.prev_version == 1
    assert active.activated_by == "tyler"
    assert activation_audits(dynamodb_table, "vons") == [
        "INITIAL_ACTIVATE",
        "FLIP_ACTIVE",
    ]
    output = capsys.readouterr().out
    assert "Result: FLIPPED" in output


class StaleReadClient(DynamoClient):
    """Reads a frozen (stale) ACTIVE pointer; writes hit the real table."""

    stale_active: MerchantTruthActive | None = None

    def get_active_merchant_truth(
        self, slug: str, *, consistent_read: bool = False
    ) -> MerchantTruthActive | None:
        del slug, consistent_read
        return self.stale_active


def test_flip_with_stale_expected_conflicts_exit_3(
    dynamodb_table: str, capsys: pytest.CaptureFixture[str]
) -> None:
    client = DynamoClient(dynamodb_table)
    hash_v1 = seal_live(client, dynamodb_table, "vons", 1)
    hash_v2 = seal_live(client, dynamodb_table, "vons", 2)
    seal_live(client, dynamodb_table, "vons", 3)
    client.initial_activate(make_active("vons", 1, hash_v1), dynamodb_table)
    client.flip_active(
        make_active("vons", 2, hash_v2), 1, hash_v1, dynamodb_table
    )
    # A racer that still believes ACTIVE is v1 attempts the flip to v3
    # with that stale expected state — the conditional write must lose.
    stale = StaleReadClient(dynamodb_table)
    stale.stale_active = make_active("vons", 1, hash_v1)

    exit_code = amt.main(
        [
            "--slug",
            "vons",
            "--version",
            "3",
            "--flip",
            "--table",
            dynamodb_table,
        ],
        client=stale,
    )

    assert exit_code == 3
    output = capsys.readouterr().out
    assert "CONFLICT" in output
    assert "Another writer changed the ACTIVE pointer" in output
    # The winner's pointer is untouched.
    active = client.get_active_merchant_truth("vons", consistent_read=True)
    assert active is not None
    assert (active.version, active.bundle_hash) == (2, hash_v2)
    # Both pre-seeded audits share activated_at, so compare unordered:
    # the failed racer must not have added a third activation audit.
    assert sorted(activation_audits(dynamodb_table, "vons")) == [
        "FLIP_ACTIVE",
        "INITIAL_ACTIVATE",
    ]


# ---------------------------------------------------------------------------
# --all-sealed --flip: continue past per-merchant failures
# ---------------------------------------------------------------------------


def test_all_sealed_flip_continues_past_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    alpha, alpha_components = build_sealed("alpha", 1)
    beta, beta_components = build_sealed("beta", 1)
    # Tamper with alpha: a missing component makes the fail-closed loader
    # raise, and the sweep must still activate beta afterward. (alpha
    # sorts first, so the failure precedes the success.)
    client = StubClient(
        [(alpha, alpha_components[:-1]), (beta, beta_components)],
        allow_writes=True,
    )

    exit_code = amt.main(["--all-sealed", "--flip"], client=client)

    assert exit_code == 1
    assert client.write_calls == [("initial", "beta", "1")]
    output = capsys.readouterr().out
    assert "FLIP: about to write ACTIVE pointer(s)" in output
    assert "alpha -> v1" in output
    assert "beta -> v1" in output
    assert "| alpha | 1 | ERROR:" in output
    assert "| beta | 1 | ACTIVATED |" in output


def test_all_sealed_flip_all_success_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    alpha, alpha_components = build_sealed("alpha", 1)
    beta, beta_components = build_sealed("beta", 1)
    client = StubClient(
        [(alpha, alpha_components), (beta, beta_components)],
        allow_writes=True,
    )

    exit_code = amt.main(["--all-sealed", "--flip"], client=client)

    assert exit_code == 0
    assert client.write_calls == [
        ("initial", "alpha", "1"),
        ("initial", "beta", "1"),
    ]
    output = capsys.readouterr().out
    assert "| alpha | 1 | ACTIVATED |" in output
    assert "| beta | 1 | ACTIVATED |" in output


class ThrottledInitialClient(StubClient):
    """A writer whose initial activation hits a transient AWS fault."""

    def initial_activate(self, active, expected_table_name):
        if active.slug == "alpha":
            raise ClientError(
                {
                    "Error": {
                        "Code": "ThrottlingException",
                        "Message": "Rate exceeded",
                    }
                },
                "TransactWriteItems",
            )
        return super().initial_activate(active, expected_table_name)


def test_all_sealed_flip_continues_past_transient_client_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    alpha, alpha_components = build_sealed("alpha", 1)
    beta, beta_components = build_sealed("beta", 1)
    client = ThrottledInitialClient(
        [(alpha, alpha_components), (beta, beta_components)],
        allow_writes=True,
    )

    exit_code = amt.main(["--all-sealed", "--flip"], client=client)

    # alpha's throttle is recorded as an ERROR, beta still activates,
    # and the summary table renders (the sweep never aborts mid-fleet).
    assert exit_code == 1
    assert client.write_calls == [("initial", "beta", "1")]
    output = capsys.readouterr().out
    assert "## Sweep summary" in output
    assert "| alpha | 1 | ERROR: ClientError:" in output
    assert "ThrottlingException" in output
    assert "| beta | 1 | ACTIVATED |" in output


def test_single_conflict_exits_three_with_current_active(
    capsys: pytest.CaptureFixture[str],
) -> None:
    v1, c1 = build_sealed("vons", 1)
    v2, c2 = build_sealed("vons", 2)
    client = BrokenFlipClient(
        [(v1, c1), (v2, c2)],
        [make_active("vons", 1, v1.bundle_hash)],
        allow_writes=True,
    )

    exit_code = amt.main(
        ["--slug", "vons", "--version", "2", "--flip"], client=client
    )

    assert exit_code == 3
    output = capsys.readouterr().out
    assert "CONFLICT" in output
    assert "Current ACTIVE for vons: v1" in output


# ---------------------------------------------------------------------------
# Global alias uniqueness (codex review finding: a colliding activation
# used to succeed and only surface when the loader built the fleet map)
# ---------------------------------------------------------------------------


def test_dry_run_refuses_alias_collision_with_other_merchant(
    dynamodb_table: str, capsys: pytest.CaptureFixture[str]
) -> None:
    client = DynamoClient(dynamodb_table)
    seal_live(client, dynamodb_table, "vons", 1, aliases=["vons", "vons club"])
    assert (
        amt.main(
            [
                "--slug",
                "vons",
                "--version",
                "1",
                "--flip",
                "--table",
                dynamodb_table,
            ],
            client=client,
        )
        == 0
    )
    capsys.readouterr()
    seal_live(
        client,
        dynamodb_table,
        "vons_market",
        1,
        aliases=["vons market", "vons club"],
    )

    # The DRY-RUN already refuses: the collision must surface at review
    # time, not only at write time.
    exit_code = amt.main(
        [
            "--slug",
            "vons_market",
            "--version",
            "1",
            "--table",
            dynamodb_table,
        ],
        client=client,
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "alias collision" in captured.err
    assert "'vons club'" in captured.err
    assert "'vons'" in captured.err
    assert (
        client.get_active_merchant_truth("vons_market", consistent_read=True)
        is None
    )
    assert activation_audits(dynamodb_table, "vons_market") == []


def test_accessor_refuses_colliding_initial_activation(
    dynamodb_table: str,
) -> None:
    """The guard lives in the accessor: no caller can bypass it."""
    client = DynamoClient(dynamodb_table)
    hash_vons = seal_live(
        client, dynamodb_table, "vons", 1, aliases=["vons", "vons club"]
    )
    client.initial_activate(
        MerchantTruthActive(
            slug="vons",
            version=1,
            bundle_hash=hash_vons,
            normalized_aliases=["vons", "vons club"],
            activated_at=NOW,
            activated_by="owner",
        ),
        dynamodb_table,
    )
    hash_other = seal_live(
        client,
        dynamodb_table,
        "vons_market",
        1,
        aliases=["vons market", "vons club"],
    )

    with pytest.raises(MerchantTruthIntegrityError, match="vons club"):
        client.initial_activate(
            MerchantTruthActive(
                slug="vons_market",
                version=1,
                bundle_hash=hash_other,
                normalized_aliases=["vons market", "vons club"],
                activated_at=NOW,
                activated_by="rogue-caller",
            ),
            dynamodb_table,
        )

    assert (
        client.get_active_merchant_truth("vons_market", consistent_read=True)
        is None
    )
    assert activation_audits(dynamodb_table, "vons_market") == []


def test_accessor_refuses_colliding_flip(dynamodb_table: str) -> None:
    client = DynamoClient(dynamodb_table)
    hash_vons = seal_live(
        client, dynamodb_table, "vons", 1, aliases=["vons", "vons club"]
    )
    client.initial_activate(
        MerchantTruthActive(
            slug="vons",
            version=1,
            bundle_hash=hash_vons,
            normalized_aliases=["vons", "vons club"],
            activated_at=NOW,
            activated_by="owner",
        ),
        dynamodb_table,
    )
    hash_b1 = seal_live(client, dynamodb_table, "beta", 1)
    client.initial_activate(make_active("beta", 1, hash_b1), dynamodb_table)
    hash_b2 = seal_live(client, dynamodb_table, "beta", 2)

    with pytest.raises(MerchantTruthIntegrityError, match="vons club"):
        client.flip_active(
            MerchantTruthActive(
                slug="beta",
                version=2,
                bundle_hash=hash_b2,
                normalized_aliases=["beta", "vons club"],
                activated_at=NOW,
                activated_by="rogue-caller",
            ),
            1,
            hash_b1,
            dynamodb_table,
        )

    active = client.get_active_merchant_truth("beta", consistent_read=True)
    assert active is not None
    assert (active.version, active.bundle_hash) == (1, hash_b1)


def test_non_colliding_aliases_still_activate(dynamodb_table: str) -> None:
    """The guard refuses collisions only — disjoint fleets are untouched."""
    client = DynamoClient(dynamodb_table)
    hash_vons = seal_live(client, dynamodb_table, "vons", 1)
    client.initial_activate(make_active("vons", 1, hash_vons), dynamodb_table)
    hash_beta = seal_live(client, dynamodb_table, "beta", 1)

    result = client.initial_activate(
        make_active("beta", 1, hash_beta), dynamodb_table
    )

    assert (result.version, result.bundle_hash) == (1, hash_beta)


def test_all_sealed_flip_continues_past_alias_collision(
    dynamodb_table: str, capsys: pytest.CaptureFixture[str]
) -> None:
    client = DynamoClient(dynamodb_table)
    seal_live(
        client, dynamodb_table, "alpha", 1, aliases=["alpha", "shared mart"]
    )
    seal_live(
        client, dynamodb_table, "beta", 1, aliases=["beta", "shared mart"]
    )
    seal_live(client, dynamodb_table, "gamma", 1)

    exit_code = amt.main(
        ["--all-sealed", "--flip", "--table", dynamodb_table], client=client
    )

    # alpha wins 'shared mart' (activates first, alphabetical sweep),
    # beta is refused as a per-merchant ERROR, gamma still activates.
    assert exit_code == 1
    output = capsys.readouterr().out
    assert "| alpha | 1 | ACTIVATED |" in output
    assert "| beta | 1 | ERROR: alias collision" in output
    assert "| gamma | 1 | ACTIVATED |" in output
    assert (
        client.get_active_merchant_truth("beta", consistent_read=True) is None
    )
    for slug in ("alpha", "gamma"):
        active = client.get_active_merchant_truth(slug, consistent_read=True)
        assert active is not None and active.version == 1
