"""Integration coverage for MerchantTruth activation races."""

import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import MerchantTruthConflictError
from receipt_dynamo.entities.merchant_truth import (
    COMPONENT_NAMES,
    MerchantTruthActive,
    MerchantTruthComponent,
    MerchantTruthManifest,
    MerchantTruthProposal,
    compute_bundle_hash,
)

pytestmark = pytest.mark.integration
SLUG = "sprouts-farmers-market"
NOW = "2026-07-20T16:00:00+00:00"
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


def manifest() -> MerchantTruthManifest:
    components = [
        MerchantTruthComponent(
            slug=SLUG,
            version=1,
            name=name,
            payload={"component": name},
            provenance={"source_kind": "migration"},
        )
        for name in sorted(COMPONENT_NAMES)
    ]
    hashes = {item.name: item.content_hash for item in components}
    return MerchantTruthManifest(
        slug=SLUG,
        version=1,
        component_hashes=hashes,
        bundle_hash=compute_bundle_hash(hashes),
        status="SEALED",
        provenance={"written_by": "test"},
        mint_run_id="run-1",
        gate_status="PASS",
    )


def active(actor: str, digest: str, version: int = 1) -> MerchantTruthActive:
    return MerchantTruthActive(
        slug=SLUG,
        version=version,
        bundle_hash=digest,
        normalized_aliases=["sprouts", "sprouts farmers market"],
        activated_at=NOW,
        activated_by=actor,
    )


def sealed_version(client: DynamoClient, table: str, version: int) -> str:
    """Mint + seal one version and return its bundle hash."""
    components = [
        MerchantTruthComponent(
            slug=SLUG,
            version=version,
            name=name,
            payload={"component": name, "v": version},
            provenance=LEGACY_PROVENANCE,
        )
        for name in sorted(COMPONENT_NAMES)
    ]
    client.mint_version(
        SLUG,
        version,
        components,
        MINT_PROVENANCE,
        f"run-{version}",
        table,
        created_at=NOW,
    )
    sealed = client.seal_version(
        SLUG,
        version,
        {"status": "PASS", "report": f"s3://eval/v{version}.json"},
        [],
        table,
        sealed_at=NOW,
    )
    return sealed.bundle_hash


def proposal_status(client: DynamoClient, claim_slug: str) -> str:
    proposals = client.list_merchant_truth_proposals(SLUG)
    return next(p for p in proposals if p.claim_slug == claim_slug).status


def test_flip_and_rollback_reconcile_proposal_effectivity(
    dynamodb_table: str,
) -> None:
    client = DynamoClient(dynamodb_table)
    hash_v1 = sealed_version(client, dynamodb_table, 1)
    hash_v2 = sealed_version(client, dynamodb_table, 2)

    client.initial_activate(
        active("owner", hash_v1, version=1), dynamodb_table
    )

    proposal = MerchantTruthProposal(
        slug=SLUG,
        created_at=NOW,
        claim_slug="bold-headers",
        claim="headers are bold",
    )
    client.add_proposal(proposal, dynamodb_table)
    client.resolve_proposal(proposal, 2, "confirmed", dynamodb_table)
    assert proposal_status(client, "bold-headers") == "MEASURED_IN_CANDIDATE"

    # Forward flip to the version that measured the proposal makes it
    # EFFECTIVE (derived, never written directly).
    client.flip_active(
        active("owner", hash_v2, version=2), 1, hash_v1, dynamodb_table
    )
    assert proposal_status(client, "bold-headers") == "EFFECTIVE"

    # Rollback to the prior version reverts the proposal out of EFFECTIVE
    # because its measuring version is no longer ACTIVE.
    client.flip_active(
        active("owner", hash_v1, version=1), 2, hash_v2, dynamodb_table
    )
    assert proposal_status(client, "bold-headers") == "MEASURED_IN_CANDIDATE"


def test_second_initial_activation_is_rejected_and_pointer_converges(
    dynamodb_table: str,
) -> None:
    # Deterministic form of the first-activation race: moto's in-memory
    # conditional writes are not atomic under real threads, so a threaded
    # barrier race flakes on parallel CI runners (both writers can pass the
    # condition read). Real DynamoDB arbitrates the condition server-side;
    # what the guard must prove is that a second writer with the same
    # ACTIVE-absent condition loses once the first has committed.
    client = DynamoClient(dynamodb_table)
    sealed = manifest()
    client._client.put_item(  # pylint: disable=protected-access
        TableName=dynamodb_table,
        Item=sealed.to_item(),
    )

    winner = client.initial_activate(
        active("owner-a", sealed.bundle_hash), dynamodb_table
    )
    assert (winner.version, winner.bundle_hash) == (1, sealed.bundle_hash)

    # A second writer with the same target converges on the existing
    # pointer instead of raising (idempotent bootstrap), and writes no
    # second audit row.
    second = client.initial_activate(
        active("owner-b", sealed.bundle_hash), dynamodb_table
    )
    assert (second.version, second.bundle_hash) == (1, sealed.bundle_hash)

    current = client.get_active_merchant_truth(SLUG, consistent_read=True)
    assert current is not None
    assert current.version == 1
    assert current.bundle_hash == sealed.bundle_hash
    assert current.activated_by == "owner-a"
    response = client._client.query(  # pylint: disable=protected-access
        TableName=dynamodb_table,
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={
            ":pk": {"S": f"MERCHANT_TRUTH#{SLUG}"},
            ":sk": {"S": "AUDIT#"},
        },
    )
    assert len(response["Items"]) == 1


def test_flip_with_stale_expected_state_conflicts_one_winner(
    dynamodb_table: str,
) -> None:
    # Deterministic form of the same-prev flip race (see note on the
    # initial-activation test): writer A flips 1->2; writer B then attempts
    # 1->3 with the identical stale expected {version=1, hash_v1} a true
    # race loser would carry, and must get MerchantTruthConflictError while
    # ACTIVE stays on the winner.
    client = DynamoClient(dynamodb_table)
    hash_v1 = sealed_version(client, dynamodb_table, 1)
    targets = {
        2: sealed_version(client, dynamodb_table, 2),
        3: sealed_version(client, dynamodb_table, 3),
    }
    client.initial_activate(
        active("owner", hash_v1, version=1), dynamodb_table
    )

    winner = client.flip_active(
        active("owner-2", targets[2], version=2), 1, hash_v1, dynamodb_table
    )
    assert winner.version == 2

    with pytest.raises(MerchantTruthConflictError):
        client.flip_active(
            active("owner-3", targets[3], version=3),
            1,
            hash_v1,
            dynamodb_table,
        )

    current = client.get_active_merchant_truth(SLUG, consistent_read=True)
    assert current is not None
    assert current.version == 2
