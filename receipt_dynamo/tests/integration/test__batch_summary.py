from datetime import datetime
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import BatchStatus, BatchType
from receipt_dynamo.entities.batch_summary import BatchSummary


@pytest.fixture
def sample_batch_summary():
    return BatchSummary(
        batch_id=str(uuid4()),
        batch_type=BatchType.EMBEDDING.value,
        openai_batch_id="openai-xyz",
        submitted_at=datetime(2024, 1, 1, 12, 0, 0),
        status=BatchStatus.PENDING.value,
        result_file_id="file-456",
        receipt_refs=[(str(uuid4()), 101)],
    )


def test_addBatchSummary_duplicate_raises(
    dynamodb_table, sample_batch_summary, mocker
):
    client = DynamoClient(dynamodb_table)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "Exists",
                }
            },
            "PutItem",
        ),
    )
    with pytest.raises(ValueError, match="already exists"):
        client.add_batch_summary(sample_batch_summary)


@pytest.mark.parametrize("invalid", [None, "not-a-batch"])
def test_addBatchSummary_invalid_param(dynamodb_table, invalid):
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError):
        client.add_batch_summary(invalid)


def test_addBatchSummaries_success(dynamodb_table, sample_batch_summary):
    client = DynamoClient(dynamodb_table)
    summaries = [sample_batch_summary]
    client.add_batch_summaries(summaries)


def test_addBatchSummaries_unprocessed_retry(
    dynamodb_table, sample_batch_summary, mocker
):
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(client._client, "batch_write_item")
    mock_batch.side_effect = [
        {
            "UnprocessedItems": {
                client.table_name: [
                    {"PutRequest": {"Item": sample_batch_summary.to_item()}}
                ]
            }
        },
        {},  # second call success
    ]
    client.add_batch_summaries([sample_batch_summary])
    assert mock_batch.call_count == 2


def test_updateBatchSummaries_chunked(
    dynamodb_table, sample_batch_summary, mocker
):
    client = DynamoClient(dynamodb_table)
    summaries = [
        BatchSummary(
            **{**dict(sample_batch_summary), "batch_id": str(uuid4())}
        )
        for i in range(30)
    ]
    for item in summaries:
        client.add_batch_summary(item)
    mock_write = mocker.patch.object(
        client._client, "transact_write_items", return_value={}
    )
    client.update_batch_summaries(summaries)
    assert mock_write.call_count == 2


def test_deleteBatchSummaries_chunked(
    dynamodb_table, sample_batch_summary, mocker
):
    client = DynamoClient(dynamodb_table)
    summaries = [
        BatchSummary(
            **{**dict(sample_batch_summary), "batch_id": str(uuid4())}
        )
        for i in range(30)
    ]
    for item in summaries:
        client.add_batch_summary(item)
    mock_write = mocker.patch.object(
        client._client, "transact_write_items", return_value={}
    )
    client.delete_batch_summaries(summaries)
    assert mock_write.call_count == 2


def test_listBatchSummaries_with_limit_and_LEK(
    dynamodb_table, sample_batch_summary
):
    client = DynamoClient(dynamodb_table)
    for i in range(3):
        summary = BatchSummary(
            **{**dict(sample_batch_summary), "batch_id": str(uuid4())}
        )
        client.add_batch_summary(summary)
    first_page, lek = client.list_batch_summaries(limit=1)
    assert len(first_page) == 1
    assert lek is not None
    second_page, lek2 = client.list_batch_summaries(
        limit=1, last_evaluated_key=lek
    )
    assert len(second_page) == 1


def test_getBatchSummariesByStatus_limit_triggers_mid_loop(
    dynamodb_table, sample_batch_summary, mocker
):
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(client._client, "query")
    item = sample_batch_summary.to_item()
    mock_query.side_effect = [
        {
            "Items": [item],
            "LastEvaluatedKey": {"PK": {"S": "p1"}, "SK": {"S": "s1"}},
        },
        {
            "Items": [item],
            "LastEvaluatedKey": {"PK": {"S": "p2"}, "SK": {"S": "s2"}},
        },
        {"Items": [item]},
    ]
    results, lek = client.get_batch_summaries_by_status("PENDING", limit=3)
    assert len(results) == 3
    assert lek is None
    assert mock_query.call_count == 3
