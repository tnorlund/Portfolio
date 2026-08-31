"""VECTOR_BACKEND retrieval swap, loaded without receipt_upload.__init__."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

from receipt_embeddings import ScoredItem

_RESOLVER = (
    Path(__file__).resolve().parents[2]
    / "receipt_upload"
    / "receipt_upload"
    / "merchant_resolution"
    / "resolver.py"
)


def _load_resolver():
    spec = importlib.util.spec_from_file_location(
        "merchant_resolver_round_c", _RESOLVER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dynamodb_backend_uses_search_not_chroma_query(monkeypatch) -> None:
    module = _load_resolver()
    monkeypatch.setenv("VECTOR_BACKEND", "dynamodb")
    resolver = module.MerchantResolver(dynamo_client=MagicMock())
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
    query_line = MagicMock()
    query_line.line_id = 1
    query_line.text = "Sprouts Farmers Market"
    resolver._receipt_lines = [query_line]
    place = MagicMock()
    place.place_id = "place-sprouts"
    place.merchant_name = "Sprouts Farmers Market"
    resolver.dynamo.get_receipt_place.return_value = place
    chroma = MagicMock()
    result = resolver._similarity_search_impl(
        lines_client=chroma,
        query_line=query_line,
        current_image_id="00000000-0000-4000-8000-000000000001",
        current_receipt_id=1,
        expected_phone=None,
        expected_address=None,
        resolution_tier="chroma_text",
    )
    chroma.query.assert_not_called()
    vector_client.search.assert_called_once()
    assert result.merchant_name == "Sprouts Farmers Market"


def test_chroma_default_still_queries(monkeypatch) -> None:
    module = _load_resolver()
    monkeypatch.delenv("VECTOR_BACKEND", raising=False)
    resolver = module.MerchantResolver(dynamo_client=MagicMock())
    resolver._line_embeddings = {1: [0.1] * 8}
    chroma = MagicMock()
    chroma.query.return_value = {"metadatas": [[]], "distances": [[]]}
    query_line = MagicMock()
    query_line.line_id = 1
    result = resolver._similarity_search_impl(
        lines_client=chroma,
        query_line=query_line,
        current_image_id="img",
        current_receipt_id=1,
        expected_phone=None,
        expected_address=None,
        resolution_tier="chroma_text",
    )
    chroma.query.assert_called_once()
    assert result.place_id is None


def test_dynamo_errors_degrade_to_empty(monkeypatch) -> None:
    module = _load_resolver()
    monkeypatch.setenv("VECTOR_BACKEND", "dynamo")
    resolver = module.MerchantResolver(dynamo_client=MagicMock())
    vector_client = MagicMock()
    vector_client.search.side_effect = RuntimeError("throttled")
    resolver._vector_client = vector_client
    resolver._line_embeddings = {1: [0.1] * 8}
    query_line = MagicMock()
    query_line.line_id = 1
    result = resolver._similarity_search_impl(
        lines_client=MagicMock(),
        query_line=query_line,
        current_image_id="img",
        current_receipt_id=1,
        expected_phone=None,
        expected_address=None,
        resolution_tier="chroma_text",
    )
    assert result.merchant_name is None
