"""Same adversarial cases for the bounded atomic receipt document."""

from typing import Any
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBThroughputError,
    EntityNotFoundError,
    EntityValidationError,
    NutritionConflictError,
)
from receipt_dynamo.entities.nutrition_support import nutrition_json
from receipt_dynamo.entities.receipt_nutrition import nutrition_receipt_key

pytestmark = [pytest.mark.integration, pytest.mark.unused_in_production]
IMAGE = "9e2816f8-429a-4b6b-9a21-0887cd07c0b2"
CONTEXT = {"product_revision": "label-v1", "quantity_override": "2 packages"}


def parent(
    client: DynamoClient, stamp: str = "2026-09-08T00:00:00+00:00"
) -> None:
    client._client.put_item(
        TableName=client.table_name,
        Item={
            **nutrition_receipt_key(IMAGE, 1),
            "TYPE": {"S": "RECEIPT"},
            "timestamp_added": {"S": stamp},
            "raw_s3_key": {"S": "receipt.png"},
        },
    )


def line(client: DynamoClient, index: int = 0, name: str = "old food") -> None:
    client._client.put_item(
        TableName=client.table_name,
        Item={
            "PK": {"S": f"IMAGE#{IMAGE}"},
            "SK": {"S": f"RECEIPT#00001#LINE_ITEM#{index:05d}"},
            "TYPE": {"S": "RECEIPT_LINE_ITEM"},
            "name": {"S": name},
        },
    )


@pytest.fixture
def client(dynamodb_table: str) -> DynamoClient:
    store = DynamoClient(dynamodb_table)
    parent(store)
    line(store)
    return store


def save(client: DynamoClient, revision: str | None = None, **kwargs: Any):
    params = {
        "source": client.get_nutrition_input(IMAGE, 1),
        "context": CONTEXT,
        "payload_json": '{"rows":[{"item":0}],"summary":{"count":1}}',
        "expected_revision": revision,
        "expected_table_name": client.table_name,
        **kwargs,
    }
    return client.save_receipt_nutrition(**params)


def read(client: DynamoClient, context: dict[str, Any] = CONTEXT):
    return client.get_receipt_nutrition(IMAGE, 1, context=context)


def test_document_is_atomic_and_identical_retry_is_idempotent(client):
    first = save(client)
    assert first.stale  # Only the read API verifies current freshness.
    assert not read(client).stale
    assert save(client).revision == first.revision
    before = client._client.query(
        TableName=client.table_name,
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": {"S": f"IMAGE#{IMAGE}"}},
    )["Items"]
    assert len(before) == 3  # Parent, source line, one nutrition document.
    assert all("GSI1PK" not in item for item in before)


def test_recreated_parent_cannot_inherit_fresh_nutrition(client):
    old = save(client)
    client._client.delete_item(
        TableName=client.table_name, Key=nutrition_receipt_key(IMAGE, 1)
    )
    assert read(client) is None
    parent(client, "2026-09-09T00:00:00+00:00")
    assert read(client).stale
    save(client, old.revision)
    assert not read(client).stale


def test_same_timestamp_different_parent_content_is_stale(client):
    save(client)
    client._client.update_item(
        TableName=client.table_name,
        Key=nutrition_receipt_key(IMAGE, 1),
        UpdateExpression="SET raw_s3_key = :key",
        ExpressionAttributeValues={":key": {"S": "different.png"}},
    )
    assert read(client).stale


def test_source_rewrite_without_stream_event_is_stale_and_blocks_save(client):
    first = save(client)
    source = client.get_nutrition_input(IMAGE, 1)
    line(client, name="new food")
    assert read(client).stale
    with pytest.raises(NutritionConflictError):
        save(client, first.revision, source=source)
    save(client, first.revision)
    assert not read(client).stale


def test_source_changes_after_prewrite_check_still_read_stale(client):
    transact = client._nutrition_transact

    def race(actions):
        line(client, name="refined after check")
        return transact(actions)

    with patch.object(client, "_nutrition_transact", side_effect=race):
        assert save(client).stale
    assert read(client).stale


def test_parent_deleted_between_check_and_write_does_not_publish(client):
    transact = client._nutrition_transact

    def race(actions):
        client._client.delete_item(
            TableName=client.table_name, Key=nutrition_receipt_key(IMAGE, 1)
        )
        return transact(actions)

    with patch.object(client, "_nutrition_transact", side_effect=race):
        with pytest.raises(NutritionConflictError):
            save(client)
    assert read(client) is None
    with pytest.raises(EntityNotFoundError):
        # Use a captured source after deletion rather than a missing token.
        parent(client)
        source = client.get_nutrition_input(IMAGE, 1)
        client._client.delete_item(
            TableName=client.table_name, Key=nutrition_receipt_key(IMAGE, 1)
        )
        save(client, source=source)


@pytest.mark.parametrize(
    "context",
    [
        {**CONTEXT, "product_revision": "corrected-label"},
        {**CONTEXT, "quantity_override": "3 packages"},
        {**CONTEXT, "alias_revision": 2},
    ],
)
def test_explicit_dependency_corrections_invalidate_snapshot(client, context):
    old = save(client)
    assert read(client, context).stale
    save(client, old.revision, context=context)
    assert not read(client, context).stale


def test_competing_old_writer_cannot_replace_new_context(client):
    old = save(client)
    new = save(client, old.revision, context={**CONTEXT, "alias_revision": 2})
    with pytest.raises(NutritionConflictError):
        save(client, old.revision, context={**CONTEXT, "alias_revision": 1})
    assert read(client).revision == new.revision


def test_failed_replacement_keeps_complete_previous_document(client):
    old = save(client)
    with patch.object(
        client,
        "_nutrition_transact",
        side_effect=DynamoDBThroughputError("injected"),
    ):
        with pytest.raises(DynamoDBThroughputError):
            save(
                client,
                old.revision,
                payload_json='{"rows":[],"summary":{"count":0}}',
            )
    assert read(client).revision == old.revision
    assert read(client).payload_json == old.payload_json
    save(
        client, old.revision, payload_json='{"rows":[],"summary":{"count":0}}'
    )
    assert read(client).payload_json == '{"rows":[],"summary":{"count":0}}'


def test_size_rejected_before_source_or_write_io(client):
    with patch.object(client, "get_nutrition_input") as io:
        with pytest.raises(EntityValidationError):
            save(
                client,
                source=object(),
                payload_json=nutrition_json({"rows": [], "summary": {}}),
            )
    # Separate direct size validation uses a valid source token.
    source = client.get_nutrition_input(IMAGE, 1)
    with patch.object(client, "get_nutrition_input") as io:
        with pytest.raises(EntityValidationError):
            client.save_receipt_nutrition(
                source,
                context=CONTEXT,
                payload_json='{"rows":[],"summary":{"large":"'
                + "x" * 300001
                + '"}}',
                expected_revision=None,
                expected_table_name=client.table_name,
            )
        io.assert_not_called()


def test_paginated_source_and_reader_churn(client):
    line(client, 1)
    query = client._client.query

    def page(**kwargs):
        return query(**{**kwargs, "Limit": 1})

    with patch.object(client._client, "query", side_effect=page):
        source = client.get_nutrition_input(IMAGE, 1)
    assert "LINE_ITEM#00001" in source.source_json
    once = client._nutrition_source_once
    calls = 0

    def churn(*args):
        nonlocal calls
        calls += 1
        line(client, name=f"changing-{calls}")
        return once(*args)

    with patch.object(client, "_nutrition_source_once", side_effect=churn):
        with pytest.raises(NutritionConflictError):
            client.get_nutrition_input(IMAGE, 1)


@pytest.mark.parametrize(
    "reason",
    [
        "TransactionConflict",
        "ThrottlingError",
        "ProvisionedThroughputExceeded",
    ],
)
def test_transaction_contention_retries_and_surfaces_exhaustion(
    client, reason
):
    conflict = ClientError(
        {
            "Error": {"Code": "TransactionCanceledException"},
            "CancellationReasons": [
                {"Code": "None"},
                {"Code": reason},
            ],
        },
        "TransactWriteItems",
    )
    real = client._client.transact_write_items
    calls = 0

    def transient(**kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise conflict
        return real(**kwargs)

    with (
        patch("receipt_dynamo.data._nutrition_catalog.sleep"),
        patch.object(
            client._client, "transact_write_items", side_effect=transient
        ),
    ):
        save(client)
    assert calls == 3
    with (
        patch("receipt_dynamo.data._nutrition_catalog.sleep"),
        patch.object(
            client._client, "transact_write_items", side_effect=conflict
        ) as txn,
    ):
        with pytest.raises(DynamoDBThroughputError):
            save(client, read(client).revision, context={"revision": 2})
        assert txn.call_count == 4


@pytest.mark.parametrize(
    "code",
    [
        "TransactionConflictException",
        "ThrottlingException",
        "ProvisionedThroughputExceededException",
        "RequestLimitExceeded",
        "TransactionInProgressException",
    ],
)
def test_top_level_transient_transaction_errors(client, code):
    error = ClientError({"Error": {"Code": code}}, "TransactWriteItems")
    real = client._client.transact_write_items
    calls = 0

    def transient(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error
        return real(**kwargs)

    with (
        patch("receipt_dynamo.data._nutrition_catalog.sleep"),
        patch.object(
            client._client, "transact_write_items", side_effect=transient
        ),
    ):
        save(client)
    assert calls == 2
    with (
        patch("receipt_dynamo.data._nutrition_catalog.sleep"),
        patch.object(
            client._client, "transact_write_items", side_effect=error
        ) as txn,
    ):
        with pytest.raises(DynamoDBThroughputError):
            save(client, read(client).revision, context={"new": "context"})
        assert txn.call_count == 4


def test_fifo_count_only_cannot_detect_interleaved_same_count_rewrite(client):
    # Two source rows stand in for fixed nutrition rows. There is ONE writer.
    # Reader gets row 0, writer replaces both, reader gets row 1 and summary.
    # FIFO ordering cannot serialize that reader with the writer.
    line(client, 0, "generation A")
    line(client, 1, "generation A")
    first_page = client._client.query(
        TableName=client.table_name,
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={
            ":pk": {"S": f"IMAGE#{IMAGE}"},
            ":sk": {"S": "RECEIPT#00001#LINE_ITEM#"},
        },
        Limit=1,
        ConsistentRead=True,
    )
    line(client, 0, "generation B")
    line(client, 1, "generation B")
    last_page = client._client.query(
        TableName=client.table_name,
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={
            ":pk": {"S": f"IMAGE#{IMAGE}"},
            ":sk": {"S": "RECEIPT#00001#LINE_ITEM#"},
        },
        ExclusiveStartKey=first_page["LastEvaluatedKey"],
        ConsistentRead=True,
    )
    observed = first_page["Items"] + last_page["Items"]
    assert len(observed) == 2  # The final summary's count would accept it.
    assert {item["name"]["S"] for item in observed} == {
        "generation A",
        "generation B",
    }
