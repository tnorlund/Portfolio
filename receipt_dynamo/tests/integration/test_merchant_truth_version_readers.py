"""Moto coverage for the §7.4 version-number readers.

Exercises the readers against a real (moto) partition: an empty partition,
a v1-only partition, a sealed+open mix (where ``sealed_only`` must skip a
higher OPEN version), and a gap left by an OPEN-version cleanup (where
"latest" is whatever the descending Query returns, never a count).
"""

from __future__ import annotations

import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.merchant_truth import (
    COMPONENT_NAMES,
    MerchantTruthComponent,
)
from receipt_dynamo.migrations.merchant_truth_v1_live import (
    cleanup_unsealed_open_version,
)

pytestmark = pytest.mark.integration
SLUG = "costco-wholesale"
NOW = "2026-07-22T16:00:00+00:00"
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


def _components(version: int) -> list[MerchantTruthComponent]:
    return [
        MerchantTruthComponent(
            slug=SLUG,
            version=version,
            name=name,
            payload={"component": name, "v": version},
            provenance=dict(LEGACY_PROVENANCE),
        )
        for name in sorted(COMPONENT_NAMES)
    ]


def _mint_open(client: DynamoClient, table: str, version: int) -> None:
    client.mint_version(
        SLUG,
        version,
        _components(version),
        MINT_PROVENANCE,
        f"run-{version}",
        table,
        created_at=NOW,
    )


def _mint_and_seal(client: DynamoClient, table: str, version: int) -> None:
    _mint_open(client, table, version)
    client.seal_version(
        SLUG,
        version,
        {"status": "PASS", "report": f"s3://eval/v{version}.json"},
        [],
        table,
        sealed_at=NOW,
    )


def test_latest_and_next_over_empty_partition(dynamodb_table: str) -> None:
    client = DynamoClient(dynamodb_table)

    assert (
        client.get_latest_merchant_truth_version(SLUG, sealed_only=False)
        is None
    )
    assert (
        client.get_latest_merchant_truth_version(SLUG, sealed_only=True)
        is None
    )
    assert client.next_mint_version(SLUG) == 1


def test_latest_and_next_with_v1_only(dynamodb_table: str) -> None:
    client = DynamoClient(dynamodb_table)
    _mint_and_seal(client, dynamodb_table, 1)

    assert (
        client.get_latest_merchant_truth_version(SLUG, sealed_only=False) == 1
    )
    assert (
        client.get_latest_merchant_truth_version(SLUG, sealed_only=True) == 1
    )
    assert client.next_mint_version(SLUG) == 2


def test_sealed_only_skips_a_higher_open_version(
    dynamodb_table: str,
) -> None:
    client = DynamoClient(dynamodb_table)
    _mint_and_seal(client, dynamodb_table, 1)
    _mint_open(client, dynamodb_table, 2)  # in-flight, never sealed

    # mint allocation must see the OPEN v2 so it never collides...
    assert (
        client.get_latest_merchant_truth_version(SLUG, sealed_only=False) == 2
    )
    assert client.next_mint_version(SLUG) == 3
    # ...while promotion/diff want the highest SEALED, which is still v1.
    assert (
        client.get_latest_merchant_truth_version(SLUG, sealed_only=True) == 1
    )


def test_gap_after_open_version_cleanup_is_legal(
    dynamodb_table: str,
) -> None:
    client = DynamoClient(dynamodb_table)
    _mint_and_seal(client, dynamodb_table, 1)
    _mint_open(client, dynamodb_table, 2)  # abandoned OPEN
    _mint_and_seal(client, dynamodb_table, 3)

    # Clean up the abandoned OPEN v2, leaving a permanent gap below v3.
    result = cleanup_unsealed_open_version(
        client._client,
        table_name=dynamodb_table,
        slug=SLUG,
        version=2,
        explicit_table=True,
        delete=True,
    )
    assert result.deleted

    # "latest" is whatever the descending Query returns, never a count:
    # the v2 gap does not fool the reader into reporting v2.
    assert (
        client.get_latest_merchant_truth_version(SLUG, sealed_only=False) == 3
    )
    assert (
        client.get_latest_merchant_truth_version(SLUG, sealed_only=True) == 3
    )
    assert client.next_mint_version(SLUG) == 4
