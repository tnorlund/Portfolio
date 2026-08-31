"""Idempotent embed-and-put writer tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import scripts.backfill_embedding_items as backfill
from receipt_dynamo.entities.receipt_line_embedding import (
    ReceiptLineEmbedding,
)
from receipt_dynamo.entities.receipt_word_embedding import (
    ReceiptWordEmbedding,
)
from scripts.backfill_embedding_items import _wait_searchable
from scripts.backfill_embedding_items import main as backfill_main

from receipt_embeddings import LINE_INDEX, WORD_INDEX, ScoredItem
from receipt_embeddings.writer import (
    WriteReport,
    owned_keys_in_hits,
    put_embedding_items,
)

_IMAGE = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"


def test_put_skips_existing_and_isolates_failures() -> None:
    dynamo = MagicMock()
    dynamo.put_receipt_line_embeddings_idempotent.side_effect = [
        RuntimeError("batch boom"),
        {
            "written": 1,
            "skipped": 0,
            "written_keys": [f"IMAGE#{_IMAGE}#RECEIPT#00001#LINE#00001"],
            "skipped_keys": [],
        },
        RuntimeError("item boom"),
    ]
    dynamo.put_receipt_word_embeddings_idempotent.return_value = {
        "written": 0,
        "skipped": 0,
        "written_keys": [],
        "skipped_keys": [],
    }
    items = [
        ReceiptLineEmbedding(
            image_id=_IMAGE,
            receipt_id=1,
            line_id=1,
            line_vector=[0.1],
        ),
        ReceiptLineEmbedding(
            image_id=_IMAGE,
            receipt_id=1,
            line_id=2,
            line_vector=[0.2],
        ),
    ]
    report = put_embedding_items(dynamo, items)
    assert report.written == 1
    assert report.written_keys == [f"IMAGE#{_IMAGE}#RECEIPT#00001#LINE#00001"]
    assert len(report.failed) == 1
    assert "LINE#00002" in report.failed[0]


def test_write_report_merge() -> None:
    left = WriteReport(
        written=2,
        skipped=3,
        failed=["a"],
        written_keys=["k1"],
        skipped_keys=["s1"],
    )
    left.merge(
        WriteReport(
            written=1,
            skipped=4,
            failed=["b"],
            written_keys=["k2"],
            skipped_keys=["s2"],
        )
    )
    assert left.written == 3
    assert left.skipped == 7
    assert left.failed == ["a", "b"]
    assert left.written_keys == ["k1", "k2"]
    assert left.skipped_keys == ["s1", "s2"]


def test_put_records_skipped_keys_from_accessor() -> None:
    key = f"IMAGE#{_IMAGE}#RECEIPT#00001#LINE#00001"
    dynamo = MagicMock()
    dynamo.put_receipt_line_embeddings_idempotent.return_value = {
        "written": 0,
        "skipped": 1,
        "written_keys": [],
        "skipped_keys": [key],
    }
    dynamo.put_receipt_word_embeddings_idempotent.return_value = {
        "written": 0,
        "skipped": 0,
        "written_keys": [],
        "skipped_keys": [],
    }
    report = put_embedding_items(
        dynamo,
        [
            ReceiptLineEmbedding(
                image_id=_IMAGE,
                receipt_id=1,
                line_id=1,
                line_vector=[0.1],
            )
        ],
    )
    assert report.written == 0
    assert report.skipped == 1
    assert report.written_keys == []
    assert report.skipped_keys == [key]


def test_owned_keys_ignore_foreign_search_hits() -> None:
    ours = [
        "IMAGE#ours#RECEIPT#00001#LINE#00001",
        "IMAGE#ours#RECEIPT#00001#LINE#00002",
    ]
    hits = [
        ScoredItem(
            key="IMAGE#foreign#RECEIPT#00009#LINE#00001", distance=0.01
        ),
        ScoredItem(key=ours[0], distance=0.02),
        ScoredItem(
            key="IMAGE#foreign#RECEIPT#00008#LINE#00003", distance=0.03
        ),
        ScoredItem(key=ours[1], distance=0.04),
    ]
    found = owned_keys_in_hits(hits, ours)
    assert found == ours
    assert "foreign" not in "".join(found)


def test_wait_searchable_ignores_foreign_neighbors(monkeypatch) -> None:
    line = ReceiptLineEmbedding(
        image_id=_IMAGE,
        receipt_id=1,
        line_id=1,
        line_vector=[0.1] * 3,
    )
    word = ReceiptWordEmbedding(
        image_id=_IMAGE,
        receipt_id=1,
        line_id=1,
        word_id=1,
        word_vector=[0.2] * 3,
    )
    client = MagicMock()

    def _search(_vector, index, top_k, filters=None):
        foreign = ScoredItem(
            key="IMAGE#foreign#RECEIPT#00009#LINE#00001", distance=0.0
        )
        if index == LINE_INDEX:
            return [foreign, ScoredItem(key=line.harness_key(), distance=0.0)]
        assert index == WORD_INDEX
        return [foreign, ScoredItem(key=word.harness_key(), distance=0.01)]

    client.search.side_effect = _search

    class _Fake:
        @classmethod
        def from_env(cls):
            return client

    clock = {"t": 0.0}

    def _advance(seconds: float) -> None:
        clock["t"] += seconds

    monkeypatch.setattr(backfill.time, "time", lambda: clock["t"])
    monkeypatch.setattr(backfill.time, "sleep", _advance)
    monkeypatch.setattr(
        "receipt_embeddings.dynamo_client.DynamoVectorSearchClient",
        _Fake,
    )
    result = _wait_searchable([line, word], timeout_s=5.0)
    assert result["ok"] is True
    assert line.harness_key() in result["searchable_keys"]
    assert word.harness_key() in result["searchable_keys"]
    assert result["pending_keys"] == []
    assert all("foreign" not in key for key in result["searchable_keys"])


def test_wait_searchable_does_not_use_hit_counts(monkeypatch) -> None:
    ours = ReceiptLineEmbedding(
        image_id=_IMAGE,
        receipt_id=1,
        line_id=1,
        line_vector=[0.1] * 3,
    )
    client = MagicMock()
    client.search.return_value = [
        ScoredItem(key="IMAGE#foreign#RECEIPT#00009#LINE#00001", distance=0.0),
        ScoredItem(key="IMAGE#foreign#RECEIPT#00008#LINE#00002", distance=0.1),
        ScoredItem(key=ours.harness_key(), distance=0.2),
    ]

    class _Fake:
        @classmethod
        def from_env(cls):
            return client

    monkeypatch.setattr(backfill.time, "time", lambda: 0.0)
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "receipt_embeddings.dynamo_client.DynamoVectorSearchClient",
        _Fake,
    )
    result = _wait_searchable([ours], timeout_s=5.0)
    assert result["ok"] is True
    assert result["searchable_keys"] == [ours.harness_key()]
    assert len(client.search.return_value) == 3  # extra neighbors exist
    assert result["searchable_keys"] != [
        hit.key for hit in client.search.return_value
    ]


def test_backfill_refuses_prod(monkeypatch) -> None:
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "ReceiptsTable-d7ff76a")
    with pytest.raises(SystemExit, match="prod"):
        backfill_main(["--limit", "1", "--allow-under-floor"])


def test_backfill_dry_run_does_not_need_aws(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    assert (
        backfill_main(["--limit", "1", "--allow-under-floor", "--dry-run"])
        == 0
    )
    payload = capsys.readouterr().out
    assert "dry_run" in payload
    assert "ReceiptsTable-dc5be22" in payload
