"""Persist the canonical visual rows alongside newly created receipt OCR."""

from collections.abc import Sequence
from typing import Protocol

from receipt_chroma.embedding.formatting import build_receipt_rows
from receipt_dynamo.entities import ReceiptLine, ReceiptRow, ReceiptWord


class ReceiptRowWriter(Protocol):
    """Dynamo operation needed by the ingest hook."""

    def get_receipt_rows_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptRow]:
        """Return the rows currently persisted for one receipt."""

    def delete_receipt_rows(self, receipt_rows: list[ReceiptRow]) -> None:
        """Delete a batch of previously materialized rows."""

    def add_receipt_rows(self, receipt_rows: list[ReceiptRow]) -> None:
        """Persist a batch of materialized rows."""


def persist_receipt_rows(
    dynamo_client: ReceiptRowWriter,
    image_id: str,
    receipt_id: int,
    lines: Sequence[ReceiptLine],
    words: Sequence[ReceiptWord],
) -> None:
    """Replace one receipt's rows with its newly materialized grouping.

    The delete precedes the put so stale row identities cannot survive a
    regrouping. Dynamo failures intentionally propagate before ingest can mark
    OCR complete; a failed replacement must be retried or repaired before its
    rows are consumed downstream.
    """

    rows = build_receipt_rows(lines, words)
    if any(
        row.image_id != image_id or row.receipt_id != receipt_id
        for row in rows
    ):
        raise ValueError("materialized rows do not match the target receipt")
    previous_rows = dynamo_client.get_receipt_rows_from_receipt(
        image_id, receipt_id
    )
    if previous_rows:
        dynamo_client.delete_receipt_rows(previous_rows)
    if rows:
        dynamo_client.add_receipt_rows(rows)
