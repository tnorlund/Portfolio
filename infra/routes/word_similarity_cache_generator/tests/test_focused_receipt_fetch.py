"""Tests for focused receipt reads in the milk cache generator."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import boto3
import pytest


def _load_handler(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load the Lambda module without initializing production clients."""
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "test-table")
    monkeypatch.setenv("CHROMADB_BUCKET", "test-chroma-bucket")
    monkeypatch.setattr(boto3, "client", MagicMock())

    receipt_chroma_module = ModuleType("receipt_chroma")
    receipt_chroma_module.__path__ = []
    setattr(receipt_chroma_module, "ChromaClient", MagicMock())
    monkeypatch.setitem(sys.modules, "receipt_chroma", receipt_chroma_module)

    compaction_module = ModuleType("receipt_chroma.compaction")
    compaction_module.__path__ = []
    setattr(compaction_module, "CloudConfig", MagicMock())
    monkeypatch.setitem(
        sys.modules, "receipt_chroma.compaction", compaction_module
    )

    chroma_s3_module = ModuleType("receipt_chroma.s3")
    setattr(chroma_s3_module, "download_snapshot_atomic", MagicMock())
    monkeypatch.setitem(sys.modules, "receipt_chroma.s3", chroma_s3_module)

    receipt_dynamo_module = ModuleType("receipt_dynamo")
    setattr(receipt_dynamo_module, "DynamoClient", MagicMock())
    monkeypatch.setitem(sys.modules, "receipt_dynamo", receipt_dynamo_module)

    handler_path = Path(__file__).parents[1] / "lambdas" / "index.py"
    spec = importlib.util.spec_from_file_location(
        "word_similarity_cache_generator", handler_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_row_line_ids_prefers_visual_row_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    row_line_ids = handler.parse_row_line_ids(
        {"line_id": 4, "row_line_ids": "[4, 7, 7]"}
    )

    assert row_line_ids == [4, 7]
    assert handler.parse_row_line_ids({"line_id": 9}) == [9]


def test_add_line_context_includes_nearby_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)

    assert handler.add_line_context([1, 3], radius=1) == [0, 1, 2, 3, 4]


def test_merge_row_line_ids_preserves_all_visual_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)

    assert handler.merge_row_line_ids(
        [
            {"line_id": 42, "row_line_ids": "[42, 51]"},
            {"line_id": 48, "row_line_ids": "[48, 43]"},
        ]
    ) == [42, 51, 48, 43]


def test_s3_snapshot_queries_only_target_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    collection = MagicMock()
    collection.get.return_value = {"ids": ["row-1"], "metadatas": [{}]}
    client = MagicMock()
    client.get_collection.return_value = collection
    handler.ChromaClient = MagicMock(return_value=client)
    handler.download_snapshot_atomic.return_value = {"status": "downloaded"}

    result = handler._fetch_lines_from_s3(handler.TimingStats(), "/tmp/chroma")

    assert result["ids"] == ["row-1"]
    handler.ChromaClient.assert_called_once_with(
        persist_directory="/tmp/chroma",
        mode="read",
        metadata_only=True,
    )
    collection.get.assert_called_once_with(
        where_document={"$contains": "MILK"},
        include=["metadatas"],
    )


def test_find_milk_line_limits_candidates_but_detects_void_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    lines = [
        SimpleNamespace(line_id=8, text="OTHER MILK"),
        SimpleNamespace(line_id=10, text="RAW WHOLE MILK"),
        SimpleNamespace(line_id=11, text="VOID"),
    ]

    assert (
        handler.find_milk_line(
            lines,
            candidate_line_ids={10},
        )
        is None
    )
    assert handler.find_milk_line(
        lines[:2],
        candidate_line_ids={10},
    ) == ("RAW WHOLE MILK", 10)
