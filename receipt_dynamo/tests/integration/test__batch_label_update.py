"""Integration tests for guarded receipt-word-label batch updates."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import boto3
import pytest
from moto import mock_aws

from receipt_dynamo import DynamoClient, ReceiptWordLabel
from receipt_dynamo.data.batch_label_update import (
    DEV_TABLE,
    MAX_BATCH,
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
                        {
                            "AttributeName": "GSI3PK",
                            "KeyType": "HASH",
                        },
                        {
                            "AttributeName": "GSI3SK",
                            "KeyType": "RANGE",
                        },
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
    )
    return response["Item"]


@pytest.mark.parametrize(
    ("new_status", "error_match"),
    [
        ("PENDING", "VALID->PENDING is not allowed"),
        ("BOGUS", "must be a valid ValidationStatus"),
    ],
)
def test_rejects_disallowed_or_invalid_transition(
    batch_dynamo_client: DynamoClient,
    tmp_path: Path,
    new_status: str,
    error_match: str,
) -> None:
    """Reject non-whitelisted and non-enum statuses before reading rows."""
    with pytest.raises(ValueError, match=error_match):
        batch_update_word_labels(
            batch_dynamo_client,
            [_update(new_status=new_status)],
            label_proposed_by="test-reviewer",
            audit_path=tmp_path / "audit.jsonl",
        )

    with pytest.raises(
        ValueError,
        match="expected_old_status must be a valid ValidationStatus",
    ):
        batch_update_word_labels(
            batch_dynamo_client,
            [_update()],
            label_proposed_by="test-reviewer",
            audit_path=tmp_path / "audit.jsonl",
            expected_old_status="BOGUS",
        )


def test_rejects_missing_or_mismatched_table_environment(
    batch_dynamo_client: DynamoClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Require the environment guard to match the client and dev table."""
    monkeypatch.delenv("DYNAMODB_TABLE_NAME")

    with pytest.raises(ValueError, match="DYNAMODB_TABLE_NAME must be set"):
        batch_update_word_labels(
            batch_dynamo_client,
            [],
            label_proposed_by="test-reviewer",
            audit_path=tmp_path / "audit.jsonl",
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
            audit_path=tmp_path / "audit.jsonl",
        )


def test_rejects_batches_over_cap(
    batch_dynamo_client: DynamoClient,
    tmp_path: Path,
) -> None:
    """Reject oversized batches rather than truncating them."""
    updates = [_update() for _ in range(MAX_BATCH + 1)]

    with pytest.raises(ValueError, match="maximum is 500"):
        batch_update_word_labels(
            batch_dynamo_client,
            updates,
            label_proposed_by="test-reviewer",
            audit_path=tmp_path / "audit.jsonl",
        )


def test_dry_run_makes_no_write_or_audit(
    batch_dynamo_client: DynamoClient,
    tmp_path: Path,
) -> None:
    """Dry runs pre-check rows without writing DynamoDB or audit files."""
    label = _seed_label(batch_dynamo_client)
    audit_path = tmp_path / "audit.jsonl"

    result = batch_update_word_labels(
        batch_dynamo_client,
        [_update()],
        label_proposed_by="test-reviewer",
        audit_path=audit_path,
    )

    item = _get_item(batch_dynamo_client, label)
    assert result["dry_run"] is True
    assert result["counts"]["WOULD_UPDATE"] == 1
    assert result["rows"][0]["outcome"] == "WOULD_UPDATE"
    assert result["spot_check"] == "not_run"
    assert item["validation_status"]["S"] == "VALID"
    assert item["GSI3PK"]["S"] == "VALIDATION_STATUS#VALID"
    assert not audit_path.exists()


def test_live_update_audits_before_write_and_spot_checks(
    batch_dynamo_client: DynamoClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Apply a live update with ordered audit, truncation, and GSI3 verify."""
    label = _seed_label(batch_dynamo_client)
    audit_path = tmp_path / "audit.jsonl"
    long_reasoning = "r" * 650
    original_update_item = batch_dynamo_client._client.update_item

    def update_item_after_audit(*args: Any, **kwargs: Any) -> Any:
        assert audit_path.exists()
        audit_lines = audit_path.read_text(encoding="utf-8").splitlines()
        assert len(audit_lines) == 1
        assert json.loads(audit_lines[0])["word_id"] == 3
        return original_update_item(*args, **kwargs)

    monkeypatch.setattr(
        batch_dynamo_client._client,
        "update_item",
        update_item_after_audit,
    )

    result = batch_update_word_labels(
        batch_dynamo_client,
        [_update(reasoning=long_reasoning)],
        label_proposed_by="test-reviewer",
        audit_path=audit_path,
        dry_run=False,
    )

    item = _get_item(batch_dynamo_client, label)
    audit_lines = audit_path.read_text(encoding="utf-8").splitlines()
    audit_record = json.loads(audit_lines[0])

    assert result["dry_run"] is False
    assert result["counts"]["UPDATED"] == 1
    assert result["rows"][0]["outcome"] == "UPDATED"
    assert result["spot_check"] == "passed"
    assert item["validation_status"]["S"] == "INVALID"
    assert item["GSI3PK"]["S"] == "VALIDATION_STATUS#INVALID"
    assert item["label_proposed_by"]["S"] == "test-reviewer"
    assert item["reasoning"]["S"] == long_reasoning[:500]
    assert len(item["reasoning"]["S"]) == 500
    assert len(audit_lines) == 1
    assert audit_record["old_status"] == "VALID"
    assert audit_record["new_status"] == "INVALID"
    assert audit_record["reasoning"] == long_reasoning
    assert audit_record["label_proposed_by"] == "test-reviewer"


def test_skips_row_when_status_already_changed(
    batch_dynamo_client: DynamoClient,
    tmp_path: Path,
) -> None:
    """Skip a row whose current status no longer matches the expectation."""
    label = _seed_label(
        batch_dynamo_client,
        validation_status="INVALID",
    )
    audit_path = tmp_path / "audit.jsonl"

    result = batch_update_word_labels(
        batch_dynamo_client,
        [_update()],
        label_proposed_by="test-reviewer",
        audit_path=audit_path,
        dry_run=False,
    )

    item = _get_item(batch_dynamo_client, label)
    row = result["rows"][0]

    assert result["counts"]["SKIPPED_STATUS_CHANGED"] == 1
    assert row["outcome"] == "SKIPPED_STATUS_CHANGED"
    assert row["old_status"] == "INVALID"
    assert item["validation_status"]["S"] == "INVALID"
    assert not audit_path.exists()


def test_skips_missing_row(
    batch_dynamo_client: DynamoClient,
    tmp_path: Path,
) -> None:
    """Report a missing label without auditing or creating it."""
    audit_path = tmp_path / "audit.jsonl"

    result = batch_update_word_labels(
        batch_dynamo_client,
        [_update()],
        label_proposed_by="test-reviewer",
        audit_path=audit_path,
        dry_run=False,
    )

    row = result["rows"][0]
    assert result["counts"]["SKIPPED_NOT_FOUND"] == 1
    assert row["outcome"] == "SKIPPED_NOT_FOUND"
    assert row["old_status"] is None
    assert not audit_path.exists()


@pytest.mark.parametrize("label_proposed_by", ["", "   ", None])
def test_requires_non_empty_label_proposed_by(
    batch_dynamo_client: DynamoClient,
    tmp_path: Path,
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
            audit_path=tmp_path / "audit.jsonl",
        )
