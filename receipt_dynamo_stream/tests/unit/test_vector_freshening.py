"""Unit tests for the vector freshening leg's degradation paths."""

from datetime import datetime
from typing import Any

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo.entities.receipt_place import ReceiptPlace
from receipt_dynamo_stream import FresheningStats, apply_vector_freshening
from receipt_dynamo_stream.vector_freshening import TABLE_ENV_VAR

from .conftest import MockMetrics

_IMAGE_ID = "550e8400-e29b-41d4-a716-446655440000"


def _place(merchant_name: str = "Cafe Nero") -> ReceiptPlace:
    return ReceiptPlace(
        image_id=_IMAGE_ID,
        receipt_id=1,
        place_id="place123",
        merchant_name=merchant_name,
        formatted_address="123 Main St",
        phone_number="555-123-4567",
        matched_fields=["name"],
        validated_by="INFERENCE",
        timestamp=datetime.fromisoformat("2024-01-01T00:00:00"),
    )


def _modify_record(
    old_item: dict[str, Any], new_item: dict[str, Any]
) -> dict[str, Any]:
    return {
        "eventID": "evt-1",
        "eventName": "MODIFY",
        "awsRegion": "us-east-1",
        "dynamodb": {
            "Keys": {"PK": new_item["PK"], "SK": new_item["SK"]},
            "OldImage": old_item,
            "NewImage": new_item,
        },
    }


class _ExplodingClient:
    """Fails the test if any DynamoDB call is made."""

    def query(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("query should not be called")

    def update_item(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("update_item should not be called")


class _ThrottlingClient:
    """Returns one line-embedding SK, then throttles every update."""

    def __init__(self) -> None:
        self.update_attempts = 0

    def query(self, **kwargs: Any) -> dict[str, Any]:
        return {"Items": [{"SK": {"S": "RECEIPT#00001#LINE#00001#EMBEDDING"}}]}

    def update_item(self, **kwargs: Any) -> dict[str, Any]:
        self.update_attempts += 1
        raise ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "slow down",
                }
            },
            "UpdateItem",
        )


class _BrokenQueryClient:
    """Raises an unexpected error from query."""

    def query(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("unexpected failure")

    def update_item(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("update_item should not be called")


def test_no_table_configured_is_inert(
    monkeypatch: pytest.MonkeyPatch, mock_metrics: MockMetrics
) -> None:
    monkeypatch.delenv(TABLE_ENV_VAR, raising=False)
    record = _modify_record(
        _place().to_item(), _place("New Merchant").to_item()
    )

    stats = apply_vector_freshening([record], mock_metrics)

    assert stats == FresheningStats()
    assert ("VectorFresheningNotConfigured", 1, None) in mock_metrics.counts


def test_irrelevant_place_modify_makes_no_calls(
    mock_metrics: MockMetrics,
) -> None:
    """Same merchant_name/place_id: the leg must not touch DynamoDB."""
    record = _modify_record(_place().to_item(), _place().to_item())

    stats = apply_vector_freshening(
        [record],
        mock_metrics,
        dynamo_client=_ExplodingClient(),
        table_name="test-table",
    )

    assert stats.updates_applied == 0
    assert stats.errors == 0


def test_non_freshening_entities_make_no_calls() -> None:
    """RECEIPT_LINE changes are not freshened (no consumer remains)."""
    item = {
        "PK": {"S": f"IMAGE#{_IMAGE_ID}"},
        "SK": {"S": "RECEIPT#00001#LINE#00001"},
        "text": {"S": "COFFEE"},
    }
    record = _modify_record(item, item)

    stats = apply_vector_freshening(
        [record],
        dynamo_client=_ExplodingClient(),
        table_name="test-table",
    )

    assert stats.updates_applied == 0
    assert stats.errors == 0


def test_throttle_skips_and_reports(mock_metrics: MockMetrics) -> None:
    """Throttled updates are counted and skipped, never raised."""
    client = _ThrottlingClient()
    record = _modify_record(
        _place().to_item(), _place("New Merchant").to_item()
    )

    stats = apply_vector_freshening(
        [record], mock_metrics, dynamo_client=client, table_name="test-table"
    )

    assert client.update_attempts == 1
    assert stats.throttled == 1
    assert stats.updates_applied == 0
    assert stats.errors == 0
    throttle_counts = [
        count
        for count in mock_metrics.counts
        if count[0] == "VectorFresheningThrottled"
    ]
    assert throttle_counts


def test_unexpected_error_never_crashes(mock_metrics: MockMetrics) -> None:
    """A RuntimeError inside a record is contained and counted."""
    record = _modify_record(
        _place().to_item(), _place("New Merchant").to_item()
    )

    stats = apply_vector_freshening(
        [record],
        mock_metrics,
        dynamo_client=_BrokenQueryClient(),
        table_name="test-table",
    )

    assert stats.errors == 1
    assert stats.updates_applied == 0
