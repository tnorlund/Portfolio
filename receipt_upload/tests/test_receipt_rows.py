"""Tests for persisting canonical rows during receipt creation."""

from unittest.mock import Mock, patch

from receipt_upload.receipt_processing.rows import persist_receipt_rows


def test_persist_receipt_rows_writes_non_empty_result() -> None:
    dynamo = Mock()
    expected = [Mock()]
    with patch(
        "receipt_upload.receipt_processing.rows.build_receipt_rows",
        return_value=expected,
    ) as build:
        persist_receipt_rows(dynamo, [Mock()], [Mock()])

    build.assert_called_once()
    dynamo.add_receipt_rows.assert_called_once_with(expected)


def test_persist_receipt_rows_skips_empty_result() -> None:
    dynamo = Mock()
    with patch(
        "receipt_upload.receipt_processing.rows.build_receipt_rows",
        return_value=[],
    ):
        persist_receipt_rows(dynamo, [], [])

    dynamo.add_receipt_rows.assert_not_called()
