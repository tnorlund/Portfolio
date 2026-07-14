"""Contract tests for the deprecated batch-result data accessors."""

from datetime import datetime, timezone
from typing import Any, TypeVar
from unittest.mock import MagicMock

import pytest

from receipt_dynamo.constants import BatchStatus, EmbeddingStatus
from receipt_dynamo.data._completion_batch_result import (
    _CompletionBatchResult,
)
from receipt_dynamo.data._embedding_batch_result import _EmbeddingBatchResult
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.completion_batch_result import (
    CompletionBatchResult,
)
from receipt_dynamo.entities.embedding_batch_result import (
    EmbeddingBatchResult,
)

BATCH_ID = "11111111-1111-4111-8111-111111111111"
IMAGE_ID = "22222222-2222-4222-8222-222222222222"
TABLE_NAME = "receipts"
LAST_KEY = {
    "PK": {"S": f"BATCH#{BATCH_ID}"},
    "SK": {"S": "RESULT#one"},
    "TYPE": {"S": "BATCH_RESULT"},
}

AccessorT = TypeVar("AccessorT", _EmbeddingBatchResult, _CompletionBatchResult)


def make_accessor(
    accessor_type: type[AccessorT],
) -> tuple[AccessorT, MagicMock]:
    accessor = accessor_type()
    client = MagicMock()
    accessor.table_name = TABLE_NAME
    accessor._client = client
    return accessor, client


def embedding_result(**overrides: Any) -> EmbeddingBatchResult:
    values = {
        "batch_id": BATCH_ID,
        "image_id": IMAGE_ID,
        "receipt_id": 3,
        "line_id": 4,
        "word_id": 5,
        "status": EmbeddingStatus.SUCCESS,
        "text": "subtotal",
        "error_message": None,
    }
    values.update(overrides)
    values.setdefault(
        "pinecone_id",
        (
            f"IMAGE#{values['image_id']}"
            f"#RECEIPT#{values['receipt_id']:05d}"
            f"#LINE#{values['line_id']:05d}"
            f"#WORD#{values['word_id']:05d}"
        ),
    )
    return EmbeddingBatchResult(**values)  # type: ignore[arg-type]


def completion_result(**overrides: Any) -> CompletionBatchResult:
    values = {
        "batch_id": BATCH_ID,
        "image_id": IMAGE_ID,
        "receipt_id": 3,
        "line_id": 4,
        "word_id": 5,
        "original_label": "SUBTOTAL",
        "gpt_suggested_label": "TOTAL",
        "status": BatchStatus.COMPLETED,
        "validated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return CompletionBatchResult(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("accessor_type", "factory", "add_name", "update_name", "delete_name"),
    [
        (
            _EmbeddingBatchResult,
            embedding_result,
            "add_embedding_batch_result",
            "update_embedding_batch_result",
            "delete_embedding_batch_result",
        ),
        (
            _CompletionBatchResult,
            completion_result,
            "add_completion_batch_result",
            "update_completion_batch_result",
            "delete_completion_batch_result",
        ),
    ],
)
def test_single_crud_uses_full_entity_and_composite_key(
    accessor_type: type[AccessorT],
    factory: Any,
    add_name: str,
    update_name: str,
    delete_name: str,
) -> None:
    accessor, client = make_accessor(accessor_type)
    result = factory()

    getattr(accessor, add_name)(result)
    client.put_item.assert_called_once_with(
        TableName=TABLE_NAME,
        Item=result.to_item(),
        ConditionExpression="attribute_not_exists(PK)",
    )

    client.reset_mock()
    getattr(accessor, update_name)(result)
    client.put_item.assert_called_once_with(
        TableName=TABLE_NAME,
        Item=result.to_item(),
        ConditionExpression="attribute_exists(PK)",
    )

    client.reset_mock()
    getattr(accessor, delete_name)(result)
    client.delete_item.assert_called_once_with(
        TableName=TABLE_NAME,
        Key=result.key,
        ConditionExpression="attribute_exists(PK)",
    )


@pytest.mark.parametrize(
    ("accessor_type", "factory", "method_name"),
    [
        (
            _EmbeddingBatchResult,
            embedding_result,
            "add_embedding_batch_results",
        ),
        (
            _CompletionBatchResult,
            completion_result,
            "add_completion_batch_results",
        ),
    ],
)
def test_batch_add_uses_each_entity_item(
    accessor_type: type[AccessorT],
    factory: Any,
    method_name: str,
) -> None:
    accessor, client = make_accessor(accessor_type)
    results = [factory(), factory(word_id=6)]

    getattr(accessor, method_name)(results)

    client.batch_write_item.assert_called_once_with(
        RequestItems={
            TABLE_NAME: [
                {"PutRequest": {"Item": result.to_item()}}
                for result in results
            ]
        }
    )


def test_embedding_batch_update_and_delete_preserve_conditions() -> None:
    accessor, client = make_accessor(_EmbeddingBatchResult)
    result = embedding_result()

    accessor.update_embedding_batch_results([result])
    client.transact_write_items.assert_called_once_with(
        TransactItems=[
            {
                "Put": {
                    "TableName": TABLE_NAME,
                    "Item": result.to_item(),
                    "ConditionExpression": "attribute_exists(PK)",
                }
            }
        ]
    )

    client.reset_mock()
    accessor.delete_embedding_batch_results([result])
    client.transact_write_items.assert_called_once_with(
        TransactItems=[
            {
                "Delete": {
                    "TableName": TABLE_NAME,
                    "Key": result.key,
                    "ConditionExpression": "attribute_exists(PK)",
                }
            }
        ]
    )


def test_get_embedding_result_uses_entity_primary_key_and_round_trips() -> (
    None
):
    accessor, client = make_accessor(_EmbeddingBatchResult)
    result = embedding_result()
    client.get_item.return_value = {"Item": result.to_item()}

    found = accessor.get_embedding_batch_result(
        BATCH_ID,
        IMAGE_ID,
        3,
        4,
        word_id=5,
    )

    assert found == result
    client.get_item.assert_called_once_with(
        TableName=TABLE_NAME,
        Key=result.key,
    )


def test_get_completion_result_uses_entity_primary_key_and_round_trips() -> (
    None
):
    accessor, client = make_accessor(_CompletionBatchResult)
    result = completion_result()
    client.get_item.return_value = {"Item": result.to_item()}

    found = accessor.get_completion_batch_result(
        BATCH_ID,
        3,
        4,
        5,
        label="SUBTOTAL",
    )

    assert found == result
    client.get_item.assert_called_once_with(
        TableName=TABLE_NAME,
        Key=result.key,
    )


@pytest.mark.parametrize(
    ("accessor_type", "call"),
    [
        (
            _EmbeddingBatchResult,
            lambda accessor: accessor.get_embedding_batch_result(
                BATCH_ID, IMAGE_ID, 3, 4, word_id=5
            ),
        ),
        (
            _CompletionBatchResult,
            lambda accessor: accessor.get_completion_batch_result(
                BATCH_ID, 3, 4, 5, label="SUBTOTAL"
            ),
        ),
    ],
)
def test_get_missing_result_raises_domain_error(
    accessor_type: type[AccessorT], call: Any
) -> None:
    accessor, _ = make_accessor(accessor_type)

    with pytest.raises(EntityNotFoundError):
        call(accessor)


@pytest.mark.parametrize(
    ("accessor_type", "factory", "method_name", "entity_type"),
    [
        (
            _EmbeddingBatchResult,
            embedding_result,
            "list_embedding_batch_results",
            "EMBEDDING_BATCH_RESULT",
        ),
        (
            _CompletionBatchResult,
            completion_result,
            "list_completion_batch_results",
            "COMPLETION_BATCH_RESULT",
        ),
    ],
)
def test_list_by_type_round_trips_and_forwards_pagination(
    accessor_type: type[AccessorT],
    factory: Any,
    method_name: str,
    entity_type: str,
) -> None:
    accessor, client = make_accessor(accessor_type)
    result = factory()
    client.query.return_value = {"Items": [result.to_item()]}

    found, next_key = getattr(accessor, method_name)(
        last_evaluated_key=LAST_KEY
    )

    assert found == [result]
    assert next_key is None
    client.query.assert_called_once_with(
        TableName=TABLE_NAME,
        IndexName="GSITYPE",
        KeyConditionExpression="#type_attr = :type_value",
        ExpressionAttributeNames={"#type_attr": "TYPE"},
        ExpressionAttributeValues={":type_value": {"S": entity_type}},
        ExclusiveStartKey=LAST_KEY,
    )


def test_embedding_status_query_uses_complete_gsi2_key() -> None:
    accessor, client = make_accessor(_EmbeddingBatchResult)
    result = embedding_result()
    client.query.return_value = {"Items": [result.to_item()]}

    found, next_key = accessor.get_embedding_batch_results_by_status(
        BATCH_ID, EmbeddingStatus.SUCCESS
    )

    assert found == [result]
    assert next_key is None
    client.query.assert_called_once_with(
        TableName=TABLE_NAME,
        IndexName="GSI2",
        KeyConditionExpression="GSI2PK = :pk AND GSI2SK = :sk",
        ExpressionAttributeValues={
            ":pk": {"S": f"BATCH#{BATCH_ID}"},
            ":sk": {"S": "STATUS#SUCCESS"},
        },
    )


def test_status_query_limit_returns_a_reusable_complete_pagination_key() -> (
    None
):
    accessor, client = make_accessor(_EmbeddingBatchResult)
    first = embedding_result()
    second = embedding_result(word_id=6)
    client.query.return_value = {"Items": [first.to_item(), second.to_item()]}

    found, next_key = accessor.get_embedding_batch_results_by_status(
        BATCH_ID, EmbeddingStatus.SUCCESS.value, limit=1
    )

    assert found == [first]
    assert next_key == {
        "PK": first.key["PK"],
        "SK": first.key["SK"],
        **first.gsi2_key,
    }
    assert client.query.call_args.kwargs["Limit"] == 2


def test_completion_status_query_uses_batch_status_and_complete_gsi2_key() -> (
    None
):
    accessor, client = make_accessor(_CompletionBatchResult)
    result = completion_result()
    client.query.return_value = {"Items": [result.to_item()]}

    found, next_key = accessor.get_completion_batch_results_by_status(
        BATCH_ID, BatchStatus.COMPLETED
    )

    assert found == [result]
    assert next_key is None
    client.query.assert_called_once_with(
        TableName=TABLE_NAME,
        IndexName="GSI2",
        KeyConditionExpression="GSI2PK = :pk AND GSI2SK = :sk",
        ExpressionAttributeValues={
            ":pk": {"S": f"BATCH#{BATCH_ID}"},
            ":sk": {"S": "STATUS#COMPLETED"},
        },
    )


@pytest.mark.parametrize(
    ("accessor_type", "factory", "method_name"),
    [
        (
            _EmbeddingBatchResult,
            embedding_result,
            "get_embedding_batch_results_by_receipt",
        ),
        (
            _CompletionBatchResult,
            completion_result,
            "get_completion_batch_results_by_receipt",
        ),
    ],
)
def test_receipt_query_uses_image_and_padded_receipt_gsi3_partition(
    accessor_type: type[AccessorT],
    factory: Any,
    method_name: str,
) -> None:
    accessor, client = make_accessor(accessor_type)
    result = factory()
    client.query.return_value = {"Items": [result.to_item()]}

    found, next_key = getattr(accessor, method_name)(IMAGE_ID, 3)

    assert found == [result]
    assert next_key is None
    client.query.assert_called_once_with(
        TableName=TABLE_NAME,
        IndexName="GSI3",
        KeyConditionExpression="GSI3PK = :pk",
        ExpressionAttributeValues={
            ":pk": {"S": f"IMAGE#{IMAGE_ID}#RECEIPT#00003"}
        },
    )


def test_completion_label_query_matches_entity_gsi1_key() -> None:
    accessor, client = make_accessor(_CompletionBatchResult)
    result = completion_result()
    client.query.return_value = {"Items": [result.to_item()]}

    found, next_key = accessor.get_completion_batch_results_by_label_target(
        "SUBTOTAL"
    )

    assert found == [result]
    assert next_key is None
    client.query.assert_called_once_with(
        TableName=TABLE_NAME,
        IndexName="GSI1",
        KeyConditionExpression="GSI1PK = :pk",
        ExpressionAttributeValues={":pk": {"S": "LABEL#SUBTOTAL"}},
    )


@pytest.mark.parametrize(
    "bad_status",
    ["", "VALID", "SUCCESS", None],
)
def test_completion_status_query_rejects_non_batch_statuses(
    bad_status: Any,
) -> None:
    accessor, client = make_accessor(_CompletionBatchResult)

    with pytest.raises(EntityValidationError):
        accessor.get_completion_batch_results_by_status(BATCH_ID, bad_status)
    client.query.assert_not_called()


@pytest.mark.parametrize("bad_status", ["", "UNKNOWN", None, 1])
def test_embedding_status_query_rejects_unknown_statuses(
    bad_status: Any,
) -> None:
    accessor, client = make_accessor(_EmbeddingBatchResult)

    with pytest.raises(EntityValidationError):
        accessor.get_embedding_batch_results_by_status(BATCH_ID, bad_status)
    client.query.assert_not_called()


@pytest.mark.parametrize("bad_uuid", ["", "not-a-uuid", None, 1])
def test_batch_and_image_uuid_errors_use_validation_contract(
    bad_uuid: Any,
) -> None:
    embedding_accessor, embedding_client = make_accessor(_EmbeddingBatchResult)
    completion_accessor, completion_client = make_accessor(
        _CompletionBatchResult
    )

    with pytest.raises(EntityValidationError, match="batch_id"):
        embedding_accessor.get_embedding_batch_results_by_status(
            bad_uuid, EmbeddingStatus.SUCCESS.value
        )
    with pytest.raises(EntityValidationError, match="batch_id"):
        completion_accessor.get_completion_batch_results_by_status(
            bad_uuid, BatchStatus.COMPLETED.value
        )
    with pytest.raises(EntityValidationError, match="image_id"):
        embedding_accessor.get_embedding_batch_results_by_receipt(bad_uuid, 3)
    with pytest.raises(EntityValidationError, match="image_id"):
        completion_accessor.get_completion_batch_results_by_receipt(
            bad_uuid, 3
        )
    embedding_client.query.assert_not_called()
    completion_client.query.assert_not_called()


@pytest.mark.parametrize(
    ("line_id", "word_id"),
    [(True, 5), (-1, 5), (4, True), (4, -1), ("4", 5), (4, "5")],
)
def test_get_result_rejects_invalid_line_and_word_ids(
    line_id: Any, word_id: Any
) -> None:
    embedding_accessor, embedding_client = make_accessor(_EmbeddingBatchResult)
    completion_accessor, completion_client = make_accessor(
        _CompletionBatchResult
    )

    with pytest.raises(EntityValidationError):
        embedding_accessor.get_embedding_batch_result(
            BATCH_ID, IMAGE_ID, 3, line_id, word_id=word_id
        )
    with pytest.raises(EntityValidationError):
        completion_accessor.get_completion_batch_result(
            BATCH_ID, 3, line_id, word_id, label="SUBTOTAL"
        )
    embedding_client.get_item.assert_not_called()
    completion_client.get_item.assert_not_called()


@pytest.mark.parametrize("bad_value", [True, 0, -1, "1", None])
def test_result_ids_reject_bool_and_invalid_values(bad_value: Any) -> None:
    embedding_accessor, embedding_client = make_accessor(_EmbeddingBatchResult)
    completion_accessor, completion_client = make_accessor(
        _CompletionBatchResult
    )

    with pytest.raises(EntityValidationError):
        embedding_accessor.get_embedding_batch_result(
            BATCH_ID, IMAGE_ID, bad_value, 4, word_id=5
        )
    with pytest.raises(EntityValidationError):
        completion_accessor.get_completion_batch_result(
            BATCH_ID, bad_value, 4, 5, label="SUBTOTAL"
        )
    embedding_client.get_item.assert_not_called()
    completion_client.get_item.assert_not_called()


@pytest.mark.parametrize("bad_receipt_id", [True, 0, -1, "1", None])
def test_receipt_queries_reject_invalid_receipt_ids(
    bad_receipt_id: Any,
) -> None:
    embedding_accessor, embedding_client = make_accessor(_EmbeddingBatchResult)
    completion_accessor, completion_client = make_accessor(
        _CompletionBatchResult
    )

    with pytest.raises(EntityValidationError):
        embedding_accessor.get_embedding_batch_results_by_receipt(
            IMAGE_ID, bad_receipt_id
        )
    with pytest.raises(EntityValidationError):
        completion_accessor.get_completion_batch_results_by_receipt(
            IMAGE_ID, bad_receipt_id
        )
    embedding_client.query.assert_not_called()
    completion_client.query.assert_not_called()


@pytest.mark.parametrize("bad_results", [None, "not-a-list", [object()]])
def test_embedding_batch_update_rejects_invalid_collections(
    bad_results: Any,
) -> None:
    accessor, client = make_accessor(_EmbeddingBatchResult)

    with pytest.raises(EntityValidationError):
        accessor.update_embedding_batch_results(bad_results)
    client.transact_write_items.assert_not_called()


@pytest.mark.parametrize("bad_label", ["", "BAD#LABEL", 1, None])
def test_completion_label_contract_rejects_malformed_values(
    bad_label: Any,
) -> None:
    accessor, client = make_accessor(_CompletionBatchResult)

    with pytest.raises(EntityValidationError):
        accessor.get_completion_batch_results_by_label_target(bad_label)
    client.query.assert_not_called()


@pytest.mark.parametrize("limit", [True, 0, -1, 1.5])
def test_all_query_families_reject_invalid_limits(limit: Any) -> None:
    embedding_accessor, embedding_client = make_accessor(_EmbeddingBatchResult)
    completion_accessor, completion_client = make_accessor(
        _CompletionBatchResult
    )

    with pytest.raises(EntityValidationError):
        embedding_accessor.list_embedding_batch_results(limit=limit)
    with pytest.raises(EntityValidationError):
        completion_accessor.list_completion_batch_results(limit=limit)
    embedding_client.query.assert_not_called()
    completion_client.query.assert_not_called()


@pytest.mark.parametrize("last_key", [{}, {"PK": {"S": "only"}}])
def test_all_query_families_reject_malformed_pagination_keys(
    last_key: dict[str, Any],
) -> None:
    embedding_accessor, embedding_client = make_accessor(_EmbeddingBatchResult)
    completion_accessor, completion_client = make_accessor(
        _CompletionBatchResult
    )

    with pytest.raises(EntityValidationError):
        embedding_accessor.get_embedding_batch_results_by_receipt(
            IMAGE_ID, 3, last_evaluated_key=last_key
        )
    with pytest.raises(EntityValidationError):
        completion_accessor.get_completion_batch_results_by_receipt(
            IMAGE_ID, 3, last_evaluated_key=last_key
        )
    embedding_client.query.assert_not_called()
    completion_client.query.assert_not_called()


def test_gsi_queries_reject_tokens_missing_index_keys() -> None:
    embedding_accessor, embedding_client = make_accessor(_EmbeddingBatchResult)
    completion_accessor, completion_client = make_accessor(
        _CompletionBatchResult
    )
    table_key_only = {key: LAST_KEY[key] for key in ("PK", "SK")}

    with pytest.raises(EntityValidationError, match="GSI2PK"):
        embedding_accessor.get_embedding_batch_results_by_status(
            BATCH_ID,
            EmbeddingStatus.SUCCESS.value,
            last_evaluated_key=table_key_only,
        )
    with pytest.raises(EntityValidationError, match="GSI1PK"):
        completion_accessor.get_completion_batch_results_by_label_target(
            "SUBTOTAL", last_evaluated_key=table_key_only
        )
    embedding_client.query.assert_not_called()
    completion_client.query.assert_not_called()
