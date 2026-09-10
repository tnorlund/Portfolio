"""Moto verifies the revision compare-and-swap on ReceiptFactOverride."""

from dataclasses import replace
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient
from receipt_dynamo.data._receipt_fact_override import (
    PROTECTED_FACT_TABLE_MARKERS,
)
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBThroughputError,
    EntityAlreadyExistsError,
    EntityValidationError,
    FactOverrideConflictError,
)
from receipt_dynamo.entities.receipt_fact_override import ReceiptFactOverride

pytestmark = [pytest.mark.integration]

IMAGE_ID = "b7eecdb7-9eaf-47c0-941a-b576604c2e9d"


@pytest.fixture
def client(dynamodb_table: str) -> DynamoClient:
    return DynamoClient(dynamodb_table)


def override(**changes) -> ReceiptFactOverride:
    fields = dict(
        image_id=IMAGE_ID,
        receipt_id=1,
        revision=1,
        date="2026-09-01",
        date_reference="Chase statement 2026-09-01 $47.18",
        changed_at="2026-09-10T00:00:00+00:00",
    )
    fields.update(changes)
    return ReceiptFactOverride(**fields)


def test_get_returns_none_when_nothing_stated(client: DynamoClient):
    assert client.get_receipt_fact_override(IMAGE_ID, 1) is None


def test_add_then_get_round_trips(client: DynamoClient):
    stated = override()

    assert client.add_receipt_fact_override(stated) == stated
    assert client.get_receipt_fact_override(IMAGE_ID, 1) == stated


def test_add_refuses_a_second_override_for_the_receipt(
    client: DynamoClient,
):
    client.add_receipt_fact_override(override())

    with pytest.raises(EntityAlreadyExistsError):
        client.add_receipt_fact_override(override(date="2026-09-02"))
    assert client.get_receipt_fact_override(IMAGE_ID, 1).date == "2026-09-01"


def test_add_requires_revision_one(client: DynamoClient):
    with pytest.raises(EntityValidationError):
        client.add_receipt_fact_override(override(revision=2))


def test_update_with_matching_revision_succeeds(client: DynamoClient):
    client.add_receipt_fact_override(override())
    stored = client.get_receipt_fact_override(IMAGE_ID, 1)

    updated = stored.with_fact(
        "merchant_name", "Trader Joe's", "cropped header", revision=2
    )
    client.update_receipt_fact_override(updated, expected_revision=1)

    assert client.get_receipt_fact_override(IMAGE_ID, 1) == updated


def test_update_with_stale_revision_is_a_conflict(client: DynamoClient):
    client.add_receipt_fact_override(override())
    client.update_receipt_fact_override(
        override(revision=2, date="2026-09-02"), expected_revision=1
    )

    with pytest.raises(FactOverrideConflictError):
        client.update_receipt_fact_override(
            override(revision=2, date="2026-09-03"), expected_revision=1
        )
    assert client.get_receipt_fact_override(IMAGE_ID, 1).date == "2026-09-02"


def test_update_of_a_missing_override_is_a_conflict(client: DynamoClient):
    with pytest.raises(FactOverrideConflictError):
        client.update_receipt_fact_override(
            override(revision=2), expected_revision=1
        )
    assert client.get_receipt_fact_override(IMAGE_ID, 1) is None


def test_retracting_every_fact_keeps_the_row_and_revision(
    client: DynamoClient,
):
    client.add_receipt_fact_override(override())
    retracted = override(revision=2, date=None, date_reference=None)

    client.update_receipt_fact_override(retracted, expected_revision=1)

    stored = client.get_receipt_fact_override(IMAGE_ID, 1)
    assert stored == retracted
    assert stored.facts == {}
    # A stale editor holding revision 1 is still refused.
    with pytest.raises(FactOverrideConflictError):
        client.update_receipt_fact_override(
            override(revision=2, date="2026-09-03"), expected_revision=1
        )
    # And a fresh create is refused: the row still exists.
    with pytest.raises(EntityAlreadyExistsError):
        client.add_receipt_fact_override(override())


@pytest.mark.parametrize("expected, revision", [(1, 1), (1, 3), (0, 1)])
def test_update_requires_the_next_revision(
    client: DynamoClient, expected: int, revision: int
):
    with pytest.raises(EntityValidationError):
        client.update_receipt_fact_override(
            override(revision=revision), expected_revision=expected
        )


def test_delete_with_matching_revision_removes_the_row(
    client: DynamoClient,
):
    client.add_receipt_fact_override(override())

    client.delete_receipt_fact_override(IMAGE_ID, 1, expected_revision=1)

    assert client.get_receipt_fact_override(IMAGE_ID, 1) is None


def test_delete_with_stale_revision_is_a_conflict(client: DynamoClient):
    client.add_receipt_fact_override(override())

    with pytest.raises(FactOverrideConflictError):
        client.delete_receipt_fact_override(IMAGE_ID, 1, expected_revision=2)
    assert client.get_receipt_fact_override(IMAGE_ID, 1) == override()


def test_delete_of_a_missing_override_is_a_conflict(client: DynamoClient):
    with pytest.raises(FactOverrideConflictError):
        client.delete_receipt_fact_override(IMAGE_ID, 1, expected_revision=1)


def test_mutated_entity_without_provenance_is_refused(
    client: DynamoClient,
):
    """A fact assigned after construction has no reference; the write
    must re-validate rather than persist a row later reads reject."""
    mutated = ReceiptFactOverride(image_id=IMAGE_ID, receipt_id=1)
    mutated.date = "2026-09-01"

    with pytest.raises(EntityValidationError, match="date_reference"):
        client.add_receipt_fact_override(mutated)
    assert client.get_receipt_fact_override(IMAGE_ID, 1) is None

    client.add_receipt_fact_override(override())
    stale = override(revision=2)
    stale.merchant_name = "Costco"  # no merchant_name_reference
    with pytest.raises(EntityValidationError, match="merchant_name_reference"):
        client.update_receipt_fact_override(stale, expected_revision=1)
    assert client.get_receipt_fact_override(IMAGE_ID, 1) == override()


def test_override_does_not_shadow_the_summary_row(client: DynamoClient):
    client.add_receipt_fact_override(override())

    response = client._client.query(
        TableName=client.table_name,
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": {"S": f"IMAGE#{IMAGE_ID}"}},
    )

    assert [item["SK"]["S"] for item in response["Items"]] == [
        "RECEIPT#00001#FACT_OVERRIDE"
    ]
    assert [item["TYPE"]["S"] for item in response["Items"]] == [
        "RECEIPT_FACT_OVERRIDE"
    ]


def test_accessors_validate_identifiers(client: DynamoClient):
    with pytest.raises(EntityValidationError):
        client.get_receipt_fact_override("not-a-uuid", 1)
    with pytest.raises(EntityValidationError):
        client.get_receipt_fact_override(IMAGE_ID, 0)
    with pytest.raises(EntityValidationError):
        client.delete_receipt_fact_override(
            "not-a-uuid", 1, expected_revision=1
        )
    with pytest.raises(EntityValidationError):
        client.delete_receipt_fact_override(IMAGE_ID, 1, expected_revision=0)


def test_throttling_is_mapped_not_swallowed_as_conflict(
    client: DynamoClient,
):
    error = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException"}},
        "PutItem",
    )
    with patch.object(client._client, "put_item", side_effect=error):
        with pytest.raises(DynamoDBThroughputError):
            client.update_receipt_fact_override(
                override(revision=2), expected_revision=1
            )


@pytest.fixture
def protected_client(dynamodb_table: str) -> DynamoClient:
    """A moto table whose name carries the protected marker."""
    dynamodb = boto3.client("dynamodb", region_name="us-east-1")
    name = f"Refused-{PROTECTED_FACT_TABLE_MARKERS[0]}"
    dynamodb.create_table(
        TableName=name,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    return DynamoClient(name)


def test_every_write_refuses_a_protected_table(
    protected_client: DynamoClient,
):
    with pytest.raises(EntityValidationError, match="refused"):
        protected_client.add_receipt_fact_override(override())
    with pytest.raises(EntityValidationError, match="refused"):
        protected_client.update_receipt_fact_override(
            override(revision=2), expected_revision=1
        )
    with pytest.raises(EntityValidationError, match="refused"):
        protected_client.delete_receipt_fact_override(
            IMAGE_ID, 1, expected_revision=1
        )
    scan = protected_client._client.scan(TableName=protected_client.table_name)
    assert scan["Count"] == 0
    # Reads stay allowed: diagnostics against any table are harmless.
    assert protected_client.get_receipt_fact_override(IMAGE_ID, 1) is None


def test_dev_table_names_are_not_refused(client: DynamoClient):
    assert not any(
        marker in client.table_name for marker in PROTECTED_FACT_TABLE_MARKERS
    )
    client.add_receipt_fact_override(override())
    assert client.get_receipt_fact_override(IMAGE_ID, 1) == override()
