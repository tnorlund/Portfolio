"""Integration tests for guarded receipt-word-label batch updates."""

from datetime import datetime
from typing import Any
from uuid import uuid4

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from receipt_dynamo import DynamoClient, ReceiptWordLabel
from receipt_dynamo.data.batch_label_update import (
    AUDIT_PK_PREFIX,
    DEV_TABLE,
    MAX_BATCH,
    AuditStoreError,
    batch_update_word_labels,
)

pytestmark = pytest.mark.integration

IMAGE_ID = str(uuid4())
BASE_UPDATE = {
    "image_id": IMAGE_ID,
    "receipt_id": 1,
    "line_id": 2,
    "word_id": 3,
    "label": "GRAND_TOTAL",
    "new_status": "INVALID",
    "reasoning": "The word is not a grand total.",
}


@pytest.fixture
def batch_dynamo_client(monkeypatch: pytest.MonkeyPatch):
    """Create the exact guarded dev table with only GSI3."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName=DEV_TABLE,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "GSI3PK", "AttributeType": "S"},
                {"AttributeName": "GSI3SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI3",
                    "KeySchema": [
                        {"AttributeName": "GSI3PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI3SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        dynamodb.meta.client.get_waiter("table_exists").wait(
            TableName=DEV_TABLE
        )
        monkeypatch.setenv("DYNAMODB_TABLE_NAME", DEV_TABLE)
        yield DynamoClient(DEV_TABLE)


def _update(**overrides: Any) -> dict[str, Any]:
    """Return a fresh batch-update request row."""
    return {**BASE_UPDATE, **overrides}


def _seed_label(
    dynamo_client: DynamoClient,
    *,
    validation_status: str = "VALID",
    **overrides: Any,
) -> ReceiptWordLabel:
    """Seed a ReceiptWordLabel through the public DynamoClient API."""
    values = _update(**overrides)
    label = ReceiptWordLabel(
        image_id=values["image_id"],
        receipt_id=values["receipt_id"],
        line_id=values["line_id"],
        word_id=values["word_id"],
        label=values["label"],
        reasoning="Original reasoning.",
        timestamp_added=datetime(2026, 7, 18, 12, 0, 0),
        validation_status=validation_status,
        label_proposed_by="seed-test",
    )
    dynamo_client.add_receipt_word_label(label)
    return label


def _get_item(
    dynamo_client: DynamoClient,
    label: ReceiptWordLabel,
) -> dict[str, Any]:
    """Fetch a seeded label through the low-level client."""
    response = dynamo_client._client.get_item(
        TableName=DEV_TABLE,
        Key=label.key,
        ConsistentRead=True,
    )
    return response["Item"]


def _audit_items(
    dynamo_client: DynamoClient,
    batch_id: str,
) -> dict[str, dict[str, Any]]:
    """Fetch one durable audit partition keyed by its sort keys."""
    response = dynamo_client._client.query(
        TableName=DEV_TABLE,
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={
            ":pk": {"S": f"{AUDIT_PK_PREFIX}{batch_id}"}
        },
        ConsistentRead=True,
    )
    return {item["SK"]["S"]: item for item in response["Items"]}


def _client_error(code: str) -> ClientError:
    """Build a low-level DynamoDB error with a stable AWS code."""
    return ClientError(
        {"Error": {"Code": code, "Message": "adversarial failure"}},
        "TransactWriteItems",
    )


@pytest.mark.parametrize(
    ("new_status", "error_match"),
    [
        ("PENDING", "VALID->PENDING is not allowed"),
        ("BOGUS", "must be a valid ValidationStatus"),
    ],
)
def test_rejects_disallowed_or_invalid_transition(
    batch_dynamo_client: DynamoClient,
    new_status: str,
    error_match: str,
) -> None:
    """Reject non-whitelisted and non-enum statuses before reading rows."""
    with pytest.raises(ValueError, match=error_match):
        batch_update_word_labels(
            batch_dynamo_client,
            [_update(new_status=new_status)],
            label_proposed_by="test-reviewer",
        )

    with pytest.raises(
        ValueError,
        match="expected_old_status must be a valid ValidationStatus",
    ):
        batch_update_word_labels(
            batch_dynamo_client,
            [_update()],
            label_proposed_by="test-reviewer",
            expected_old_status="BOGUS",
        )


def test_rejects_missing_or_mismatched_table_environment(
    batch_dynamo_client: DynamoClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require the environment guard to match the client and dev table."""
    monkeypatch.delenv("DYNAMODB_TABLE_NAME")

    with pytest.raises(ValueError, match="DYNAMODB_TABLE_NAME must be set"):
        batch_update_word_labels(
            batch_dynamo_client,
            [],
            label_proposed_by="test-reviewer",
        )

    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "SomeOtherTable")
    with pytest.raises(
        ValueError,
        match="must equal dynamo_client.table_name",
    ):
        batch_update_word_labels(
            batch_dynamo_client,
            [],
            label_proposed_by="test-reviewer",
        )


def test_rejects_batches_over_cap(
    batch_dynamo_client: DynamoClient,
) -> None:
    """Reject oversized batches rather than truncating them."""
    updates = [_update() for _ in range(MAX_BATCH + 1)]

    with pytest.raises(ValueError, match="maximum is 500"):
        batch_update_word_labels(
            batch_dynamo_client,
            updates,
            label_proposed_by="test-reviewer",
        )


def test_dry_run_makes_no_write_or_audit(
    batch_dynamo_client: DynamoClient,
) -> None:
    """Dry runs pre-check rows without writing DynamoDB or audit items."""
    label = _seed_label(batch_dynamo_client)

    result = batch_update_word_labels(
        batch_dynamo_client,
        [_update()],
        label_proposed_by="test-reviewer",
    )

    item = _get_item(batch_dynamo_client, label)
    all_items = batch_dynamo_client._client.scan(TableName=DEV_TABLE)["Items"]
    assert result["dry_run"] is True
    assert result["batch_id"] is None
    assert result["audit_uri"] is None
    assert result["audit_sha256"] is None
    assert result["audit_manifest_status"] == "not_run"
    assert result["counts"]["WOULD_UPDATE"] == 1
    assert result["rows"][0]["outcome"] == "WOULD_UPDATE"
    assert result["spot_check"] == "not_run"
    assert item["validation_status"]["S"] == "VALID"
    assert all(
        not candidate["PK"]["S"].startswith(AUDIT_PK_PREFIX)
        for candidate in all_items
    )


def test_live_update_audit_and_label_commit_atomically(
    batch_dynamo_client: DynamoClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persist the full plan first, then atomically commit row plus outcome."""
    label = _seed_label(batch_dynamo_client)
    long_reasoning = "r" * 650
    original_transaction = batch_dynamo_client._client.transact_write_items
    saw_complete_plan = False

    def transact_after_plan(*args: Any, **kwargs: Any) -> Any:
        nonlocal saw_complete_plan
        scan = batch_dynamo_client._client.scan(TableName=DEV_TABLE)["Items"]
        audits = [
            item
            for item in scan
            if item["PK"]["S"].startswith(AUDIT_PK_PREFIX)
        ]
        assert len(audits) == 2
        assert {item["outcome"]["S"] for item in audits} == {"PLANNED"}
        saw_complete_plan = True
        return original_transaction(*args, **kwargs)

    monkeypatch.setattr(
        batch_dynamo_client._client,
        "transact_write_items",
        transact_after_plan,
    )

    result = batch_update_word_labels(
        batch_dynamo_client,
        [_update(reasoning=long_reasoning)],
        label_proposed_by="test-reviewer",
        dry_run=False,
    )

    item = _get_item(batch_dynamo_client, label)
    audit = _audit_items(batch_dynamo_client, result["batch_id"])
    manifest = audit["MANIFEST"]
    row_audit = audit["ROW#00000"]

    assert saw_complete_plan is True
    assert result["dry_run"] is False
    assert result["counts"]["UPDATED"] == 1
    assert result["rows"][0]["outcome"] == "UPDATED"
    assert result["rows"][0]["reconciliation"] == "ATOMIC_COMMIT"
    assert result["spot_check"] == "passed"
    assert result["audit_uri"].startswith(f"dynamodb://{DEV_TABLE}/")
    assert len(result["audit_sha256"]) == 64
    assert result["audit_manifest_status"] == "complete"
    assert item["validation_status"]["S"] == "INVALID"
    assert item["GSI3PK"]["S"] == "VALIDATION_STATUS#INVALID"
    assert item["label_proposed_by"]["S"] == "test-reviewer"
    assert item["reasoning"]["S"] == long_reasoning[:500]
    assert manifest["outcome"]["S"] == "COMPLETED"
    assert row_audit["outcome"]["S"] == "UPDATED"
    assert row_audit["reconciliation"]["S"] == "ATOMIC_COMMIT"
    assert row_audit["reasoning"]["S"] == long_reasoning
    assert row_audit["label_proposed_by"]["S"] == "test-reviewer"


def test_status_changed_and_missing_rows_have_durable_outcomes(
    batch_dynamo_client: DynamoClient,
) -> None:
    """Make pre-check skips explicit in the durable outcome ledger."""
    _seed_label(batch_dynamo_client, validation_status="INVALID")
    missing = _update(word_id=999)

    result = batch_update_word_labels(
        batch_dynamo_client,
        [_update(), missing],
        label_proposed_by="test-reviewer",
        dry_run=False,
    )

    audit = _audit_items(batch_dynamo_client, result["batch_id"])
    assert result["counts"]["SKIPPED_STATUS_CHANGED"] == 1
    assert result["counts"]["SKIPPED_NOT_FOUND"] == 1
    assert audit["ROW#00000"]["outcome"]["S"] == ("SKIPPED_STATUS_CHANGED")
    assert audit["ROW#00000"]["observed_status"]["S"] == "INVALID"
    assert audit["ROW#00001"]["outcome"]["S"] == "SKIPPED_NOT_FOUND"
    assert audit["ROW#00001"]["observed_status"]["NULL"] is True


@pytest.mark.parametrize("label_proposed_by", ["", "   ", None])
def test_requires_non_empty_label_proposed_by(
    batch_dynamo_client: DynamoClient,
    label_proposed_by: Any,
) -> None:
    """Reject missing, empty, or whitespace-only audit identities."""
    with pytest.raises(
        ValueError,
        match="label_proposed_by must be a non-empty string",
    ):
        batch_update_word_labels(
            batch_dynamo_client,
            [],
            label_proposed_by=label_proposed_by,
        )


def test_durable_audit_plan_failure_blocks_every_mutation(
    batch_dynamo_client: DynamoClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed when the durable sink rejects the plan."""
    label = _seed_label(batch_dynamo_client)
    transaction_called = False

    def deny_audit(*args: Any, **kwargs: Any) -> Any:
        raise _client_error("AccessDeniedException")

    def observe_transaction(*args: Any, **kwargs: Any) -> Any:
        nonlocal transaction_called
        transaction_called = True

    monkeypatch.setattr(batch_dynamo_client._client, "put_item", deny_audit)
    monkeypatch.setattr(
        batch_dynamo_client._client,
        "transact_write_items",
        observe_transaction,
    )

    with pytest.raises(
        AuditStoreError,
        match="failed before mutation.*AccessDeniedException",
    ):
        batch_update_word_labels(
            batch_dynamo_client,
            [_update()],
            label_proposed_by="test-reviewer",
            dry_run=False,
        )

    assert transaction_called is False
    assert _get_item(batch_dynamo_client, label)["validation_status"]["S"] == (
        "VALID"
    )


def test_nonconditional_dynamo_error_is_durably_unambiguous(
    batch_dynamo_client: DynamoClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persist the AWS code and prove a throughput failure did not apply."""
    label = _seed_label(batch_dynamo_client)

    def throttle(*args: Any, **kwargs: Any) -> Any:
        raise _client_error("ProvisionedThroughputExceededException")

    monkeypatch.setattr(
        batch_dynamo_client._client,
        "transact_write_items",
        throttle,
    )

    result = batch_update_word_labels(
        batch_dynamo_client,
        [_update()],
        label_proposed_by="test-reviewer",
        dry_run=False,
    )

    item = _get_item(batch_dynamo_client, label)
    row = result["rows"][0]
    audit = _audit_items(batch_dynamo_client, result["batch_id"])["ROW#00000"]
    assert result["counts"]["ERROR"] == 1
    assert row["outcome"] == "ERROR"
    assert row["error_code"] == "ProvisionedThroughputExceededException"
    assert row["reconciliation"] == "NOT_APPLIED"
    assert item["validation_status"]["S"] == "VALID"
    assert audit["outcome"]["S"] == "ERROR"
    assert audit["aws_error_code"]["S"] == (
        "ProvisionedThroughputExceededException"
    )
    assert audit["reconciliation"]["S"] == "NOT_APPLIED"
    assert audit["observed_status"]["S"] == "VALID"


def test_ambiguous_transport_error_reconciles_atomic_commit(
    batch_dynamo_client: DynamoClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recognize a committed transaction even when its response is lost."""
    label = _seed_label(batch_dynamo_client)
    original_transaction = batch_dynamo_client._client.transact_write_items

    def commit_then_lose_response(*args: Any, **kwargs: Any) -> Any:
        original_transaction(*args, **kwargs)
        raise _client_error("InternalServerError")

    monkeypatch.setattr(
        batch_dynamo_client._client,
        "transact_write_items",
        commit_then_lose_response,
    )

    result = batch_update_word_labels(
        batch_dynamo_client,
        [_update()],
        label_proposed_by="test-reviewer",
        dry_run=False,
    )

    item = _get_item(batch_dynamo_client, label)
    row = result["rows"][0]
    audit = _audit_items(batch_dynamo_client, result["batch_id"])["ROW#00000"]
    assert result["counts"]["UPDATED"] == 1
    assert row["outcome"] == "UPDATED"
    assert row["old_status"] == "VALID"
    assert row["error_code"] == "InternalServerError"
    assert row["reconciliation"] == "COMMITTED_AFTER_ERROR"
    assert item["validation_status"]["S"] == "INVALID"
    assert audit["outcome"]["S"] == "UPDATED"
    assert audit["aws_error_code"]["S"] == "InternalServerError"
    assert audit["reconciliation"]["S"] == "COMMITTED_AFTER_ERROR"
