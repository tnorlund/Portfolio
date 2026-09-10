"""The line-item stage stamps the summary's merchant onto merchant-less rows.

The merchant index (GSI1 ``MERCHANT#<slug>``) only lists a row whose
``merchant_name`` is set; rows written before the summary knew its merchant
never appear there. ``backfill_line_item_merchants`` rewrites exactly those
rows -- same key, same content, merchant added -- from the receipt's own
summary, and ``update_receipt_line_items`` runs it before recomputing.

Runs against moto through the real DynamoClient so the rewrite and the
index membership it exists for are both observed on the table.
"""

from dataclasses import replace
from datetime import datetime, timezone

import boto3
import pytest
from infra.receipt_line_item_updater import line_item_processor
from moto import mock_aws
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.receipt_line_item import ReceiptLineItem
from receipt_dynamo.entities.receipt_summary import (
    MonetaryTotals,
    ReceiptSummary,
)
from receipt_dynamo.entities.receipt_summary_record import (
    ReceiptSummaryRecord,
)

IMAGE_ID = "11111111-2222-4333-8444-555555555555"
RECEIPT_ID = 1
MERCHANT = "Sprouts Farmers Market"
GSI1_KEYS = {"GSI1PK", "GSI1SK"}


def create_table(table_name: str) -> str:
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    indexes = [
        ("GSI1", "GSI1PK", "GSI1SK"),
        ("GSI2", "GSI2PK", "GSI2SK"),
        ("GSI3", "GSI3PK", "GSI3SK"),
    ]
    throughput = {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5}
    dynamodb.create_table(
        TableName=table_name,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "TYPE", "AttributeType": "S"},
        ]
        + [
            {"AttributeName": attr, "AttributeType": "S"}
            for _, pk, sk in indexes
            for attr in (pk, sk)
        ],
        ProvisionedThroughput=throughput,
        GlobalSecondaryIndexes=[
            {
                "IndexName": index,
                "KeySchema": [
                    {"AttributeName": pk, "KeyType": "HASH"},
                    {"AttributeName": sk, "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": throughput,
            }
            for index, pk, sk in indexes
        ]
        + [
            {
                "IndexName": "GSITYPE",
                "KeySchema": [{"AttributeName": "TYPE", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": throughput,
            }
        ],
    )
    dynamodb.meta.client.get_waiter("table_exists").wait(TableName=table_name)
    return table_name


@pytest.fixture
def client(monkeypatch):
    with mock_aws():
        dynamo_client = DynamoClient(create_table("MyMockedTable"))
        monkeypatch.setattr(
            line_item_processor, "dynamo_client", dynamo_client
        )
        yield dynamo_client


def summary(merchant_name):
    return ReceiptSummaryRecord(
        summary=ReceiptSummary(
            image_id=IMAGE_ID,
            receipt_id=RECEIPT_ID,
            merchant_name=merchant_name,
            date=datetime(2026, 9, 1, tzinfo=timezone.utc),
            totals=MonetaryTotals(grand_total=9.72, subtotal=9.00, tax=0.72),
            item_count=0,
        ),
        timestamp_computed="2026-09-01T00:00:00+00:00",
    )


def row(item_index, merchant_name=None, **changes):
    fields = dict(
        image_id=IMAGE_ID,
        receipt_id=RECEIPT_ID,
        item_index=item_index,
        name=f"ITEM {item_index}",
        price="3.00",
        line_ids=[item_index + 1],
        extractor_version="line-items-blocks-v2",
        extracted_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        raw_text=f"ITEM {item_index} 3.00",
        merchant_name=merchant_name,
        reconciliation_status="match",
        source_section_status="VALID",
        source_model_source="swift-worker-v1",
        baseline_figures_agreeing=2,
    )
    fields.update(changes)
    return ReceiptLineItem(**fields)


def stored(client):
    return {
        item.item_index: item
        for item in client.get_receipt_line_items_from_receipt(
            IMAGE_ID, RECEIPT_ID
        )
    }


def without_merchant(item: ReceiptLineItem) -> dict:
    """The stored item minus the fields the backfill is allowed to touch."""
    return {
        key: value
        for key, value in item.to_item().items()
        if key not in GSI1_KEYS | {"merchant_name"}
    }


def test_backfill_stamps_the_summary_merchant_only_where_missing(client):
    client.upsert_receipt_summary(summary(MERCHANT))
    seeded = [
        row(0, merchant_name=None),
        row(1, merchant_name=""),
        row(2, merchant_name="Other Store"),
        row(3, merchant_name=None, name="", name_quality="low"),
    ]
    client.add_receipt_line_items(seeded)
    before = {item.item_index: item.to_item() for item in seeded}

    rewritten = line_item_processor.backfill_line_item_merchants(
        IMAGE_ID, RECEIPT_ID
    )

    assert rewritten == 3
    after = stored(client)
    assert set(after) == {0, 1, 2, 3}
    for index in (0, 1, 3):
        assert after[index].merchant_name == MERCHANT
        # Same key, same content: only merchant_name (and the GSI1 keys
        # derived from it) may differ from the seeded row.
        assert without_merchant(after[index]) == {
            key: value
            for key, value in before[index].items()
            if key not in GSI1_KEYS | {"merchant_name"}
        }
    # A row that already carries a (different) merchant is never touched.
    assert after[2].to_item() == before[2]
    # The point of the exercise: the rows now appear in the merchant
    # index -- except the low-quality name, whose GSI1 keys stay sparse.
    indexed, _ = client.list_receipt_line_items_by_merchant(MERCHANT)
    assert sorted(item.item_index for item in indexed) == [0, 1]
    assert GSI1_KEYS.isdisjoint(after[3].to_item())


def test_backfill_never_touches_rows_without_a_summary_merchant(client):
    client.upsert_receipt_summary(summary(None))
    seeded = [row(0, merchant_name=None), row(1, merchant_name="Kept")]
    client.add_receipt_line_items(seeded)

    assert (
        line_item_processor.backfill_line_item_merchants(IMAGE_ID, RECEIPT_ID)
        == 0
    )
    after = stored(client)
    assert [after[i].to_item() for i in (0, 1)] == [
        item.to_item() for item in seeded
    ]


def test_backfill_without_a_summary_is_a_noop(client):
    client.add_receipt_line_items([row(0, merchant_name=None)])

    assert (
        line_item_processor.backfill_line_item_merchants(IMAGE_ID, RECEIPT_ID)
        == 0
    )
    assert stored(client)[0].merchant_name is None


def test_backfill_with_no_missing_rows_writes_nothing(client, monkeypatch):
    client.upsert_receipt_summary(summary(MERCHANT))
    seeded = [row(0, merchant_name=MERCHANT), row(1, merchant_name="Other")]
    client.add_receipt_line_items(seeded)
    calls = []
    monkeypatch.setattr(
        client,
        "set_receipt_line_item_merchant_if_missing",
        lambda *args: calls.append(args),
    )
    monkeypatch.setattr(
        client, "add_receipt_line_items", lambda *_: pytest.fail("put")
    )

    assert (
        line_item_processor.backfill_line_item_merchants(IMAGE_ID, RECEIPT_ID)
        == 0
    )
    assert calls == []


def test_backfill_skips_a_row_corrected_between_read_and_write(
    client, monkeypatch
):
    """Codex's failing input: a concurrent correction must survive."""
    client.upsert_receipt_summary(summary(MERCHANT))
    client.add_receipt_line_items([row(0, merchant_name=None)])
    corrected = row(0, merchant_name="Target", price="7.00", name="FIXED")
    original = client.set_receipt_line_item_merchant_if_missing

    def correct_then_stamp(line_item, merchant):
        client.add_receipt_line_items([corrected])
        return original(line_item, merchant)

    monkeypatch.setattr(
        client, "set_receipt_line_item_merchant_if_missing", correct_then_stamp
    )

    stamped = line_item_processor.backfill_line_item_merchants(
        IMAGE_ID, RECEIPT_ID
    )

    assert stamped == 0
    assert stored(client)[0].to_item() == corrected.to_item()


def test_accessor_is_a_conditional_field_update(client):
    client.add_receipt_line_items(
        [row(0, merchant_name=None), row(1, merchant_name="Kept")]
    )
    bare, kept = stored(client)[0], stored(client)[1]

    assert client.set_receipt_line_item_merchant_if_missing(bare, MERCHANT)
    # Already stamped: the condition fails and nothing changes.
    assert not client.set_receipt_line_item_merchant_if_missing(
        bare, "Someone Else"
    )
    assert not client.set_receipt_line_item_merchant_if_missing(kept, MERCHANT)
    # A stale read (name changed underneath) is refused: the GSI1 sort key
    # derived from the old name must never land on the new row.
    stale = replace(kept, merchant_name=None, name="OLD NAME")
    assert not client.set_receipt_line_item_merchant_if_missing(
        stale, MERCHANT
    )
    after = stored(client)
    assert after[0].merchant_name == MERCHANT
    assert after[0].to_item()["GSI1PK"] == {
        "S": "MERCHANT#sprouts-farmers-market"
    }
    assert after[1].to_item() == kept.to_item()
    with pytest.raises(Exception, match="merchant_name"):
        client.set_receipt_line_item_merchant_if_missing(bare, "  ")


def test_backfill_is_idempotent(client):
    client.upsert_receipt_summary(summary(MERCHANT))
    client.add_receipt_line_items([row(0), row(1)])

    first = line_item_processor.backfill_line_item_merchants(
        IMAGE_ID, RECEIPT_ID
    )
    snapshot = {i: item.to_item() for i, item in stored(client).items()}
    second = line_item_processor.backfill_line_item_merchants(
        IMAGE_ID, RECEIPT_ID
    )

    assert (first, second) == (2, 0)
    assert {i: item.to_item() for i, item in stored(client).items()} == (
        snapshot
    )


def test_update_receipt_line_items_backfills_before_recomputing(
    client, monkeypatch
):
    client.upsert_receipt_summary(summary(MERCHANT))
    client.add_receipt_line_items([row(0), row(1, merchant_name=MERCHANT)])
    monkeypatch.setattr(client, "receipt_exists_consistent", lambda *_: True)
    seen = {}

    def fake_recompute(image_id, receipt_id, reocr_mechanism=None):
        # The recompute observes rows that already carry the merchant.
        seen["merchants"] = [
            item.merchant_name for item in stored(client).values()
        ]
        return {"items": 2}

    monkeypatch.setattr(
        line_item_processor, "_recompute_receipt_line_items", fake_recompute
    )

    result = line_item_processor.update_receipt_line_items(
        IMAGE_ID, RECEIPT_ID
    )

    assert result["merchant_backfilled"] == 1
    assert seen["merchants"] == [MERCHANT, MERCHANT]
    assert client.get_receipt_summary(IMAGE_ID, RECEIPT_ID).item_count == 2
