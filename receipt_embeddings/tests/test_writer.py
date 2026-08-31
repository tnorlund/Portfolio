"""Idempotent embed-and-put writer tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from receipt_dynamo.entities.receipt_line_embedding import (
    ReceiptLineEmbedding,
)
from scripts.backfill_embedding_items import main as backfill_main

from receipt_embeddings.writer import WriteReport, put_embedding_items

_IMAGE = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"


def test_put_skips_existing_and_isolates_failures() -> None:
    dynamo = MagicMock()
    dynamo.put_receipt_line_embeddings_idempotent.side_effect = [
        RuntimeError("batch boom"),
        {"written": 1, "skipped": 0},
        RuntimeError("item boom"),
    ]
    dynamo.put_receipt_word_embeddings_idempotent.return_value = {
        "written": 0,
        "skipped": 0,
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
    assert len(report.failed) == 1
    assert "LINE#00002" in report.failed[0]


def test_write_report_merge() -> None:
    left = WriteReport(written=2, skipped=3, failed=["a"])
    left.merge(WriteReport(written=1, skipped=4, failed=["b"]))
    assert left.written == 3
    assert left.skipped == 7
    assert left.failed == ["a", "b"]


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
