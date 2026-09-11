"""Fenced correction claims use real conditional writes under moto."""

from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from unittest.mock import patch
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBThroughputError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)
from receipt_dynamo.entities import OCRRoutingDecision

pytestmark = pytest.mark.integration


@pytest.fixture(name="routing")
def _routing(
    dynamodb_table: Literal["MyMockedTable"],
) -> tuple[DynamoClient, OCRRoutingDecision]:
    """Create one pending routing decision in an offline DynamoDB table."""
    client = DynamoClient(dynamodb_table, region="us-east-1")
    decision = OCRRoutingDecision(
        image_id=str(uuid4()),
        job_id=str(uuid4()),
        s3_bucket="mock-bucket",
        s3_key="result.json",
        created_at=datetime.now(timezone.utc),
        updated_at=None,
        receipt_count=0,
    )
    client.add_ocr_routing_decision(decision)
    return client, decision


def test_only_owner_can_complete_and_completion_cannot_be_downgraded(
    routing: tuple[DynamoClient, OCRRoutingDecision],
) -> None:
    """Conditional writes fence competing completion and failure paths."""
    client, decision = routing
    now = datetime.now(timezone.utc)
    assert (
        client.claim_ocr_routing_decision(
            decision.image_id, decision.job_id, "first", now=now
        )
        == "claimed"
    )
    assert (
        client.claim_ocr_routing_decision(
            decision.image_id, decision.job_id, "second", now=now
        )
        == "busy"
    )
    decision.status = "COMPLETED"
    decision.receipt_count = 1
    decision.updated_at = now
    with pytest.raises(OperationError, match="no longer owned"):
        client.complete_ocr_routing_decision(decision, "second")
    assert (
        client.release_ocr_routing_decision(
            decision.image_id, decision.job_id, "second"
        )
        is False
    )
    client.complete_ocr_routing_decision(decision, "first")
    assert (
        client.release_ocr_routing_decision(
            decision.image_id, decision.job_id, "first"
        )
        is False
    )
    assert (
        client.claim_ocr_routing_decision(
            decision.image_id, decision.job_id, "second", now=now
        )
        == "completed"
    )
    assert (
        client.get_ocr_routing_decision(
            decision.image_id, decision.job_id
        ).status
        == "COMPLETED"
    )


def test_expired_attempt_cannot_complete_or_release_new_owner(
    routing: tuple[DynamoClient, OCRRoutingDecision],
) -> None:
    """Takeover invalidates all status writes from the previous owner."""
    client, decision = routing
    now = datetime.now(timezone.utc)
    assert (
        client.claim_ocr_routing_decision(
            decision.image_id,
            decision.job_id,
            "old",
            now=now - timedelta(seconds=1000),
        )
        == "claimed"
    )
    assert (
        client.claim_ocr_routing_decision(
            decision.image_id, decision.job_id, "new", now=now
        )
        == "claimed"
    )
    decision.status = "COMPLETED"
    decision.updated_at = now
    with pytest.raises(OperationError):
        client.complete_ocr_routing_decision(decision, "old")
    assert (
        client.release_ocr_routing_decision(
            decision.image_id, decision.job_id, "old"
        )
        is False
    )
    client.complete_ocr_routing_decision(decision, "new")


def test_expired_owner_cannot_complete_without_a_takeover(
    routing: tuple[DynamoClient, OCRRoutingDecision],
) -> None:
    """Expiry alone prevents an invocation from publishing completion."""
    client, decision = routing
    now = datetime.now(timezone.utc)
    client.claim_ocr_routing_decision(
        decision.image_id,
        decision.job_id,
        "old",
        now=now - timedelta(seconds=1000),
    )
    decision.status = "COMPLETED"
    decision.updated_at = now
    with pytest.raises(OperationError):
        client.complete_ocr_routing_decision(decision, "old")


def test_released_failure_can_retry_immediately(
    routing: tuple[DynamoClient, OCRRoutingDecision],
) -> None:
    """A caught failure releases its claim without waiting for expiry."""
    client, decision = routing
    now = datetime.now(timezone.utc)
    client.claim_ocr_routing_decision(
        decision.image_id, decision.job_id, "first", now=now
    )
    assert (
        client.release_ocr_routing_decision(
            decision.image_id, decision.job_id, "first"
        )
        is True
    )
    assert (
        client.claim_ocr_routing_decision(
            decision.image_id, decision.job_id, "second", now=now
        )
        == "claimed"
    )


@pytest.mark.parametrize("lease_seconds", [True, 0, 900, "960"])
def test_lease_must_exceed_lambda_runtime(
    routing: tuple[DynamoClient, OCRRoutingDecision], lease_seconds: Any
) -> None:
    """A lease cannot expire while a Lambda invocation can still run."""
    client, decision = routing
    with pytest.raises(EntityValidationError, match="exceed 900"):
        client.claim_ocr_routing_decision(
            decision.image_id,
            decision.job_id,
            "owner",
            now=datetime.now(timezone.utc),
            lease_seconds=lease_seconds,
        )


def test_missing_routing_is_not_claimed(
    routing: tuple[DynamoClient, OCRRoutingDecision],
) -> None:
    """Claims never create replacement rows for missing routing data."""
    client, decision = routing
    with pytest.raises(EntityNotFoundError):
        client.claim_ocr_routing_decision(
            decision.image_id,
            str(uuid4()),
            "owner",
            now=datetime.now(timezone.utc),
        )


@pytest.mark.parametrize("method", ["claim", "complete", "release"])
def test_infrastructure_errors_remain_retryable(
    routing: tuple[DynamoClient, OCRRoutingDecision], method: str
) -> None:
    """Throttled conditional writes preserve the DAL retryable exception."""
    client, decision = routing
    decision.status = "COMPLETED"
    decision.updated_at = datetime.now(timezone.utc)
    error = ClientError(
        {
            "Error": {
                "Code": "ProvisionedThroughputExceededException",
                "Message": "busy",
            }
        },
        "UpdateItem",
    )
    with (
        patch.object(client, "_client") as raw,
        pytest.raises(DynamoDBThroughputError),
    ):
        raw.update_item.side_effect = error
        if method == "claim":
            client.claim_ocr_routing_decision(
                decision.image_id,
                decision.job_id,
                "owner",
                now=datetime.now(timezone.utc),
            )
        elif method == "complete":
            client.complete_ocr_routing_decision(decision, "owner")
        else:
            client.release_ocr_routing_decision(
                decision.image_id, decision.job_id, "owner"
            )
