"""The offline summary recompute previews by default and writes only on --apply.

It runs the summary updater Lambda's own compute path against a moto
table, so a parser fix (here: month-name dates OCR splits into words)
shows up in the dry-run preview exactly as the Lambda would store it.
"""

import importlib.util
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# isort: off
# receipt_dynamo is first-party to isort in jobs that do not install the
# rest of the stack and third-party in repository tests; pin the block.
import boto3
import pytest
from moto import mock_aws

from receipt_dynamo import (
    DynamoClient,
    Receipt,
    ReceiptWord,
    ReceiptWordLabel,
)
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities.receipt_summary import (
    MonetaryTotals,
    ReceiptSummary,
)
from receipt_dynamo.entities.receipt_summary_record import (
    ReceiptSummaryRecord,
)

# isort: on

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "recompute_receipt_summaries.py"
)
spec = importlib.util.spec_from_file_location(
    "recompute_receipt_summaries", SCRIPT
)
recompute = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(recompute)

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
OTHER_IMAGE_ID = "4a63915c-22f5-4f11-a3d9-c684eb4b9ef4"


@pytest.fixture(autouse=True)
def mock_aws_services(monkeypatch):
    """Replace the root conftest's MagicMock boto3 with moto.

    The root stub answers every DynamoDB query with a MagicMock whose
    LastEvaluatedKey is truthy, so pagination never ends; moto gives the
    script a real (offline) table instead.
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    with mock_aws():
        yield


@pytest.fixture
def table():
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    name = "RecomputeSummariesTable"
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


def _receipt(image_id: str, receipt_id: int) -> Receipt:
    return Receipt(
        receipt_id=receipt_id,
        image_id=image_id,
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


def _word(image_id, receipt_id, line_id, word_id, text) -> ReceiptWord:
    x = 0.1 + 0.15 * word_id
    y = 0.05 * line_id
    return ReceiptWord(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box={"x": x, "y": y, "width": 0.1, "height": 0.02},
        top_left={"x": x, "y": y},
        top_right={"x": x + 0.1, "y": y},
        bottom_left={"x": x, "y": y + 0.02},
        bottom_right={"x": x + 0.1, "y": y + 0.02},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.95,
    )


def _label(image_id, receipt_id, line_id, word_id, label) -> ReceiptWordLabel:
    return ReceiptWordLabel(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        label=label,
        reasoning="test",
        timestamp_added="2026-09-10T00:00:00+00:00",
        validation_status=ValidationStatus.VALID,
    )


def _stored_summary(image_id, receipt_id, date=None) -> ReceiptSummaryRecord:
    return ReceiptSummaryRecord.from_summary(
        ReceiptSummary(
            image_id=image_id,
            receipt_id=receipt_id,
            merchant_name=None,
            date=date,
            totals=MonetaryTotals(grand_total=47.18),
            item_count=0,
        )
    )


def _seed_dateless_receipt(client: DynamoClient) -> None:
    """A receipt whose DATE words the old parser rejected: May / 6. / 2025."""
    client.add_receipt(_receipt(IMAGE_ID, 1))
    client.add_receipt_words(
        [
            _word(IMAGE_ID, 1, 4, 1, "May"),
            _word(IMAGE_ID, 1, 4, 2, "6."),
            _word(IMAGE_ID, 1, 4, 3, "2025"),
            _word(IMAGE_ID, 1, 9, 1, "TOTAL"),
            _word(IMAGE_ID, 1, 9, 2, "47.18"),
        ]
    )
    client.add_receipt_word_labels(
        [
            _label(IMAGE_ID, 1, 4, 1, "DATE"),
            _label(IMAGE_ID, 1, 4, 2, "DATE"),
            _label(IMAGE_ID, 1, 4, 3, "DATE"),
            _label(IMAGE_ID, 1, 9, 2, "GRAND_TOTAL"),
        ]
    )
    client.add_receipt_summary(_stored_summary(IMAGE_ID, 1))


def _seed_dated_receipt(client: DynamoClient) -> None:
    """A receipt whose stored summary already matches its labels."""
    client.add_receipt(_receipt(OTHER_IMAGE_ID, 2))
    client.add_receipt_words(
        [
            _word(OTHER_IMAGE_ID, 2, 4, 1, "01/02/2026"),
            _word(OTHER_IMAGE_ID, 2, 9, 2, "47.18"),
        ]
    )
    client.add_receipt_word_labels(
        [
            _label(OTHER_IMAGE_ID, 2, 4, 1, "DATE"),
            _label(OTHER_IMAGE_ID, 2, 9, 2, "GRAND_TOTAL"),
        ]
    )
    client.add_receipt_summary(
        _stored_summary(OTHER_IMAGE_ID, 2, date=datetime(2026, 1, 2))
    )


def test_dry_run_previews_without_writing(table, capsys):
    client = DynamoClient(table)
    _seed_dateless_receipt(client)
    _seed_dated_receipt(client)

    assert recompute.main(["--table", table, "--only-missing-date"]) == 0

    out = capsys.readouterr().out
    assert (
        f"{IMAGE_ID}#1 WOULD UPDATE: date=None total=47.18 items=0 "
        "-> date=2025-05-06 total=47.18 items=0" in out
    )
    assert OTHER_IMAGE_ID not in out
    assert "DRY RUN: 1 receipts examined" in out
    assert "date_filled              1" in out
    # nothing written
    assert client.get_receipt_summary(IMAGE_ID, 1).date is None


def test_without_filter_every_summary_is_examined(table, capsys):
    client = DynamoClient(table)
    _seed_dateless_receipt(client)
    _seed_dated_receipt(client)

    assert recompute.main(["--table", table]) == 0

    out = capsys.readouterr().out
    assert f"{IMAGE_ID}#1 WOULD UPDATE" in out
    assert f"{OTHER_IMAGE_ID}#2 unchanged" in out
    assert "DRY RUN: 2 receipts examined" in out
    assert client.get_receipt_summary(IMAGE_ID, 1).date is None
    assert client.get_receipt_summary(OTHER_IMAGE_ID, 2).date == datetime(
        2026, 1, 2
    )


def test_apply_writes_through_the_lambda_path(table, capsys):
    client = DynamoClient(table)
    _seed_dateless_receipt(client)
    _seed_dated_receipt(client)

    assert (
        recompute.main(["--table", table, "--only-missing-date", "--apply"])
        == 0
    )

    out = capsys.readouterr().out
    assert f"{IMAGE_ID}#1 UPDATED: date=None" in out
    assert "APPLIED: 1 receipts examined" in out
    stored = client.get_receipt_summary(IMAGE_ID, 1)
    assert stored.date == datetime(2025, 5, 6)
    assert stored.grand_total == 47.18
    # the filtered-out receipt is untouched
    assert client.get_receipt_summary(OTHER_IMAGE_ID, 2).date == datetime(
        2026, 1, 2
    )


def test_apply_preserves_offline_bank_fields(table):
    client = DynamoClient(table)
    _seed_dateless_receipt(client)
    stored = client.get_receipt_summary(IMAGE_ID, 1)
    client.upsert_receipt_summary(
        ReceiptSummaryRecord.from_summary(
            ReceiptSummary(
                image_id=IMAGE_ID,
                receipt_id=1,
                merchant_name=None,
                date=None,
                totals=MonetaryTotals(grand_total=47.18),
                item_count=0,
                ledger="chase",
                bank_amount=47.18,
                bank_match_confidence=0.9,
            )
        )
    )
    assert stored.ledger is None  # the seed had no bank fields

    assert recompute.main(["--table", table, "--apply"]) == 0

    after = client.get_receipt_summary(IMAGE_ID, 1)
    assert after.date == datetime(2025, 5, 6)
    assert (after.ledger, after.bank_amount, after.bank_match_confidence) == (
        "chase",
        47.18,
        0.9,
    )


def test_orphan_summary_is_skipped_not_recomputed(table, capsys):
    client = DynamoClient(table)
    client.add_receipt_summary(_stored_summary(IMAGE_ID, 3))

    assert recompute.main(["--table", table]) == 0
    assert f"{IMAGE_ID}#3 SKIPPED (parent receipt deleted)" in (
        capsys.readouterr().out
    )
    # dry run leaves the orphan alone; the Lambda path sweeps it on apply
    assert client.get_receipt_summary(IMAGE_ID, 3).date is None


@pytest.mark.parametrize(
    "argv",
    [
        ["--table", "ReceiptsTable-d7ff76a"],
        ["--table", "copy-d7ff76a-of-prod", "--only-missing-date"],
        ["--table", "ReceiptsTable-d7ff76a", "--apply"],
    ],
)
def test_prod_table_is_refused_before_any_client_is_built(
    monkeypatch, capsys, argv
):
    def _never(*_args, **_kwargs):
        raise AssertionError("DynamoClient must not be constructed")

    monkeypatch.setattr(recompute, "DynamoClient", _never)
    with pytest.raises(SystemExit):
        recompute.main(argv)
    assert "refusing to recompute the prod table" in capsys.readouterr().err


def test_inherited_lambda_table_env_never_opens_a_client(monkeypatch):
    """Importing the script must not let the Lambda module describe a table.

    summary_processor builds a DynamoClient at import time from
    DYNAMODB_TABLE_NAME; the script drops that variable before importing
    so an inherited prod value cannot be read before the --table check.
    """
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "ReceiptsTable-d7ff76a")
    monkeypatch.delitem(
        sys.modules, "infra.receipt_summary_updater.summary_processor"
    )

    def _never(self, *_args, **_kwargs):
        raise AssertionError("DynamoClient must not be constructed")

    monkeypatch.setattr(DynamoClient, "__init__", _never)
    fresh_spec = importlib.util.spec_from_file_location(
        "recompute_fresh", SCRIPT
    )
    fresh = importlib.util.module_from_spec(fresh_spec)
    assert fresh_spec.loader is not None
    fresh_spec.loader.exec_module(fresh)
    assert fresh.summary_processor.dynamo_client is None
    assert "DYNAMODB_TABLE_NAME" not in os.environ


def test_table_is_required(capsys):
    with pytest.raises(SystemExit):
        recompute.main([])
    assert "--table" in capsys.readouterr().err
