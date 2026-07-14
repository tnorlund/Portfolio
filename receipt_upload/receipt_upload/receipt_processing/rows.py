"""Persist the canonical visual rows alongside newly created receipt OCR."""

from collections.abc import Sequence
from typing import Protocol

from receipt_chroma.embedding.formatting import build_receipt_rows
from receipt_dynamo.entities import ReceiptLine, ReceiptRow, ReceiptWord


class ReceiptRowWriter(Protocol):
    """Dynamo operation needed by the ingest hook."""

    def add_receipt_rows(self, receipt_rows: list[ReceiptRow]) -> None:
        """Persist a batch of materialized rows."""


def persist_receipt_rows(
    dynamo_client: ReceiptRowWriter,
    lines: Sequence[ReceiptLine],
    words: Sequence[ReceiptWord],
) -> None:
    """Build and write rows for one newly ingested receipt."""

    rows = build_receipt_rows(lines, words)
    if rows:
        dynamo_client.add_receipt_rows(rows)
