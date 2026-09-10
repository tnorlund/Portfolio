"""The backfill script applies owner fact overrides like the Lambda does.

Every writer that recomputes a summary from labels must read the
receipt's ReceiptFactOverride through the DynamoClient accessor and let
its stated facts win; otherwise running the script after stating a fact
silently drops it again.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

# isort: off
# scripts/ and receipt_dynamo are grouped differently by the CI jobs that
# do and do not install the local packages; pin the grouping.
import boto3
import pytest
from botocore.client import BaseClient
from moto import mock_aws

from scripts import backfill_receipt_summaries as backfill_module
from scripts.backfill_receipt_summaries import backfill_summaries

from receipt_dynamo import DynamoClient, Receipt, ReceiptWord, ReceiptWordLabel
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities.receipt_fact_override import (
    ReceiptFactOverride,
)
from receipt_dynamo.entities.receipt_summary import (
    MonetaryTotals,
    ReceiptSummary,
)
from receipt_dynamo.entities.receipt_summary_record import (
    ReceiptSummaryRecord,
)

# isort: on

IMAGE_ID = "b7eecdb7-9eaf-47c0-941a-b576604c2e9d"
KEY = f"{IMAGE_ID}_1"


class FakeClient:
    """Minimal DynamoClient stand-in for backfill_summaries."""

    def __init__(self, override=None):
        self.override = override
        self.override_reads = []
        self.upserted = []
        self.existing = ReceiptSummaryRecord.from_summary(
            ReceiptSummary(
                image_id=IMAGE_ID,
                receipt_id=1,
                totals=MonetaryTotals(grand_total=47.18),
                ledger="chase",
                bank_amount=47.18,
                bank_match_confidence=1.0,
            )
        )

    def list_receipt_places(self, **kwargs):
        return [], None

    def list_receipt_summaries(self, **kwargs):
        return [self.existing], None

    def list_receipt_details(self, **kwargs):
        bundle = SimpleNamespace(
            receipt=SimpleNamespace(image_id=IMAGE_ID, receipt_id=1),
            word_labels=[],
            words=[],
        )
        return SimpleNamespace(bundles={KEY: bundle}, last_evaluated_key=None)

    def get_receipt_fact_override(self, image_id, receipt_id):
        self.override_reads.append((image_id, receipt_id))
        return self.override

    def upsert_receipt_summaries(self, records):
        self.upserted.extend(records)


def test_backfill_applies_the_owner_fact():
    client = FakeClient(
        ReceiptFactOverride(
            image_id=IMAGE_ID,
            receipt_id=1,
            date="2026-09-01",
            date_reference="Chase statement",
        )
    )

    stats = backfill_summaries(client, batch_size=1, dry_run=False)

    assert client.override_reads == [(IMAGE_ID, 1)]
    record = client.upserted[0]
    assert record.date == datetime(2026, 9, 1)
    assert record.overrides_applied == ["date"]
    # Offline bank fields still carry over alongside the override.
    assert record.ledger == "chase"
    assert record.bank_amount == 47.18
    assert stats["summaries_with_override"] == 1
    assert stats["summaries_with_date"] == 1


def test_backfill_without_override_is_unchanged():
    client = FakeClient()

    stats = backfill_summaries(client, batch_size=1, dry_run=False)

    record = client.upserted[0]
    assert record.date is None
    assert record.overrides_applied == []
    assert stats["summaries_with_override"] == 0


def test_backfill_dry_run_still_reads_the_override():
    client = FakeClient(
        ReceiptFactOverride(
            image_id=IMAGE_ID,
            receipt_id=1,
            merchant_name="Trader Joe's",
            merchant_name_reference="cropped header",
        )
    )

    stats = backfill_summaries(client)  # dry run is the default

    assert client.upserted == []
    assert stats["summaries_with_override"] == 1


# --- CLI: dry run by default, --apply opt-in, prod refused --------------

DYNAMODB_WRITE_OPERATIONS = frozenset(
    {
        "PutItem",
        "UpdateItem",
        "DeleteItem",
        "BatchWriteItem",
        "TransactWriteItems",
    }
)


@contextmanager
def _dynamodb_write_spy():
    """Record every DynamoDB write any boto3 client performs."""
    original = BaseClient._make_api_call
    writes: list[str] = []

    def spy(self, operation_name, api_params):
        if (
            self.meta.service_model.service_name == "dynamodb"
            and operation_name in DYNAMODB_WRITE_OPERATIONS
        ):
            writes.append(operation_name)
        return original(self, operation_name, api_params)

    with patch.object(BaseClient, "_make_api_call", spy):
        yield writes


@pytest.fixture
def mock_aws_services(monkeypatch):
    """Replace the root conftest's MagicMock boto3 with moto (no creds)."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    with mock_aws():
        yield


@pytest.fixture
def table(mock_aws_services):
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    name = "BackfillSummariesTable"
    throughput = {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5}
    indexes = [
        {
            "IndexName": f"GSI{n}",
            "KeySchema": [
                {"AttributeName": f"GSI{n}PK", "KeyType": "HASH"},
                {"AttributeName": f"GSI{n}SK", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
            "ProvisionedThroughput": throughput,
        }
        for n in (1, 2, 3, 4)
    ]
    indexes.append(
        {
            "IndexName": "GSITYPE",
            "KeySchema": [{"AttributeName": "TYPE", "KeyType": "HASH"}],
            "Projection": {"ProjectionType": "ALL"},
            "ProvisionedThroughput": throughput,
        }
    )
    attrs = ["PK", "SK", "TYPE"] + [
        f"GSI{n}{part}" for n in (1, 2, 3, 4) for part in ("PK", "SK")
    ]
    dynamodb.create_table(
        TableName=name,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": a, "AttributeType": "S"} for a in attrs
        ],
        ProvisionedThroughput=throughput,
        GlobalSecondaryIndexes=indexes,
    )
    dynamodb.meta.client.get_waiter("table_exists").wait(TableName=name)
    return name


def _receipt(receipt_id: int) -> Receipt:
    return Receipt(
        receipt_id=receipt_id,
        image_id=IMAGE_ID,
        width=1000,
        height=2000,
        timestamp_added=datetime.now(timezone.utc).isoformat(),
        raw_s3_bucket="bucket",
        raw_s3_key="key",
        top_left={"x": 0, "y": 0},
        top_right={"x": 1000, "y": 0},
        bottom_left={"x": 0, "y": 2000},
        bottom_right={"x": 1000, "y": 2000},
        sha256="sha256",
    )


def _word(receipt_id, line_id, word_id, text) -> ReceiptWord:
    x = 0.1 + 0.15 * word_id
    y = 0.05 * line_id
    return ReceiptWord(
        image_id=IMAGE_ID,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box={"x": x, "y": y, "width": 0.1, "height": 0.02},
        top_left={"x": x, "y": y + 0.02},
        top_right={"x": x + 0.1, "y": y + 0.02},
        bottom_left={"x": x, "y": y},
        bottom_right={"x": x + 0.1, "y": y},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.99,
    )


def _label(receipt_id, line_id, word_id, label) -> ReceiptWordLabel:
    return ReceiptWordLabel(
        image_id=IMAGE_ID,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        label=label,
        reasoning="test",
        timestamp_added="2026-09-10T00:00:00+00:00",
        validation_status=ValidationStatus.VALID,
    )


def _seed(client: DynamoClient) -> None:
    """Two receipts: one plain, one whose owner stated the date."""
    for receipt_id in (1, 2):
        client.add_receipt(_receipt(receipt_id))
        client.add_receipt_words(
            [
                _word(receipt_id, 9, 1, "TOTAL"),
                _word(receipt_id, 9, 2, "47.18"),
            ]
        )
        client.add_receipt_word_labels(
            [_label(receipt_id, 9, 2, "GRAND_TOTAL")]
        )
    client.add_receipt_fact_override(
        ReceiptFactOverride(
            image_id=IMAGE_ID,
            receipt_id=2,
            date="2026-09-01",
            date_reference="Chase statement",
        )
    )


def _run(table, argv, caplog):
    with patch.object(
        backfill_module,
        "load_env",
        return_value={"dynamodb_table_name": table},
    ):
        with caplog.at_level("INFO", logger=backfill_module.__name__):
            return backfill_module.main(argv)


def test_cli_without_apply_performs_zero_writes(table, caplog):
    client = DynamoClient(table)
    _seed(client)

    with _dynamodb_write_spy() as writes:
        assert _run(table, [], caplog) == 0

    assert writes == []
    for receipt_id in (1, 2):
        with pytest.raises(Exception):
            client.get_receipt_summary(IMAGE_ID, receipt_id)
    assert "DRY RUN - no changes will be written" in caplog.text
    assert "Summaries that would be written: 2" in caplog.text
    assert "With owner fact:    1" in caplog.text


def test_cli_apply_writes_with_owner_facts(table, caplog):
    client = DynamoClient(table)
    _seed(client)

    with _dynamodb_write_spy() as writes:
        assert _run(table, ["--apply"], caplog) == 0

    assert writes and set(writes) == {"BatchWriteItem"}
    plain = client.get_receipt_summary(IMAGE_ID, 1)
    stated = client.get_receipt_summary(IMAGE_ID, 2)
    assert plain.date is None and plain.overrides_applied == []
    assert stated.date == datetime(2026, 9, 1)
    assert stated.overrides_applied == ["date"]
    assert "Summaries written: 2" in caplog.text


@pytest.mark.parametrize(
    "argv, env",
    [
        (["--env", "prod-d7ff76a"], {"dynamodb_table_name": "unused"}),
        (["--env", "dev"], {"dynamodb_table_name": "ReceiptsTable-d7ff76a"}),
        (["--env", "dev", "--apply"], {"dynamodb_table_name": "x-d7ff76a"}),
    ],
)
def test_prod_is_refused_before_any_client_is_built(argv, env, capsys):
    with patch.object(backfill_module, "load_env", return_value=env):
        with patch.object(backfill_module, "DynamoClient") as client_cls:
            with pytest.raises(SystemExit) as exc:
                backfill_module.main(argv)

    assert exc.value.code == 2
    assert "refusing" in capsys.readouterr().err
    client_cls.assert_not_called()


def test_env_defaults_to_dev(capsys):
    seen = {}

    def fake_load_env(env):
        seen["env"] = env
        return {"dynamodb_table_name": "RefusedAnyway-d7ff76a"}

    with patch.object(backfill_module, "load_env", fake_load_env):
        with pytest.raises(SystemExit):
            backfill_module.main([])

    assert seen["env"] == "dev"
