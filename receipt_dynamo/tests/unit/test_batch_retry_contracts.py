"""Partial DynamoDB batches must finish or report bounded retry exhaustion."""

from typing import Any
from unittest.mock import Mock, call

import pytest

from receipt_dynamo import DynamoClient, ReceiptWordLabel
from receipt_dynamo.data.shared_exceptions import BatchOperationError

IMAGE_ID = "344f4a1b-1476-442e-bb01-7eed30934285"
TABLE_NAME = "MyMockedTable"


@pytest.fixture(name="batch_client")
def _batch_client(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[DynamoClient, Mock, Mock]:
    backend = Mock()
    sleep = Mock()
    monkeypatch.setattr(
        "receipt_dynamo.data.dynamo_client.boto3.client",
        Mock(return_value=backend),
    )
    monkeypatch.setattr("time.sleep", sleep)
    return DynamoClient(TABLE_NAME), backend, sleep


def _key(word_id: int) -> dict[str, dict[str, str]]:
    return {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": f"RECEIPT#00001#LINE#00001#WORD#{word_id:05d}"},
    }


def _label(word_id: int) -> ReceiptWordLabel:
    return ReceiptWordLabel(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=1,
        word_id=word_id,
        label="PRODUCT_NAME",
        reasoning="Product text",
        timestamp_added="2026-09-10T12:00:00+00:00",
    )


@pytest.mark.parametrize("completed_chunks", [0, 1])
def test_batch_delete_stops_retrying_and_reports_every_pending_write(
    batch_client: tuple[DynamoClient, Mock, Mock],
    completed_chunks: int,
) -> None:
    client, backend, sleep = batch_client
    start = completed_chunks * 25
    keys = [_key(word_id) for word_id in range(1, start + 31)]
    writes = [{"DeleteRequest": {"Key": key}} for key in keys]
    backend.query.return_value = {"Items": keys}
    failed = {TABLE_NAME: writes[start : start + 1]}
    # A sentinel makes an unbounded implementation fail instead of hanging.
    backend.batch_write_item.side_effect = [
        *[{"UnprocessedItems": {}}] * completed_chunks,
        *[{"UnprocessedItems": failed}] * 4,
        AssertionError("batch write exceeded its retry budget"),
    ]

    with pytest.raises(BatchOperationError) as raised:
        client.delete_receipt_items(IMAGE_ID, 1)

    assert raised.value.attempts == 4
    assert raised.value.unprocessed_items == {
        TABLE_NAME: writes[start : start + 1] + writes[start + 25 :]
    }
    assert backend.batch_write_item.call_count == 4 + completed_chunks
    assert sleep.call_args_list == [call(0.1), call(0.2), call(0.4)]


def test_batch_delete_retries_only_failed_writes_before_next_chunk(
    batch_client: tuple[DynamoClient, Mock, Mock],
) -> None:
    client, backend, sleep = batch_client
    keys = [_key(word_id) for word_id in range(1, 31)]
    writes = [{"DeleteRequest": {"Key": key}} for key in keys]
    backend.query.return_value = {"Items": keys}
    backend.batch_write_item.side_effect = [
        {"UnprocessedItems": {TABLE_NAME: writes[1:3]}},
        {"UnprocessedItems": {TABLE_NAME: writes[2:3]}},
        {"UnprocessedItems": {}},
        {"UnprocessedItems": {}},
    ]

    assert client.delete_receipt_items(IMAGE_ID, 1) == 30

    assert backend.batch_write_item.call_args_list == [
        call(RequestItems={TABLE_NAME: writes[:25]}),
        call(RequestItems={TABLE_NAME: writes[1:3]}),
        call(RequestItems={TABLE_NAME: writes[2:3]}),
        call(RequestItems={TABLE_NAME: writes[25:]}),
    ]
    assert sleep.call_args_list == [call(0.1), call(0.2)]


def test_empty_unprocessed_write_list_finishes_without_retry(
    batch_client: tuple[DynamoClient, Mock, Mock],
) -> None:
    client, backend, sleep = batch_client
    backend.query.return_value = {"Items": [_key(1)]}
    backend.batch_write_item.side_effect = [
        {"UnprocessedItems": {TABLE_NAME: []}},
        AssertionError("completed batch was retried"),
    ]

    assert client.delete_receipt_items(IMAGE_ID, 1) == 1

    backend.batch_write_item.assert_called_once()
    sleep.assert_not_called()


def test_batch_get_stops_retrying_without_returning_partial_results(
    batch_client: tuple[DynamoClient, Mock, Mock],
) -> None:
    client, backend, sleep = batch_client
    labels = [_label(word_id) for word_id in range(1, 111)]
    keys = [label.key for label in labels]
    failed = {TABLE_NAME: {"Keys": keys[1:100]}}
    backend.batch_get_item.side_effect = [
        {
            "Responses": {TABLE_NAME: [labels[0].to_item()]},
            "UnprocessedKeys": failed,
        },
        *[{"UnprocessedKeys": failed}] * 3,
        AssertionError("batch get exceeded its retry budget"),
    ]

    with pytest.raises(BatchOperationError) as raised:
        client.get_receipt_word_labels(
            [(IMAGE_ID, 1, 1, label.word_id, label.label) for label in labels]
        )

    assert raised.value.attempts == 4
    assert raised.value.unprocessed_items == {TABLE_NAME: {"Keys": keys[1:]}}
    assert backend.batch_get_item.call_count == 4
    assert sleep.call_args_list == [call(0.1), call(0.2), call(0.4)]


def test_batch_get_preserves_results_across_retries_and_chunks(
    batch_client: tuple[DynamoClient, Mock, Mock],
) -> None:
    client, backend, sleep = batch_client
    labels = [_label(word_id) for word_id in range(1, 106)]
    items = [label.to_item() for label in labels]
    keys = [label.key for label in labels]
    failed: dict[str, Any] = {
        TABLE_NAME: {"Keys": keys[1:3], "ConsistentRead": True}
    }
    backend.batch_get_item.side_effect = [
        {
            "Responses": {TABLE_NAME: items[:1] + items[3:100]},
            "UnprocessedKeys": failed,
        },
        {
            "Responses": {TABLE_NAME: items[1:3]},
            "UnprocessedKeys": {},
        },
        {"Responses": {TABLE_NAME: items[100:]}},
    ]

    result = client.get_receipt_word_labels(
        [(IMAGE_ID, 1, 1, label.word_id, label.label) for label in labels]
    )

    assert sorted(result, key=lambda label: label.word_id) == labels
    assert backend.batch_get_item.call_args_list == [
        call(RequestItems={TABLE_NAME: {"Keys": keys[:100]}}),
        call(RequestItems=failed),
        call(RequestItems={TABLE_NAME: {"Keys": keys[100:]}}),
    ]
    assert sleep.call_args_list == [call(0.1)]


def test_empty_batches_do_not_call_dynamodb(
    batch_client: tuple[DynamoClient, Mock, Mock],
) -> None:
    client, backend, sleep = batch_client

    client.add_receipts([])
    assert client.get_receipt_word_labels([]) == []

    backend.batch_write_item.assert_not_called()
    backend.batch_get_item.assert_not_called()
    sleep.assert_not_called()
