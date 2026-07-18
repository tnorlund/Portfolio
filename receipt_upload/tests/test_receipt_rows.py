"""Tests for replacing canonical rows during receipt creation."""

from unittest.mock import Mock, call, patch

import pytest

from receipt_upload.receipt_processing.rows import persist_receipt_rows


def test_persist_receipt_rows_writes_non_empty_result() -> None:
    dynamo = Mock()
    expected = [Mock(image_id="image-id", receipt_id=7)]
    dynamo.get_receipt_rows_from_receipt.return_value = []
    with patch(
        "receipt_upload.receipt_processing.rows.build_receipt_rows",
        return_value=expected,
    ) as build:
        persist_receipt_rows(dynamo, "image-id", 7, [Mock()], [Mock()])

    build.assert_called_once()
    dynamo.get_receipt_rows_from_receipt.assert_called_once_with("image-id", 7)
    dynamo.delete_receipt_rows.assert_not_called()
    dynamo.add_receipt_rows.assert_called_once_with(expected)


def test_persist_receipt_rows_deletes_stale_rows_before_writing() -> None:
    dynamo = Mock()
    stale = [Mock()]
    expected = [Mock(image_id="image-id", receipt_id=7)]
    dynamo.get_receipt_rows_from_receipt.return_value = stale
    with patch(
        "receipt_upload.receipt_processing.rows.build_receipt_rows",
        return_value=expected,
    ):
        persist_receipt_rows(dynamo, "image-id", 7, [Mock()], [Mock()])

    assert dynamo.method_calls == [
        call.get_receipt_rows_from_receipt("image-id", 7),
        call.delete_receipt_rows(stale),
        call.add_receipt_rows(expected),
    ]


def test_persist_receipt_rows_stops_when_stale_delete_fails() -> None:
    dynamo = Mock()
    dynamo.get_receipt_rows_from_receipt.return_value = [Mock()]
    dynamo.delete_receipt_rows.side_effect = RuntimeError("delete failed")
    expected = [Mock(image_id="image-id", receipt_id=7)]
    with (
        patch(
            "receipt_upload.receipt_processing.rows.build_receipt_rows",
            return_value=expected,
        ),
        pytest.raises(RuntimeError, match="delete failed"),
    ):
        persist_receipt_rows(dynamo, "image-id", 7, [Mock()], [Mock()])

    dynamo.add_receipt_rows.assert_not_called()


def test_persist_receipt_rows_empty_result_deletes_stale_rows() -> None:
    dynamo = Mock()
    stale = [Mock()]
    dynamo.get_receipt_rows_from_receipt.return_value = stale
    with patch(
        "receipt_upload.receipt_processing.rows.build_receipt_rows",
        return_value=[],
    ):
        persist_receipt_rows(dynamo, "image-id", 7, [], [])

    dynamo.get_receipt_rows_from_receipt.assert_called_once_with("image-id", 7)
    dynamo.delete_receipt_rows.assert_called_once_with(stale)
    dynamo.add_receipt_rows.assert_not_called()
