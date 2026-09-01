"""Moto-backed tests for the vector freshening leg (SPEC §3.4a).

Stream records are built from real entity ``to_item()`` images; the
DynamoDB table (queries + conditional updates) runs under moto.
"""

from datetime import datetime
from typing import Any, Iterator

import boto3
import pytest
from moto import mock_aws

from receipt_dynamo.entities.receipt_embedding import (
    ReceiptLineEmbedding,
    ReceiptWordEmbedding,
)
from receipt_dynamo.entities.receipt_place import ReceiptPlace
from receipt_dynamo.entities.receipt_section import ReceiptSection
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_dynamo_stream import apply_vector_freshening

_IMAGE_ID = "550e8400-e29b-41d4-a716-446655440000"
_PK = f"IMAGE#{_IMAGE_ID}"
_TABLE = "ReceiptData"
_VECTOR = [0.001] * 1536


@pytest.fixture(name="dynamo")
def _dynamo() -> Iterator[Any]:
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=_TABLE,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        client.get_waiter("table_exists").wait(TableName=_TABLE)
        yield client


def _line_embedding(
    line_id: int,
    row_line_ids: list[int],
    section_type: str = "",
) -> ReceiptLineEmbedding:
    return ReceiptLineEmbedding(
        image_id=_IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        text="COFFEE 4.50",
        merchant_name="Cafe Nero",
        place_id="place123",
        row_line_ids=row_line_ids,
        section_type=section_type,
        line_vector=list(_VECTOR),
        normalized_phone_10="5551234567",
        normalized_full_address="123 main st",
    )


def _word_embedding(
    line_id: int = 1, word_id: int = 1, label_status: str = "none"
) -> ReceiptWordEmbedding:
    return ReceiptWordEmbedding(
        image_id=_IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        text="COFFEE",
        merchant_name="Cafe Nero",
        label_status=label_status,
        word_vector=list(_VECTOR),
    )


def _place(
    merchant_name: str = "Cafe Nero", place_id: str = "place123"
) -> ReceiptPlace:
    return ReceiptPlace(
        image_id=_IMAGE_ID,
        receipt_id=1,
        place_id=place_id,
        merchant_name=merchant_name,
        formatted_address="123 Main St",
        phone_number="555-123-4567",
        matched_fields=["name"],
        validated_by="INFERENCE",
        timestamp=datetime.fromisoformat("2024-01-01T00:00:00"),
    )


def _word_label(
    word_id: int = 1,
    label: str = "PRODUCT_NAME",
    validation_status: str = "VALID",
) -> ReceiptWordLabel:
    return ReceiptWordLabel(
        image_id=_IMAGE_ID,
        receipt_id=1,
        line_id=1,
        word_id=word_id,
        label=label,
        reasoning="test",
        timestamp_added=datetime.fromisoformat("2024-01-01T00:00:00"),
        validation_status=validation_status,
    )


def _section(
    line_ids: list[int], section_type: str = "ITEMS"
) -> ReceiptSection:
    return ReceiptSection(
        receipt_id=1,
        image_id=_IMAGE_ID,
        section_type=section_type,
        line_ids=line_ids,
        created_at=datetime.fromisoformat("2024-01-01T00:00:00"),
    )


def _record(
    event_name: str,
    new_item: dict[str, Any] | None = None,
    old_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    keyed = new_item or old_item
    assert keyed is not None
    dynamodb: dict[str, Any] = {"Keys": {"PK": keyed["PK"], "SK": keyed["SK"]}}
    if new_item is not None:
        dynamodb["NewImage"] = new_item
    if old_item is not None:
        dynamodb["OldImage"] = old_item
    return {
        "eventID": "evt-1",
        "eventName": event_name,
        "awsRegion": "us-east-1",
        "dynamodb": dynamodb,
    }


def _get_item(dynamo: Any, sk: str) -> dict[str, Any] | None:
    response = dynamo.get_item(
        TableName=_TABLE, Key={"PK": {"S": _PK}, "SK": {"S": sk}}
    )
    return response.get("Item")


def _seed(dynamo: Any, *items: dict[str, Any]) -> None:
    for item in items:
        dynamo.put_item(TableName=_TABLE, Item=item)


class TestPlaceFreshening:
    def test_modify_updates_all_line_embeddings(self, dynamo: Any) -> None:
        _seed(
            dynamo,
            _line_embedding(1, [1, 2]).to_item(),
            _line_embedding(3, [3]).to_item(),
            _word_embedding().to_item(),
        )
        record = _record(
            "MODIFY",
            new_item=_place("Blue Bottle", "place456").to_item(),
            old_item=_place().to_item(),
        )

        stats = apply_vector_freshening(
            [record], dynamo_client=dynamo, table_name=_TABLE
        )

        assert stats.updates_applied == 2
        assert stats.errors == 0
        for line_id in (1, 3):
            item = _get_item(
                dynamo, f"RECEIPT#00001#LINE#{line_id:05d}#EMBEDDING"
            )
            assert item is not None
            assert item["merchant_name"]["S"] == "Blue Bottle"
            assert item["place_id"]["S"] == "place456"
            # Anchors derive from word extracted_data, not place fields:
            # a place change must not touch them.
            assert item["normalized_phone_10"]["S"] == "5551234567"
        # Word embeddings are not a place-freshening target (SPEC §3.4a).
        word_item = _get_item(
            dynamo, "RECEIPT#00001#LINE#00001#WORD#00001#EMBEDDING"
        )
        assert word_item is not None
        assert word_item["merchant_name"]["S"] == "Cafe Nero"

    def test_is_idempotent(self, dynamo: Any) -> None:
        _seed(dynamo, _line_embedding(1, [1]).to_item())
        record = _record(
            "MODIFY",
            new_item=_place("Blue Bottle").to_item(),
            old_item=_place().to_item(),
        )

        first = apply_vector_freshening(
            [record], dynamo_client=dynamo, table_name=_TABLE
        )
        item_after_first = _get_item(
            dynamo, "RECEIPT#00001#LINE#00001#EMBEDDING"
        )
        second = apply_vector_freshening(
            [record], dynamo_client=dynamo, table_name=_TABLE
        )
        item_after_second = _get_item(
            dynamo, "RECEIPT#00001#LINE#00001#EMBEDDING"
        )

        assert first.updates_applied == second.updates_applied == 1
        assert item_after_first == item_after_second

    def test_insert_with_no_embeddings_is_clean(self, dynamo: Any) -> None:
        record = _record("INSERT", new_item=_place().to_item())

        stats = apply_vector_freshening(
            [record], dynamo_client=dynamo, table_name=_TABLE
        )

        assert stats.updates_applied == 0
        assert stats.errors == 0

    def test_query_excludes_non_embedding_items(self, dynamo: Any) -> None:
        """Words/labels under the LINE# prefix must not be updated."""
        label_item = _word_label().to_item()
        _seed(dynamo, label_item)
        record = _record(
            "MODIFY",
            new_item=_place("Blue Bottle").to_item(),
            old_item=_place().to_item(),
        )

        stats = apply_vector_freshening(
            [record], dynamo_client=dynamo, table_name=_TABLE
        )

        assert stats.updates_applied == 0
        untouched = _get_item(dynamo, label_item["SK"]["S"])
        assert untouched is not None
        assert "merchant_name" not in untouched


class TestWordLabelFreshening:
    def test_valid_label_marks_word_validated(self, dynamo: Any) -> None:
        label = _word_label(validation_status="VALID")
        _seed(dynamo, _word_embedding().to_item(), label.to_item())
        record = _record("INSERT", new_item=label.to_item())

        stats = apply_vector_freshening(
            [record], dynamo_client=dynamo, table_name=_TABLE
        )

        assert stats.updates_applied == 1
        item = _get_item(
            dynamo, "RECEIPT#00001#LINE#00001#WORD#00001#EMBEDDING"
        )
        assert item is not None
        assert item["label_status"]["S"] == "validated"

    def test_status_aggregates_all_labels_for_word(self, dynamo: Any) -> None:
        """A PENDING change must not demote a word with a VALID label."""
        valid_label = _word_label(label="PRODUCT_NAME")
        pending_label = _word_label(
            label="MERCHANT_NAME", validation_status="PENDING"
        )
        _seed(
            dynamo,
            _word_embedding().to_item(),
            valid_label.to_item(),
            pending_label.to_item(),
        )
        record = _record("INSERT", new_item=pending_label.to_item())

        apply_vector_freshening(
            [record], dynamo_client=dynamo, table_name=_TABLE
        )

        item = _get_item(
            dynamo, "RECEIPT#00001#LINE#00001#WORD#00001#EMBEDDING"
        )
        assert item is not None
        assert item["label_status"]["S"] == "validated"

    def test_remove_recomputes_from_remaining_labels(
        self, dynamo: Any
    ) -> None:
        label = _word_label(validation_status="VALID")
        _seed(
            dynamo,
            _word_embedding(label_status="validated").to_item(),
        )
        # The label item is already gone when the REMOVE record arrives.
        record = _record("REMOVE", old_item=label.to_item())

        stats = apply_vector_freshening(
            [record], dynamo_client=dynamo, table_name=_TABLE
        )

        assert stats.updates_applied == 1
        item = _get_item(
            dynamo, "RECEIPT#00001#LINE#00001#WORD#00001#EMBEDDING"
        )
        assert item is not None
        assert item["label_status"]["S"] == "none"

    def test_missing_embedding_is_skipped_never_created(
        self, dynamo: Any
    ) -> None:
        label = _word_label(word_id=99)
        _seed(dynamo, label.to_item())
        record = _record("INSERT", new_item=label.to_item())

        stats = apply_vector_freshening(
            [record], dynamo_client=dynamo, table_name=_TABLE
        )

        assert stats.updates_applied == 0
        assert stats.missing_embeddings == 1
        assert stats.errors == 0
        assert (
            _get_item(dynamo, "RECEIPT#00001#LINE#00001#WORD#00099#EMBEDDING")
            is None
        )

    def test_irrelevant_modify_is_ignored(self, dynamo: Any) -> None:
        """Unchanged validation_status (e.g. reasoning edit): no work."""
        label = _word_label()
        _seed(dynamo, _word_embedding().to_item(), label.to_item())
        record = _record(
            "MODIFY", new_item=label.to_item(), old_item=label.to_item()
        )

        stats = apply_vector_freshening(
            [record], dynamo_client=dynamo, table_name=_TABLE
        )

        assert stats.updates_applied == 0


class TestSectionFreshening:
    def test_insert_stamps_primary_lines_and_skips_others(
        self, dynamo: Any
    ) -> None:
        # Rows [1,2] and [3]: embeddings exist at primary lines 1 and 3
        # only; line 2 is a non-primary row member.
        _seed(
            dynamo,
            _line_embedding(1, [1, 2]).to_item(),
            _line_embedding(3, [3]).to_item(),
        )
        section = _section([1, 2, 3])
        record = _record("INSERT", new_item=section.to_item())

        stats = apply_vector_freshening(
            [record], dynamo_client=dynamo, table_name=_TABLE
        )

        assert stats.updates_applied == 2
        assert stats.missing_embeddings == 1
        for line_id in (1, 3):
            item = _get_item(
                dynamo, f"RECEIPT#00001#LINE#{line_id:05d}#EMBEDDING"
            )
            assert item is not None
            assert item["section_type"]["S"] == "ITEMS"
        assert _get_item(dynamo, "RECEIPT#00001#LINE#00002#EMBEDDING") is None

    def test_modify_clears_lines_dropped_from_section(
        self, dynamo: Any
    ) -> None:
        _seed(
            dynamo,
            _line_embedding(1, [1], section_type="ITEMS").to_item(),
            _line_embedding(3, [3], section_type="ITEMS").to_item(),
        )
        record = _record(
            "MODIFY",
            new_item=_section([1]).to_item(),
            old_item=_section([1, 3]).to_item(),
        )

        stats = apply_vector_freshening(
            [record], dynamo_client=dynamo, table_name=_TABLE
        )

        assert stats.updates_applied == 2
        kept = _get_item(dynamo, "RECEIPT#00001#LINE#00001#EMBEDDING")
        dropped = _get_item(dynamo, "RECEIPT#00001#LINE#00003#EMBEDDING")
        assert kept is not None and kept["section_type"]["S"] == "ITEMS"
        assert dropped is not None and dropped["section_type"]["S"] == ""

    def test_remove_clears_section_membership(self, dynamo: Any) -> None:
        _seed(
            dynamo,
            _line_embedding(1, [1], section_type="ITEMS").to_item(),
        )
        record = _record("REMOVE", old_item=_section([1]).to_item())

        stats = apply_vector_freshening(
            [record], dynamo_client=dynamo, table_name=_TABLE
        )

        assert stats.updates_applied == 1
        item = _get_item(dynamo, "RECEIPT#00001#LINE#00001#EMBEDDING")
        assert item is not None
        assert item["section_type"]["S"] == ""

    def test_unchanged_modify_is_ignored(self, dynamo: Any) -> None:
        _seed(dynamo, _line_embedding(1, [1]).to_item())
        section = _section([1])
        record = _record(
            "MODIFY",
            new_item=section.to_item(),
            old_item=section.to_item(),
        )

        stats = apply_vector_freshening(
            [record], dynamo_client=dynamo, table_name=_TABLE
        )

        assert stats.updates_applied == 0
