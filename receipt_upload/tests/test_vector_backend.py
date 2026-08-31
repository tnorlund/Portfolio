"""VECTOR_BACKEND swaps retrieval only; thresholds stay on the chroma path."""

from __future__ import annotations

from unittest.mock import MagicMock

from receipt_embeddings import ScoredItem
from receipt_upload.merchant_resolution.resolver import MerchantResolver

TEST_IMAGE_ID = "00000000-0000-4000-8000-000000000001"


def _line() -> MagicMock:
    line = MagicMock()
    line.line_id = 1
    line.text = "Sprouts Farmers Market"
    return line


def test_dynamodb_backend_uses_search_not_chroma_query(monkeypatch) -> None:
    monkeypatch.setenv("VECTOR_BACKEND", "dynamodb")
    resolver = MerchantResolver(dynamo_client=MagicMock())
    vector_client = MagicMock()
    vector_client.search.return_value = [
        ScoredItem(
            key="IMAGE#other#RECEIPT#00002#LINE#00001",
            distance=0.1,
            metadata={
                "image_id": "other",
                "receipt_id": 2,
                "merchant_name": "Sprouts Farmers Market",
                "place_id": "place-sprouts",
            },
        )
    ]
    resolver._vector_client = vector_client
    resolver._line_embeddings = {1: [0.1] * 8}
    query_line = _line()
    resolver._receipt_lines = [query_line]
    place = MagicMock()
    place.place_id = "place-sprouts"
    place.merchant_name = "Sprouts Farmers Market"
    resolver.dynamo.get_receipt_place.return_value = place
    chroma = MagicMock()
    result = resolver._similarity_search_impl(
        lines_client=chroma,
        query_line=query_line,
        current_image_id=TEST_IMAGE_ID,
        current_receipt_id=1,
        expected_phone=None,
        expected_address=None,
        resolution_tier="chroma_text",
    )
    chroma.query.assert_not_called()
    vector_client.search.assert_called_once()
    assert result.merchant_name == "Sprouts Farmers Market"


def test_chroma_backend_still_queries_lines_client(monkeypatch) -> None:
    monkeypatch.delenv("VECTOR_BACKEND", raising=False)
    resolver = MerchantResolver(dynamo_client=MagicMock())
    resolver._line_embeddings = {1: [0.1] * 8}
    chroma = MagicMock()
    chroma.query.return_value = {"metadatas": [[]], "distances": [[]]}
    result = resolver._similarity_search_impl(
        lines_client=chroma,
        query_line=_line(),
        current_image_id=TEST_IMAGE_ID,
        current_receipt_id=1,
        expected_phone=None,
        expected_address=None,
        resolution_tier="chroma_text",
    )
    chroma.query.assert_called_once()
    assert result.place_id is None


def test_dynamo_throttle_degrades_to_empty(monkeypatch) -> None:
    monkeypatch.setenv("VECTOR_BACKEND", "dynamo")
    resolver = MerchantResolver(dynamo_client=MagicMock())
    vector_client = MagicMock()
    vector_client.search.side_effect = RuntimeError("throttled")
    resolver._vector_client = vector_client
    resolver._line_embeddings = {1: [0.1] * 8}
    result = resolver._similarity_search_impl(
        lines_client=MagicMock(),
        query_line=_line(),
        current_image_id=TEST_IMAGE_ID,
        current_receipt_id=1,
        expected_phone=None,
        expected_address=None,
        resolution_tier="chroma_text",
    )
    assert result.place_id is None
    assert result.merchant_name is None
