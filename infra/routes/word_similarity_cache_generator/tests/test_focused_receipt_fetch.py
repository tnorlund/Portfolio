"""Tests for focused receipt reads in the milk cache generator."""

import importlib
import importlib.util
import json
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


def _word(
    line_id: int,
    text: str,
    y: float,
    *,
    word_id: int = 1,
    x: float = 0.1,
    height: float = 0.02,
) -> SimpleNamespace:
    """Small synthetic OCR geometry; no receipt images or private dumps."""
    return SimpleNamespace(
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box={"height": height},
        top_left={"x": x, "y": y + height / 2},
        top_right={"x": x + 0.1, "y": y + height / 2},
        bottom_left={"x": x, "y": y - height / 2},
        calculate_centroid=lambda: (x, y),
    )


def _label(line_id: int, role: str, *, word_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        line_id=line_id,
        word_id=word_id,
        label=role,
        validation_status="VALID",
        timestamp_added="2026-01-01T00:00:00+00:00",
    )


def _milk_item(**overrides: object) -> SimpleNamespace:
    attributes = {
        "name": "MILK ORGANIC HALF GALLON",
        "price": "4.29",
        "line_ids": [9, 11],
        "name_quality": "ok",
        "is_discount": False,
        "collapsed_banding": False,
        "source_section_status": "VALID",
        "reconciliation_status": "match",
    }
    attributes.update(overrides)
    return SimpleNamespace(**attributes)


def _adjacent_prices() -> tuple[list, list]:
    # Close rows with separate product/price OCR lines. Coffee sorts first
    # within the broad visual group, which used to win the milk lookup.
    words = [
        _word(8, "COFFEE COLD BREW", 0.51),
        _word(9, "MILK ORGANIC HALF GALLON", 0.50),
        _word(10, "$5.49", 0.51, x=0.8),
        _word(11, "$4.29", 0.50, x=0.8),
    ]
    labels = [
        _label(8, "PRODUCT_NAME"),
        _label(9, "PRODUCT_NAME"),
        _label(10, "LINE_TOTAL"),
        _label(11, "LINE_TOTAL"),
    ]
    return words, labels


@pytest.mark.parametrize("status", ["match", "near"])
@pytest.mark.parametrize("section_status", ["VALID", "PENDING"])
def test_canonical_milk_price_ignores_adjacent_coffee(
    monkeypatch: pytest.MonkeyPatch, status: str, section_status: str
) -> None:
    handler = _load_handler(monkeypatch)
    words, labels = _adjacent_prices()
    coffee = _milk_item(
        name="COFFEE COLD BREW", price="5.49", line_ids=[8, 10]
    )

    result = handler.find_milk_price(
        9,
        words,
        labels,
        [
            coffee,
            _milk_item(
                reconciliation_status=status,
                source_section_status=section_status,
            ),
        ],
        "MILK 5.49",
    )

    assert result.price == "4.29"
    assert result.source == "line_item"


@pytest.mark.parametrize(
    "overrides",
    [
        {"reconciliation_status": "mismatch"},
        {"reconciliation_status": "no-baseline"},
        {"reconciliation_status": None},
        {"source_section_status": "INVALID"},
        {"source_section_status": None},
        {"name_quality": "low"},
        {"collapsed_banding": True},
        {"is_discount": True},
        {"price": "-4.29"},
        {"price": "NaN"},
        {"price": "Infinity"},
        {"name": "ALMOND MILK"},
        {"name": "COFFEE COLD BREW"},
    ],
)
def test_untrusted_canonical_item_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch, overrides: dict
) -> None:
    handler = _load_handler(monkeypatch)
    words, labels = _adjacent_prices()

    result = handler.find_milk_price(
        9, words, labels, [_milk_item(**overrides)], "MILK 5.49"
    )

    assert result.price is None
    assert result.source == "untrusted"


def test_overlapping_canonical_items_do_not_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)

    result = handler.find_milk_price(
        9, [], [], [_milk_item(), _milk_item(price="5.49")], "MILK 5.49"
    )

    assert result.price is None
    assert result.source == "ambiguous"


def test_incomplete_canonical_items_do_not_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    other_item = _milk_item(name="COFFEE", line_ids=[8, 10])

    result = handler.find_milk_price(9, [], [], [other_item], "MILK 5.49")

    assert result.price is None
    assert result.source == "unmatched"


def test_legacy_adjacent_prices_do_not_use_row_text_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    words, labels = _adjacent_prices()

    result = handler.find_milk_price(9, words, labels, [], "MILK 5.49")

    assert result.price is None
    assert result.source == "ambiguous"


def test_legacy_competing_prices_without_product_labels_are_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    words, labels = _adjacent_prices()

    result = handler.find_milk_price(9, words, labels[2:], [], "MILK 5.49")

    assert result.price is None
    assert result.source == "ambiguous"


def test_legacy_split_row_keeps_unambiguous_proximity_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    words = [
        _word(9, "MILK", 0.50, height=0.008),
        _word(11, "4.29", 0.52, x=0.8, height=0.008),
        _word(11, "F", 0.52, x=0.95, height=0.008, word_id=2),
    ]
    labels = [_label(11, "UNIT_PRICE"), _label(11, "UNIT_PRICE", word_id=2)]

    result = handler.find_milk_price(9, words, labels, [], "MILK")

    assert result.price == "4.29"
    assert result.source == "labels"


def test_legacy_same_line_total_wins_over_unit_and_neighbor_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    words = [
        _word(9, "MILK", 0.50),
        _word(9, "4.29", 0.50, x=0.6, word_id=2),
        _word(9, "8.58", 0.50, x=0.8, word_id=3),
        _word(10, "5.49", 0.51, x=0.8),
    ]
    labels = [
        _label(9, "UNIT_PRICE", word_id=2),
        _label(9, "LINE_TOTAL", word_id=3),
        _label(10, "LINE_TOTAL"),
    ]

    result = handler.find_milk_price(9, words, labels, [], "MILK 4.29 8.58")

    assert result.price == "8.58"
    assert result.source == "labels"


@pytest.mark.parametrize(
    ("row_text", "expected_price", "expected_source"),
    [
        ("MILK HALF GALLON $4.29", "4.29", "row_text"),
        ("MILK HALF GALLON $0.00", "0.00", "row_text"),
        ("COFFEE 5.49 MILK 4.29", None, "ambiguous"),
        ("MILK HALF GALLON", None, "missing"),
    ],
)
def test_legacy_row_text_requires_one_price(
    monkeypatch: pytest.MonkeyPatch,
    row_text: str,
    expected_price: str | None,
    expected_source: str,
) -> None:
    handler = _load_handler(monkeypatch)

    result = handler.find_milk_price(
        9, [_word(9, "MILK", 0.5)], [], [], row_text
    )

    assert result.price == expected_price
    assert result.source == expected_source


@pytest.mark.parametrize("price", ["4.29", "0.00", None])
def test_handler_fetches_canonical_items_and_aggregates_price(
    monkeypatch: pytest.MonkeyPatch, price: str | None
) -> None:
    handler = _load_handler(monkeypatch)
    monkeypatch.setattr(handler, "LOCAL_CACHE_OUTPUT", None)
    words, labels = _adjacent_prices()
    client = MagicMock()
    client.get_receipt_details_for_lines.return_value = SimpleNamespace(
        receipt=SimpleNamespace(),
        place=SimpleNamespace(merchant_name="Example Market"),
        lines=[SimpleNamespace(line_id=9, text="MILK ORGANIC HALF GALLON")],
        words=words,
        labels=labels,
    )
    client.get_receipt_line_items_from_receipt.return_value = (
        [_milk_item(price=price)]
        if price is not None
        else [_milk_item(), _milk_item(price="5.49")]
    )
    handler.DynamoClient.return_value = client
    monkeypatch.setattr(handler, "receipt_to_dict", lambda receipt: {})
    monkeypatch.setattr(
        handler,
        "_fetch_lines_from_dynamo",
        lambda *args: {
            "ids": ["synthetic-line"],
            "metadatas": [
                {
                    "image_id": "3f2a1b0c-4d5e-4f70-8192-a3b4c5d6e7f8",
                    "receipt_id": 1,
                    "line_id": 9,
                    "row_line_ids": [9, 11],
                    "text": "MILK ORGANIC HALF GALLON 5.49",
                }
            ],
        },
    )

    response = handler.handler({}, None)

    assert response["statusCode"] == 200
    client.get_receipt_line_items_from_receipt.assert_called_once_with(
        "3f2a1b0c-4d5e-4f70-8192-a3b4c5d6e7f8", 1
    )
    cache = json.loads(handler.s3_client.put_object.call_args.kwargs["Body"])
    assert cache["receipts"][0]["price"] == price
    source = "line_item" if price is not None else "ambiguous"
    assert cache["receipts"][0]["price_source"] == source
    assert cache["price_coverage"] == {
        "priced_receipts": int(price is not None),
        "unpriced_receipts": int(price is None),
        "sources": {source: 1},
    }
    numeric_price = float(price) if price is not None else None
    assert cache["summary_table"][0]["avg_price"] == numeric_price
    assert cache["summary_table"][0]["total"] == numeric_price
    assert cache["grand_total"] == (numeric_price or 0)


def test_legacy_split_product_name_does_not_guess_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    words = [
        _word(9, "ORGANIC MILK", 0.50),
        _word(10, "HALF GALLON", 0.49),
        _word(11, "4.29", 0.50, x=0.8),
    ]
    labels = [
        _label(9, "PRODUCT_NAME"),
        _label(10, "PRODUCT_NAME"),
        _label(11, "LINE_TOTAL"),
    ]

    result = handler.find_milk_price(9, words, labels, [], "MILK 4.29")

    assert result.price is None
    assert result.source == "ambiguous"


def test_partial_canonical_extraction_allows_only_same_line_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    words = [_word(9, "MILK", 0.50), _word(9, "4.29", 0.50, word_id=2)]
    labels = [_label(9, "LINE_TOTAL", word_id=2)]
    other_item = _milk_item(name="COFFEE", line_ids=[8, 10])

    result = handler.find_milk_price(
        9, words, labels, [other_item], "MILK 5.49"
    )

    assert result.price == "4.29"
    assert result.source == "labels"
    words[1].line_id = 11
    labels[0].line_id = 11
    result = handler.find_milk_price(
        9, words, labels, [other_item], "MILK 5.49"
    )
    assert result.price is None
    assert result.source == "unmatched"
