"""Integration coverage for MerchantTruth activation races."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.merchant_truth import (
    COMPONENT_NAMES,
    MerchantTruthActive,
    MerchantTruthComponent,
    MerchantTruthManifest,
    compute_bundle_hash,
)

pytestmark = pytest.mark.integration
SLUG = "sprouts-farmers-market"
NOW = "2026-07-20T16:00:00+00:00"


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


def active(actor: str, digest: str) -> MerchantTruthActive:
    return MerchantTruthActive(
        slug=SLUG,
        version=1,
        bundle_hash=digest,
        normalized_aliases=["sprouts", "sprouts farmers market"],
        activated_at=NOW,
        activated_by=actor,
    )


def test_two_concurrent_first_activations_converge_on_one_pointer(
    dynamodb_table: str,
) -> None:
    client = DynamoClient(dynamodb_table)
    sealed = manifest()
    client._client.put_item(  # pylint: disable=protected-access
        TableName=dynamodb_table,
        Item=sealed.to_item(),
    )
    barrier = Barrier(2)

    def activate(actor: str) -> MerchantTruthActive:
        barrier.wait()
        target = active(actor, sealed.bundle_hash)
        return client.initial_activate(target, dynamodb_table)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(activate, ("owner-a", "owner-b")))

    assert {(item.version, item.bundle_hash) for item in results} == {
        (1, sealed.bundle_hash)
    }
    current = client.get_active_merchant_truth(SLUG, consistent_read=True)
    assert current is not None
    assert current.version == 1
    assert current.bundle_hash == sealed.bundle_hash
    response = client._client.query(  # pylint: disable=protected-access
        TableName=dynamodb_table,
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={
            ":pk": {"S": f"MERCHANT_TRUTH#{SLUG}"},
            ":sk": {"S": "AUDIT#"},
        },
    )
    assert len(response["Items"]) == 1
