"""Owner-gated cleanup of a failed (never-sealed) OPEN mint + recovery.

Simulates the orphaned G1 costco state — an OPEN manifest with legacy
AttributeValue-encoded component payloads that can never re-verify — and
proves the documented recovery path: cleanup deletes exactly the OPEN
version rows (audits preserved), after which the fixed code re-mints and
seals the same version successfully.
"""

from typing import Any

import boto3
import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    MerchantTruthConflictError,
    MerchantTruthTableMismatchError,
)
from receipt_dynamo.entities.dynamodb_utils import to_dynamodb_value
from receipt_dynamo.entities.merchant_truth import (
    COMPONENT_NAMES,
    MerchantTruthAudit,
    MerchantTruthComponent,
    MerchantTruthManifest,
    compute_bundle_hash,
)
from receipt_dynamo.migrations.merchant_truth_v1_live import (
    PROD_TABLE_NAME,
    cleanup_unsealed_open_version,
)

pytestmark = pytest.mark.integration
SLUG = "costco_wholesale"
NOW = "2026-07-21T16:00:00+00:00"
LEGACY_PROVENANCE = {
    "source_kind": "migration",
    "provenance_completeness": "legacy",
    "written_by": {
        "kind": "migration",
        "name": "merchant_truth_v1",
        "version": "1",
    },
}
# Real G1 culprit values: integral floats whose AttributeValue form
# real DynamoDB normalizes (0.0 -> 0), breaking hash re-verification.
PAYLOADS: dict[str, Any] = {
    name: {"component": name, "bitmap_thin": 0.0, "scale": 1.0}
    for name in sorted(COMPONENT_NAMES)
}


def build_components() -> list[MerchantTruthComponent]:
    return [
        MerchantTruthComponent(
            slug=SLUG,
            version=1,
            name=name,
            payload=PAYLOADS[name],
            provenance=dict(LEGACY_PROVENANCE),
        )
        for name in sorted(COMPONENT_NAMES)
    ]


def seed_orphaned_open_mint(table: str) -> None:
    """Write the failed-mint state exactly as the old code left it in dev:
    OPEN manifest + legacy AttributeValue-encoded components + MINT audit.
    """
    dynamodb = boto3.client("dynamodb", region_name="us-east-1")
    components = build_components()
    hashes = {item.name: item.content_hash for item in components}
    manifest = MerchantTruthManifest(
        slug=SLUG,
        version=1,
        component_hashes=hashes,
        bundle_hash=compute_bundle_hash(hashes),
        status="OPEN",
        provenance={"written_by": LEGACY_PROVENANCE["written_by"]},
        mint_run_id="merchant-truth-v1-costco-orphan",
    )
    dynamodb.put_item(TableName=table, Item=manifest.to_item())
    for component in components:
        item = component.to_item()
        # The pre-fix representation: a native AttributeValue tree.
        item["payload"] = to_dynamodb_value(component.payload)
        dynamodb.put_item(TableName=table, Item=item)
    audit = MerchantTruthAudit(
        slug=SLUG,
        created_at=NOW,
        audit_id="orphanedmintauditrecord001",
        action="MINT",
        version=1,
        bundle_hash=manifest.bundle_hash,
        details={"run_id": manifest.mint_run_id, "component_count": 7},
    )
    dynamodb.put_item(TableName=table, Item=audit.to_item())


def version_row_keys(table: str) -> list[str]:
    response = boto3.client("dynamodb", region_name="us-east-1").query(
        TableName=table,
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={
            ":pk": {"S": f"MERCHANT_TRUTH#{SLUG}"},
            ":sk": {"S": "TRUTH#v0000000001#"},
        },
        ConsistentRead=True,
    )
    return sorted(item["SK"]["S"] for item in response["Items"])


def audit_row_count(table: str) -> int:
    response = boto3.client("dynamodb", region_name="us-east-1").query(
        TableName=table,
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={
            ":pk": {"S": f"MERCHANT_TRUTH#{SLUG}"},
            ":sk": {"S": "AUDIT#"},
        },
        ConsistentRead=True,
    )
    return len(response["Items"])


def cleanup(table: str, *, delete: bool):
    return cleanup_unsealed_open_version(
        boto3.client("dynamodb", region_name="us-east-1"),
        table_name=table,
        slug=SLUG,
        version=1,
        explicit_table=True,
        delete=delete,
    )


def test_orphaned_legacy_rows_cannot_seal_even_after_the_fix(
    dynamodb_table: str,
) -> None:
    """The stored representation is unverifiable: reject, do not resume."""
    seed_orphaned_open_mint(dynamodb_table)
    client = DynamoClient(dynamodb_table)

    with pytest.raises(ValueError, match="legacy AttributeValue"):
        client.seal_version(
            SLUG,
            1,
            {"status": "PASS", "passed": True},
            [],
            dynamodb_table,
            sealed_at=NOW,
        )


def test_cleanup_dry_run_lists_rows_and_deletes_nothing(
    dynamodb_table: str,
) -> None:
    seed_orphaned_open_mint(dynamodb_table)

    result = cleanup(dynamodb_table, delete=False)

    assert not result.deleted
    assert len(result.found_keys) == 8  # manifest + 7 components
    assert len(version_row_keys(dynamodb_table)) == 8
    assert audit_row_count(dynamodb_table) == 1


def test_cleanup_deletes_open_rows_and_preserves_audits(
    dynamodb_table: str,
) -> None:
    seed_orphaned_open_mint(dynamodb_table)

    result = cleanup(dynamodb_table, delete=True)

    assert result.deleted
    assert len(result.found_keys) == 8
    assert version_row_keys(dynamodb_table) == []
    assert audit_row_count(dynamodb_table) == 1


def test_cleanup_refuses_sealed_versions(dynamodb_table: str) -> None:
    client = DynamoClient(dynamodb_table)
    components = build_components()
    client.mint_version(
        SLUG,
        1,
        components,
        {"written_by": LEGACY_PROVENANCE["written_by"]},
        "run-sealed",
        dynamodb_table,
        created_at=NOW,
    )
    client.seal_version(
        SLUG,
        1,
        {"status": "PASS", "passed": True},
        [],
        dynamodb_table,
        sealed_at=NOW,
    )

    with pytest.raises(MerchantTruthConflictError, match="OPEN"):
        cleanup(dynamodb_table, delete=True)
    assert len(version_row_keys(dynamodb_table)) == 8


def test_cleanup_refuses_prod_table_unconditionally() -> None:
    class ExplodingDynamo:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError("prod refusal must precede any call")

    for explicit in (True, False):
        with pytest.raises(
            MerchantTruthTableMismatchError, match="unconditional"
        ):
            cleanup_unsealed_open_version(
                ExplodingDynamo(),
                table_name=PROD_TABLE_NAME,
                slug=SLUG,
                version=1,
                explicit_table=explicit,
                delete=True,
            )


def test_cleanup_is_a_noop_when_the_version_is_absent(
    dynamodb_table: str,
) -> None:
    result = cleanup(dynamodb_table, delete=True)

    assert not result.deleted
    assert result.found_keys == ()


def test_recovery_end_to_end_cleanup_then_remint_and_seal(
    dynamodb_table: str,
) -> None:
    """The full G1 recovery: cleanup, re-mint with fixed code, seal, verify."""
    seed_orphaned_open_mint(dynamodb_table)
    cleanup(dynamodb_table, delete=True)

    client = DynamoClient(dynamodb_table)
    components = build_components()
    client.mint_version(
        SLUG,
        1,
        components,
        {"written_by": LEGACY_PROVENANCE["written_by"]},
        "merchant-truth-v1-costco-remint",
        dynamodb_table,
        created_at=NOW,
    )
    sealed = client.seal_version(
        SLUG,
        1,
        {"status": "PASS", "passed": True},
        [],
        dynamodb_table,
        sealed_at=NOW,
    )

    assert sealed.status == "SEALED"
    read_back = {
        component.name: component
        for component in client.list_merchant_truth_components(
            SLUG, 1, consistent_read=True
        )
    }
    assert set(read_back) == COMPONENT_NAMES
    for component in components:
        assert read_back[component.name].payload == component.payload
        assert read_back[component.name].content_hash == component.content_hash
    assert audit_row_count(dynamodb_table) == 3  # orphan MINT + MINT + SEAL
