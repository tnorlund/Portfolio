"""Offline transactional reservation, lease, and recovery contracts."""

from dataclasses import replace
from typing import Any, Literal
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient, Image, Receipt
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityValidationError,
    OperationError,
    ReceiptDynamoError,
)
from receipt_dynamo.entities.receipt_merge import ReceiptMerge

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
pytestmark = pytest.mark.integration


def _receipt(receipt_id: int) -> Receipt:
    return Receipt(
        image_id=IMAGE_ID,
        receipt_id=receipt_id,
        width=10,
        height=20,
        timestamp_added="2026-09-10T00:00:00+00:00",
        raw_s3_bucket="offline-merge",
        raw_s3_key=f"{receipt_id}.png",
        top_left={"x": 0, "y": 1},
        top_right={"x": 1, "y": 1},
        bottom_left={"x": 0, "y": 0},
        bottom_right={"x": 1, "y": 0},
    )


@pytest.fixture(name="client")
def _client(dynamodb_table: Literal["MyMockedTable"]) -> DynamoClient:
    client = DynamoClient(dynamodb_table)
    for receipt_id in range(1, 5):
        client.add_receipt(_receipt(receipt_id))
    return client


def test_claim_normalizes_sources_and_rejects_active_duplicate(
    client: DynamoClient,
) -> None:
    operation = client.claim_receipt_merge(IMAGE_ID, [2, 1], "first")
    assert operation.source_ids == (1, 2)
    assert operation.output_id == 5
    assert client.get_receipt_merge(IMAGE_ID, [1, 2]) == operation
    with pytest.raises(OperationError, match="already in progress"):
        client.claim_receipt_merge(IMAGE_ID, [1, 2], "second")
    assert client.get_receipt_merge(IMAGE_ID, [1, 2]) == operation


def test_release_and_expiry_preserve_output_and_fence_old_owner(
    client: DynamoClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Mock(return_value=1000)
    monkeypatch.setattr("receipt_dynamo.data._receipt_merge.time.time", clock)
    first = client.claim_receipt_merge(IMAGE_ID, [1, 2], "first")
    assert first.lease_until == 1900
    clock.return_value = 1901
    second = client.claim_receipt_merge(IMAGE_ID, [1, 2], "second")
    assert second.output_id == first.output_id
    with pytest.raises(OperationError, match="expired or changed"):
        client.assert_receipt_merge_owner(first)
    with pytest.raises(ReceiptDynamoError):
        client.put_receipt_merge_output(first, _receipt(first.output_id))
    with pytest.raises(ReceiptDynamoError):
        client.checkpoint_receipt_merge(first, replace(first, status="READY"))
    client.release_receipt_merge(first)
    assert client.get_receipt_merge(IMAGE_ID, [1, 2]) == second
    client.release_receipt_merge(second)
    third = client.claim_receipt_merge(IMAGE_ID, [2, 1], "third")
    assert third.output_id == first.output_id
    client.put_receipt_merge_output(third, _receipt(third.output_id))


def test_completed_redelivery_retains_result_after_sources_deleted(
    client: DynamoClient,
) -> None:
    operation = client.claim_receipt_merge(IMAGE_ID, [1, 2], "first")
    ready = client.checkpoint_receipt_merge(
        operation,
        replace(
            operation,
            status="READY",
            source_assets={"1": [["bucket", "key"]], "2": []},
        ),
    )
    for rid in (1, 2):
        client.delete_receipt(_receipt(rid))
    result = {"new_receipt_id": operation.output_id, "status": "success"}
    done = client.checkpoint_receipt_merge(
        ready, replace(ready, status="COMPLETED", result=result)
    )
    replay = client.claim_receipt_merge(IMAGE_ID, [2, 1], "retry")
    assert replay == done
    assert replay.result == result
    # Completion and image-lease release are one transaction.
    assert client.claim_receipt_merge(IMAGE_ID, [3, 4], "next").output_id == 6


def test_expired_owner_cannot_overwrite_another_merges_image_count(
    client: DynamoClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Mock(return_value=1000)
    monkeypatch.setattr("receipt_dynamo.data._receipt_merge.time.time", clock)
    image = Image(
        image_id=IMAGE_ID,
        width=10,
        height=20,
        timestamp_added="2026-09-10T00:00:00+00:00",
        raw_s3_bucket="offline-merge",
        raw_s3_key="original.png",
        receipt_count=4,
    )
    client.add_image(image)
    first = client.claim_receipt_merge(IMAGE_ID, [1, 2], "first")
    first = client.checkpoint_receipt_merge(
        first, replace(first, status="READY")
    )
    clock.return_value = 1901
    second = client.claim_receipt_merge(IMAGE_ID, [3, 4], "second")
    second = client.checkpoint_receipt_merge(
        second, replace(second, status="READY")
    )
    client.update_receipt_merge_image(second, replace(image, receipt_count=2))
    with pytest.raises(ReceiptDynamoError):
        client.update_receipt_merge_image(
            first, replace(image, receipt_count=3)
        )
    assert client.get_image(IMAGE_ID).receipt_count == 2
    client.release_receipt_merge(first)
    client.assert_receipt_merge_owner(second)


def test_overlapping_source_pairs_are_reserved_atomically(
    client: DynamoClient,
) -> None:
    first = client.claim_receipt_merge(IMAGE_ID, [1, 2], "first")
    with pytest.raises(OperationError, match="already reserved"):
        client.claim_receipt_merge(IMAGE_ID, [2, 3], "overlap")
    assert client.get_receipt_merge(IMAGE_ID, [2, 3]) is None
    with pytest.raises(OperationError, match="image is busy"):
        client.claim_receipt_merge(IMAGE_ID, [3, 4], "disjoint")
    client.release_receipt_merge(first)
    disjoint = client.claim_receipt_merge(IMAGE_ID, [3, 4], "disjoint")
    assert disjoint.output_id == first.output_id + 1


def test_concurrent_disjoint_reservation_retries_next_id(
    client: DynamoClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    reserve = getattr(client, "_reserve_merge")
    entered = False

    def race(operation: ReceiptMerge) -> None:
        nonlocal entered
        if not entered:
            entered = True
            other = client.claim_receipt_merge(IMAGE_ID, [3, 4], "other")
            assert other.output_id == operation.output_id
            client.release_receipt_merge(other)
        reserve(operation)

    monkeypatch.setattr(client, "_reserve_merge", race)
    operation = client.claim_receipt_merge(IMAGE_ID, [1, 2], "first")
    assert operation.output_id == 6
    assert client.get_receipt_merge(IMAGE_ID, [3, 4]).output_id == 5


def test_unrelated_producer_cannot_be_overwritten_after_reservation(
    client: DynamoClient,
) -> None:
    operation = client.claim_receipt_merge(IMAGE_ID, [1, 2], "first")
    unrelated = replace(_receipt(operation.output_id), raw_s3_key="other.png")
    client.add_receipt(unrelated)
    with pytest.raises(ReceiptDynamoError):
        client.put_receipt_merge_output(
            operation, _receipt(operation.output_id)
        )
    assert client.get_receipt(IMAGE_ID, operation.output_id) == unrelated
    assert all(
        client.receipt_exists_consistent(IMAGE_ID, rid) for rid in (1, 2)
    )


def test_output_rewrite_requires_its_own_operation_marker(
    client: DynamoClient,
) -> None:
    operation = client.claim_receipt_merge(IMAGE_ID, [1, 2], "first")
    output = _receipt(operation.output_id)
    client.put_receipt_merge_output(operation, output)
    updated = replace(output, raw_s3_key="rewritten.png")
    client.put_receipt_merge_output(operation, updated)
    assert client.get_receipt(IMAGE_ID, operation.output_id) == updated
    with pytest.raises(EntityValidationError, match="match a preparing"):
        client.put_receipt_merge_output(operation, _receipt(1))


@pytest.mark.parametrize(
    "source_ids", [None, [], [1], [1, 1], [0, 1], [True, 2]]
)
def test_invalid_source_ids_do_not_write(
    client: DynamoClient, source_ids: Any
) -> None:
    with pytest.raises(EntityValidationError):
        client.claim_receipt_merge(IMAGE_ID, source_ids, "first")


def test_missing_sources_do_not_leave_reservations(
    client: DynamoClient,
) -> None:
    with pytest.raises(OperationError, match="sources are missing"):
        client.claim_receipt_merge(IMAGE_ID, [1, 99], "first")
    assert client.get_receipt_merge(IMAGE_ID, [1, 99]) is None
    assert client.claim_receipt_merge(IMAGE_ID, [1, 2], "valid").output_id == 5


def test_ids_remain_reserved_when_output_is_deleted(
    client: DynamoClient,
) -> None:
    first = client.claim_receipt_merge(IMAGE_ID, [1, 2], "first")
    client.put_receipt_merge_output(first, _receipt(first.output_id))
    client.delete_receipt(_receipt(first.output_id))
    client.release_receipt_merge(first)
    second = client.claim_receipt_merge(IMAGE_ID, [3, 4], "second")
    assert second.output_id == first.output_id + 1


ERROR_CASES = [
    ("ValidationException", EntityValidationError),
    ("ResourceNotFoundException", OperationError),
    ("ProvisionedThroughputExceededException", DynamoDBThroughputError),
    ("InternalServerError", DynamoDBServerError),
    ("AccessDeniedException", DynamoDBError),
]


@pytest.mark.parametrize("code,exception_type", ERROR_CASES)
@pytest.mark.parametrize(
    "operation", ["get", "claim", "put", "checkpoint", "release"]
)
def test_operations_map_infrastructure_errors(
    client: DynamoClient,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    code: str,
    exception_type: type[Exception],
) -> None:
    journal = client.claim_receipt_merge(IMAGE_ID, [1, 2], "first")
    method = {
        "get": "get_item",
        "claim": "get_item",
        "put": "transact_write_items",
        "checkpoint": "transact_write_items",
        "release": "transact_write_items",
    }[operation]
    monkeypatch.setattr(
        getattr(client, "_client"),
        method,
        Mock(side_effect=ClientError({"Error": {"Code": code}}, method)),
    )
    with pytest.raises(exception_type):
        if operation == "get":
            client.get_receipt_merge(IMAGE_ID, [1, 2])
        elif operation == "claim":
            client.claim_receipt_merge(IMAGE_ID, [1, 2], "second")
        elif operation == "put":
            client.put_receipt_merge_output(
                journal, _receipt(journal.output_id)
            )
        elif operation == "checkpoint":
            client.checkpoint_receipt_merge(
                journal, replace(journal, status="READY")
            )
        else:
            client.release_receipt_merge(journal)
