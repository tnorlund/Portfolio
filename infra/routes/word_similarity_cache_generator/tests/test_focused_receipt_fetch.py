"""Tests for focused receipt reads in the milk cache generator."""

import importlib
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
    monkeypatch.setenv("S3_CACHE_BUCKET", "test-cache-bucket")
    monkeypatch.setattr(boto3, "client", MagicMock())

    # Resolve the real receipt_embeddings import chain before receipt_dynamo
    # is stubbed below; the handler only needs its pure key helper.
    importlib.import_module("receipt_embeddings.keys")

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


def test_dynamo_fetch_keeps_only_target_rows_and_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    image_id = "3f2a1b0c-4d5e-4f70-8192-a3b4c5d6e7f8"

    def _item(line_id: int, text: str) -> dict:
        return {
            "PK": {"S": f"IMAGE#{image_id}"},
            "SK": {"S": f"RECEIPT#00001#LINE#{line_id:05d}#EMBEDDING"},
            "text": {"S": text},
            "merchant_name": {"S": "Sprouts"},
            "row_line_ids": {"L": [{"N": str(line_id)}, {"N": "9"}]},
        }

    raw_client = MagicMock()
    raw_client.query.side_effect = [
        {
            "Items": [_item(4, "Milk Shake 3.99"), _item(5, "BREAD 2.49")],
            "LastEvaluatedKey": {"PK": {"S": "x"}},
        },
        {"Items": [_item(2, "RAW WHOLE MILK 8.99")]},
    ]
    dynamo_client = SimpleNamespace(_client=raw_client)

    result = handler._fetch_lines_from_dynamo(
        handler.TimingStats(), dynamo_client
    )

    assert raw_client.query.call_count == 2
    first_kwargs = raw_client.query.call_args_list[0].kwargs
    assert first_kwargs["IndexName"] == "GSITYPE"
    assert "ExclusiveStartKey" not in first_kwargs
    assert raw_client.query.call_args_list[1].kwargs["ExclusiveStartKey"] == {
        "PK": {"S": "x"}
    }
    # Case-insensitive MILK match, non-milk rows dropped, sorted by key.
    assert [meta["line_id"] for meta in result["metadatas"]] == [2, 4]
    assert result["metadatas"][0]["row_line_ids"] == [2, 9]
    assert result["metadatas"][0]["merchant_name"] == "Sprouts"
    assert len(result["ids"]) == 2


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
