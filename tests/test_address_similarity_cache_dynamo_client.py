"""Address-cache Dynamo lines client: 2× cosine scale and key parsing."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import boto3
import pytest
from receipt_embeddings.keys import (
    embedding_item_key,
    line_canonical_key,
)

IMAGE_ID = "2f1e7204-84f1-4ab3-9b05-7dc6edebc1b7"


def _load_handler(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "test-table")
    monkeypatch.setenv("CHROMADB_BUCKET", "test-chroma-bucket")
    monkeypatch.setattr(boto3, "client", MagicMock())

    receipt_chroma_module = ModuleType("receipt_chroma")
    receipt_chroma_module.__path__ = []
    setattr(receipt_chroma_module, "ChromaClient", MagicMock())
    monkeypatch.setitem(sys.modules, "receipt_chroma", receipt_chroma_module)

    chroma_s3_module = ModuleType("receipt_chroma.s3")
    setattr(chroma_s3_module, "download_snapshot_atomic", MagicMock())
    monkeypatch.setitem(sys.modules, "receipt_chroma.s3", chroma_s3_module)

    receipt_dynamo_module = ModuleType("receipt_dynamo")
    setattr(receipt_dynamo_module, "DynamoClient", MagicMock())
    monkeypatch.setitem(sys.modules, "receipt_dynamo", receipt_dynamo_module)

    handler_path = (
        Path(__file__).resolve().parents[1]
        / "infra"
        / "routes"
        / "address_similarity_cache_generator"
        / "lambdas"
        / "index.py"
    )
    spec = importlib.util.spec_from_file_location(
        "address_similarity_cache_generator", handler_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_query_converts_cosine_to_chroma_squared_l2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_handler(monkeypatch)
    client = module._DynamoLinesClient("test-table")
    key = embedding_item_key(IMAGE_ID, 1, 2)
    fake = SimpleNamespace(
        search_vectors=lambda **_kwargs: {
            "SearchResults": [
                {"Item": key, "Score": 0.25},
                {"Item": key, "Score": None},
                {
                    "Item": {"PK": {"S": "IMAGE#x"}, "SK": {"S": "nope"}},
                    "Score": 0.1,
                },
            ]
        }
    )
    client._client = fake

    result = client.query(
        collection_name="lines",
        query_embeddings=[[0.1, 0.2]],
        n_results=8,
    )

    assert result["metadatas"] == [[{"image_id": IMAGE_ID, "receipt_id": 1}]]
    assert result["distances"] == [[0.5]]
    assert result["ids"] == [[f"{key['PK']['S']}#{key['SK']['S']}"]]


def test_get_returns_line_vector_for_canonical_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_handler(monkeypatch)
    client = module._DynamoLinesClient("test-table")
    canonical = line_canonical_key(IMAGE_ID, 1, 2)
    seen = {}

    def get_item(**kwargs):
        seen["key"] = kwargs["Key"]
        return {
            "Item": {
                "line_vector": {"L": [{"N": "0.5"}, {"N": "-0.25"}]},
            }
        }

    client._client = SimpleNamespace(get_item=get_item)
    result = client.get(collection_name="lines", ids=[canonical])

    assert seen["key"] == embedding_item_key(IMAGE_ID, 1, 2)
    assert result["ids"] == [canonical]
    assert result["embeddings"] == [[0.5, -0.25]]
    assert client.list_collections() == ["lines"]
    client.close()
