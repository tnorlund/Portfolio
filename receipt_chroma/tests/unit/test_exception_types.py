"""Focused tests for receipt-chroma's public failure contract."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from chromadb.errors import NotFoundError

from receipt_chroma import ChromaClient
from receipt_chroma.exceptions import (
    ChromaClientClosedError,
    ChromaCollectionNotFoundError,
    ChromaDeltaUploadError,
    ChromaReadOnlyError,
)


def test_closed_client_raises_exact_state_error() -> None:
    client = ChromaClient(mode="write", metadata_only=True)
    client._closed = True  # pylint: disable=protected-access

    with pytest.raises(ChromaClientClosedError) as caught:
        client.get_collection("receipts")

    assert type(caught.value) is ChromaClientClosedError
    assert str(caught.value) == "Cannot use closed ChromaClient"
    assert caught.value.__cause__ is None


def test_write_on_read_only_client_raises_exact_state_error() -> None:
    client = ChromaClient(mode="read")

    with pytest.raises(ChromaReadOnlyError) as caught:
        client.upsert("receipts", ["receipt-1"])

    assert type(caught.value) is ChromaReadOnlyError
    assert str(caught.value) == "This client is read-only (mode='read')"
    assert caught.value.__cause__ is None


def test_missing_collection_preserves_chroma_cause() -> None:
    client = ChromaClient(mode="write", metadata_only=True)
    chroma_client = Mock()
    cause = NotFoundError("collection is absent")
    chroma_client.get_collection.side_effect = cause
    client._client = chroma_client  # pylint: disable=protected-access

    with pytest.raises(ChromaCollectionNotFoundError) as caught:
        client.get_collection("receipts")

    assert type(caught.value) is ChromaCollectionNotFoundError
    assert str(caught.value) == (
        "Collection 'receipts' not found and create_if_missing=False"
    )
    assert caught.value.collection_name == "receipts"
    assert caught.value.__cause__ is cause
    assert isinstance(caught.value, ValueError)


def test_delta_upload_error_includes_s3_target_and_cause(tmp_path) -> None:
    (tmp_path / "chroma.sqlite3").write_bytes(b"delta")
    client = ChromaClient(
        persist_directory=str(tmp_path), mode="delta", metadata_only=True
    )
    client._closed = True  # pylint: disable=protected-access
    s3 = Mock()
    cause = OSError("connection reset")
    s3.upload_file.side_effect = cause

    with patch("uuid.uuid4", return_value=SimpleNamespace(hex="delta123")):
        with pytest.raises(ChromaDeltaUploadError) as caught:
            client.persist_and_upload_delta(
                "receipt-bucket", "deltas", s3_client=s3
            )

    assert type(caught.value) is ChromaDeltaUploadError
    assert str(caught.value) == (
        "Failed to upload Chroma delta file to "
        "s3://receipt-bucket/deltas/delta123/chroma.sqlite3"
    )
    assert caught.value.bucket == "receipt-bucket"
    assert caught.value.key == "deltas/delta123/chroma.sqlite3"
    assert caught.value.__cause__ is cause
