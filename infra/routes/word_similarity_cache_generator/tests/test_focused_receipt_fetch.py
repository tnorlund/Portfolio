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
        bottom_right={"x": x + 0.1, "y": y - height / 2},
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
def test_untrusted_canonical_item_without_row_evidence_stays_unknown(
    monkeypatch: pytest.MonkeyPatch, overrides: dict
) -> None:
    handler = _load_handler(monkeypatch)
    result = handler.find_milk_price(9, [], [], [_milk_item(**overrides)])

    assert result.price is None
    assert result.source == "untrusted"


def test_overlapping_canonical_items_do_not_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)

    result = handler.find_milk_price(
        9, [], [], [_milk_item(), _milk_item(price="5.49")]
    )

    assert result.price is None
    assert result.source == "ambiguous"


def test_incomplete_canonical_items_do_not_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    other_item = _milk_item(name="COFFEE", line_ids=[8, 10])

    result = handler.find_milk_price(9, [], [], [other_item])

    assert result.price is None
    assert result.source == "unmatched"


def test_adjacent_prices_use_owned_row_instead_of_embedding_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    words, labels = _adjacent_prices()

    result = handler.find_milk_price(9, words, labels, [])

    assert result.price == "4.29"
    assert result.source == "row_geometry"


def test_product_text_supplies_ownership_when_product_labels_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    words, labels = _adjacent_prices()

    result = handler.find_milk_price(9, words, labels[2:], [])

    assert result.price == "4.29"
    assert result.source == "row_geometry"


def test_split_price_column_ignores_food_code_letter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    words = [
        _word(9, "MILK", 0.50, height=0.008),
        _word(11, "4.29", 0.501, x=0.8, height=0.008),
        _word(11, "F", 0.501, x=0.95, height=0.008, word_id=2),
    ]
    labels = [_label(11, "UNIT_PRICE"), _label(11, "UNIT_PRICE", word_id=2)]

    result = handler.find_milk_price(9, words, labels, [])

    assert result.price == "4.29"
    assert result.source == "row_geometry"


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

    result = handler.find_milk_price(9, words, labels, [])

    assert result.price == "8.58"
    assert result.source == "labels"


@pytest.mark.parametrize("price", ["4.29", "0.00", None])
@pytest.mark.parametrize("canonical_error", [False, True])
def test_handler_fetches_canonical_items_and_aggregates_price(
    monkeypatch: pytest.MonkeyPatch, price: str | None, canonical_error: bool
) -> None:
    handler = _load_handler(monkeypatch)
    monkeypatch.setattr(handler, "LOCAL_CACHE_OUTPUT", None)
    words, labels = _adjacent_prices()
    if price is None:
        words = [words[1]]
        labels = []
    else:
        words[-1].text = price
    client = MagicMock()
    client.get_receipt_details.return_value = SimpleNamespace(
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
    if canonical_error:
        client.get_receipt_line_items_from_receipt.side_effect = RuntimeError(
            "read failed"
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

    if canonical_error and price is None:
        with pytest.raises(
            RuntimeError, match="Preserving previous milk cache"
        ):
            handler.handler({}, None)
        handler.s3_client.put_object.assert_not_called()
        return

    response = handler.handler({}, None)

    assert response["statusCode"] == 200
    client.get_receipt_line_items_from_receipt.assert_called_once_with(
        "3f2a1b0c-4d5e-4f70-8192-a3b4c5d6e7f8", 1
    )
    cache = json.loads(handler.s3_client.put_object.call_args.kwargs["Body"])
    assert cache["receipts"][0]["price"] == price
    source = (
        ("row_geometry" if price is not None else "line_items_unavailable")
        if canonical_error
        else ("line_item" if price is not None else "ambiguous")
    )
    assert cache["receipts"][0]["price_source"] == source
    assert cache["price_coverage"] == {
        "priced_receipts": int(price is not None),
        "unpriced_receipts": int(price is None),
        "canonical_read_failures": int(canonical_error),
        "failed_receipts": 0,
        "excluded_receipts": 0,
        "sources": {source: 1},
    }
    numeric_price = float(price) if price is not None else None
    assert cache["summary_table"][0]["avg_price"] == numeric_price
    assert cache["summary_table"][0]["total"] == numeric_price
    assert cache["grand_total"] == (numeric_price or 0)


def test_split_product_name_retains_clearly_aligned_price(
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

    result = handler.find_milk_price(9, words, labels, [])

    assert result.price == "4.29"
    assert result.source == "row_geometry"


def test_partial_canonical_extraction_allows_direct_row_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    words = [_word(9, "MILK", 0.50), _word(9, "4.29", 0.50, word_id=2)]
    labels = [_label(9, "LINE_TOTAL", word_id=2)]
    other_item = _milk_item(name="COFFEE", line_ids=[8, 10])

    result = handler.find_milk_price(9, words, labels, [other_item])

    assert result.price == "4.29"
    assert result.source == "labels"
    words[1] = _word(11, "4.29", 0.50, x=0.8)
    labels[0].line_id = 11
    labels[0].word_id = 1
    result = handler.find_milk_price(9, words, labels, [other_item])
    assert result.price == "4.29"
    assert result.source == "row_geometry"


@pytest.mark.parametrize(
    "items",
    [
        [_milk_item(reconciliation_status="mismatch")],
        [_milk_item(name="BANANA EACH", price="5.49")],
        [_milk_item(line_ids=[40, 50])],
        [_milk_item(), _milk_item(price="5.49")],
    ],
)
def test_owned_row_price_survives_stale_canonical_evidence(
    monkeypatch: pytest.MonkeyPatch, items: list
) -> None:
    handler = _load_handler(monkeypatch)
    words, labels = _adjacent_prices()

    result = handler.find_milk_price(9, words, labels, items)

    assert result.price == "4.29"
    assert result.source == "row_geometry"


def test_reconciled_canonical_swap_requires_row_corroboration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    words, labels = _adjacent_prices()

    result = handler.find_milk_price(
        9, words, labels, [_milk_item(price="5.49")]
    )

    assert result.price == "4.29"
    assert result.source == "row_geometry"
    assert handler.find_milk_price(9, [], [], [_milk_item()]).price is None


def test_equally_aligned_products_remain_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    words = [
        _word(8, "COFFEE COLD BREW", 0.50),
        _word(9, "ORGANIC MILK", 0.50),
        _word(11, "4.29", 0.50, x=0.8),
    ]
    labels = [_label(11, "LINE_TOTAL")]

    result = handler.find_milk_price(9, words, labels, [_milk_item()])

    assert result.price is None
    assert result.source == "ambiguous"


def test_skewed_product_baseline_owns_its_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    words = [
        _word(9, "WHOLE", 0.50, x=0.1),
        _word(9, "MILK", 0.51, x=0.3, word_id=2),
        _word(10, "NEXT PRODUCT", 0.48, x=0.1),
        _word(11, "4.29", 0.535, x=0.8),
        _word(12, "5.49", 0.515, x=0.8),
    ]
    labels = [_label(11, "LINE_TOTAL"), _label(12, "LINE_TOTAL")]

    result = handler.find_milk_price(9, words, labels, [])

    assert result.price == "4.29"


def test_oversized_price_box_uses_bottom_edge_not_center(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    words = [
        _word(8, "RAW CREAM", 0.52),
        _word(9, "RAW MILK", 0.50),
        _word(11, "4.29", 0.51, x=0.8, height=0.04),
    ]

    result = handler.find_milk_price(9, words, [_label(11, "UNIT_PRICE")], [])

    assert result.price == "4.29"


def test_truncated_ocr_price_needs_corrected_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    words = [_word(9, "WHOLE MILK", 0.50), _word(11, "11,4", 0.50, x=0.8)]
    labels = [_label(11, "LINE_TOTAL")]

    assert handler.find_milk_price(9, words, labels, []).price is None
    words[1].text = "11.49"
    assert handler.find_milk_price(9, words, labels, []).price == "11.49"


def test_void_pair_cancels_matching_amount_and_keeps_other_milk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    lines = [
        SimpleNamespace(line_id=10, text="WHOLE MILK"),
        SimpleNamespace(line_id=11, text="WHOLE MILK"),
        SimpleNamespace(line_id=12, text="Voided Item"),
        SimpleNamespace(line_id=13, text="WHOLE MILK"),
    ]
    words = [
        _word(10, "WHOLE MILK", 0.55),
        _word(11, "WHOLE MILK", 0.52),
        _word(13, "WHOLE MILK", 0.46),
        _word(20, "8.99", 0.55, x=0.8),
        _word(21, "6.29", 0.52, x=0.8),
        _word(22, "-8.99", 0.46, x=0.8),
    ]
    labels = [_label(20, "LINE_TOTAL"), _label(21, "LINE_TOTAL")]

    assert handler.find_milk_line(lines, words=words, labels=labels) == (
        "WHOLE MILK",
        11,
    )
    assert handler.find_milk_price(13, words, labels, []).source == "void"
    assert (
        handler.find_milk_line(
            [lines[0], lines[2], lines[3]], words=words, labels=labels
        )
        is None
    )


def test_receipt_fingerprint_and_dedup_keep_distinct_transactions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    receipt = SimpleNamespace(width=300, height=500, sha256=None)
    lines = [_word(i, f"PRODUCT {i}", 0.9 - i * 0.03) for i in range(10)]
    words = [
        _word(i, f"WORD {j}", 0.9 - i * 0.03, word_id=j, x=j * 0.1)
        for i in range(10)
        for j in range(4)
    ]
    fingerprint = handler.receipt_fingerprint(receipt, lines, words)
    assert fingerprint is not None
    assert fingerprint == handler.receipt_fingerprint(
        receipt, list(reversed(lines)), list(reversed(words))
    )
    lines[-1].text = "DIFFERENT TRANSACTION"
    assert fingerprint != handler.receipt_fingerprint(receipt, lines, words)
    assert handler.receipt_fingerprint(receipt, lines[:2], words[:3]) is None
    results = [
        {
            "image_id": "second",
            "receipt_id": 1,
            "price": "4.29",
            "_fingerprint": fingerprint,
        },
        {
            "image_id": "first",
            "receipt_id": 2,
            "price": "4.29",
            "_fingerprint": fingerprint,
        },
        {
            "image_id": "first",
            "receipt_id": 3,
            "price": "5.49",
            "_fingerprint": None,
        },
    ]
    unique, duplicates = handler.deduplicate_receipts(results)
    assert [(r["image_id"], r["receipt_id"]) for r in unique] == [
        ("first", 2),
        ("first", 3),
    ]
    assert duplicates == [
        {
            "image_id": "second",
            "receipt_id": 1,
            "kept_image_id": "first",
            "kept_receipt_id": 2,
        }
    ]


def test_rotated_receipt_can_have_prices_left_of_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    words = [
        _word(8, "RAW CREAM", 0.48, x=0.7),
        _word(9, "RAW WHOLE MILK", 0.50, x=0.7),
        _word(11, "13.99", 0.48, x=0.1),
        _word(12, "10.99", 0.50, x=0.1),
    ]
    labels = [_label(11, "LINE_TOTAL"), _label(12, "LINE_TOTAL")]

    result = handler.find_milk_price(9, words, labels, [])

    assert result.price == "10.99"
    assert result.source == "row_geometry"


@pytest.mark.parametrize("second_region", ["purchase", "void", "error"])
def test_handler_keeps_receipt_regions_separate_and_uses_full_rows(
    monkeypatch: pytest.MonkeyPatch,
    second_region: str,
) -> None:
    handler = _load_handler(monkeypatch)
    monkeypatch.setattr(handler, "LOCAL_CACHE_OUTPUT", None)
    words = [
        _word(10, "WHOLE MILK", 0.55),
        _word(11, "WHOLE MILK", 0.52),
        _word(13, "WHOLE MILK", 0.46),
        _word(20, "8.99", 0.55, x=0.8),
        _word(21, "6.29", 0.52, x=0.8),
        _word(22, "-8.99", 0.46, x=0.8),
    ]
    lines = [
        SimpleNamespace(line_id=i, text="WHOLE MILK") for i in (10, 11, 13)
    ]
    lines.append(SimpleNamespace(line_id=12, text="Voided Item"))
    client = MagicMock()

    def receipt_details(
        image_id: str, receipt_id: int, **kwargs: object
    ) -> SimpleNamespace:
        if receipt_id == 2 and second_region == "error":
            raise RuntimeError("primary read failed")
        selected_lines = lines
        if receipt_id == 2 and second_region == "void":
            selected_lines = [line for line in lines if line.line_id != 11]
        return SimpleNamespace(
            receipt=SimpleNamespace(),
            place=None,
            lines=selected_lines,
            words=words,
            labels=[],
        )

    client.get_receipt_details.side_effect = receipt_details
    client.get_receipt_line_items_from_receipt.return_value = []
    handler.DynamoClient.return_value = client
    monkeypatch.setattr(handler, "receipt_to_dict", lambda receipt: {})
    # Stale embeddings contain only the reversed row. Full receipt reads must
    # still find the purchased row, separately in both physical receipt regions.
    monkeypatch.setattr(
        handler,
        "_fetch_lines_from_dynamo",
        lambda *args: {
            "ids": ["one", "two"],
            "metadatas": [
                {
                    "image_id": "3f2a1b0c-4d5e-4f70-8192-a3b4c5d6e7f8",
                    "receipt_id": rid,
                    "line_id": 13,
                    "row_line_ids": [13, 22],
                    "text": "WHOLE MILK -8.99 CHOCOLATE OAT MILK",
                }
                for rid in (1, 2)
            ],
        },
    )

    if second_region == "error":
        with pytest.raises(
            RuntimeError, match="Preserving previous milk cache"
        ):
            handler.handler({}, None)
        assert client.get_receipt_details.call_count == 2
        handler.s3_client.put_object.assert_not_called()
        return

    assert handler.handler({}, None)["statusCode"] == 200

    assert client.get_receipt_details.call_count == 2
    assert all(
        call.kwargs == {"consistent_read": True}
        for call in client.get_receipt_details.call_args_list
    )
    client.get_receipt_details_for_lines.assert_not_called()
    cache = json.loads(handler.s3_client.put_object.call_args.kwargs["Body"])
    assert [
        (r["receipt_id"], r["line_id"], r["price"]) for r in cache["receipts"]
    ] == (
        [(1, 11, "6.29"), (2, 11, "6.29")]
        if second_region == "purchase"
        else [(1, 11, "6.29")]
    )
    assert cache["grand_total"] == (
        12.58 if second_region == "purchase" else 6.29
    )
    assert cache["price_coverage"]["failed_receipts"] == 0
    assert cache["price_coverage"]["excluded_receipts"] == int(
        second_region == "void"
    )
    if second_region == "void":
        assert cache["excluded_receipts"][0]["reason"] == "no_purchased_milk"


def test_all_receipt_reads_failing_preserves_previous_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    monkeypatch.setattr(handler, "LOCAL_CACHE_OUTPUT", None)
    client = MagicMock()
    client.get_receipt_details.side_effect = RuntimeError(
        "primary read failed"
    )
    handler.DynamoClient.return_value = client
    monkeypatch.setattr(
        handler,
        "_fetch_lines_from_dynamo",
        lambda *args: {
            "ids": ["one"],
            "metadatas": [
                {
                    "image_id": "example",
                    "receipt_id": 1,
                    "line_id": 9,
                    "text": "WHOLE MILK 4.29",
                }
            ],
        },
    )

    with pytest.raises(RuntimeError, match="Preserving previous milk cache"):
        handler.handler({}, None)
    handler.s3_client.put_object.assert_not_called()


def test_tax_marker_price_and_independently_priced_deposit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    words = [
        _word(7, "WHOLE MILK", 0.52),
        _word(8, "BOTTLE DEPOSIT", 0.50),
        _word(8, "$2.00", 0.50, x=0.05, word_id=2),
        _word(20, "5.19*", 0.509, x=0.8),
        _word(21, "2.00", 0.50, x=0.8),
    ]
    labels = [_label(20, "LINE_TOTAL"), _label(21, "LINE_TOTAL")]

    assert handler.find_milk_price(7, words, labels, []).price == "5.19"
    # Without an independently confirmed deposit total, the competing rows
    # are genuinely ambiguous; the asterisk must not authorize a row guess.
    assert handler.find_milk_price(7, words[:-1], labels, []).price is None


@pytest.mark.parametrize("cents_layout", ["adjacent", "distant", "ambiguous"])
def test_split_dollars_cents_requires_unique_adjacent_pair(
    monkeypatch: pytest.MonkeyPatch,
    cents_layout: str,
) -> None:
    handler = _load_handler(monkeypatch)
    words = [
        _word(7, "ORGANIC WHOLE MILK", 0.50),
        _word(20, "$5", 0.50, x=0.75),
        _word(21, "99", 0.50 if cents_layout != "distant" else 0.46, x=0.85),
    ]
    if cents_layout == "ambiguous":
        words.append(_word(22, "49", 0.50, x=0.855))

    result = handler.find_milk_price(7, words, [], [])

    assert result.price == ("5.99" if cents_layout == "adjacent" else None)


@pytest.mark.parametrize(
    "text,continuation,reason",
    [
        ("Hot CupCake Latte, Whole Milk", None, "prepared_drink"),
        ("Strawberry Milk Matcha", None, "prepared_drink"),
        ("Hot Strawberry Milk", "Matcha Latte", "prepared_drink"),
        ("Splash of Whole Milk", None, "milk_modifier"),
    ],
)
def test_prepared_drinks_and_explicit_milk_options_are_excluded(
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    continuation: str | None,
    reason: str,
) -> None:
    handler = _load_handler(monkeypatch)
    lines = [SimpleNamespace(line_id=7, text=text)]
    if continuation:
        lines.append(SimpleNamespace(line_id=8, text=continuation))

    assert (
        handler.milk_line_exclusion_reason(lines[0], lines, [], []) == reason
    )
    assert handler.find_milk_line(lines) is None


@pytest.mark.parametrize("milk_price", [None, "4.29"])
@pytest.mark.parametrize("size_line", [False, True])
def test_indented_unpriced_milk_is_drink_modifier_but_paid_milk_is_purchase(
    monkeypatch: pytest.MonkeyPatch,
    milk_price: str | None,
    size_line: bool,
) -> None:
    handler = _load_handler(monkeypatch)
    lines = [
        SimpleNamespace(line_id=7, text="ICED LATTE"),
        SimpleNamespace(line_id=9 if size_line else 8, text="WHOLE MILK"),
    ]
    words = [
        _word(7, "ICED LATTE", 0.56, x=0.1),
        _word(20, "6.25", 0.56, x=0.8),
        _word(lines[1].line_id, "WHOLE MILK", 0.50, x=0.15),
    ]
    if size_line:
        lines.append(SimpleNamespace(line_id=8, text="Regular"))
        words.append(_word(8, "Regular", 0.53, x=0.15))
    if milk_price:
        words.append(_word(21, milk_price, 0.50, x=0.8))

    assert handler.find_milk_line(lines, words=words, labels=[]) == (
        ("WHOLE MILK", 9 if size_line else 8) if milk_price else None
    )
    assert handler.milk_line_exclusion_reason(lines[1], lines, words, []) == (
        None if milk_price else "milk_modifier"
    )


def test_unrelated_negative_row_does_not_void_preceding_milk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    lines = [
        SimpleNamespace(line_id=10, text="WHOLE MILK"),
        SimpleNamespace(line_id=11, text="Voided Item"),
        SimpleNamespace(line_id=12, text="BANANAS -1.00"),
    ]
    words = [
        _word(10, "WHOLE MILK", 0.55),
        _word(20, "4.49", 0.55, x=0.8),
        _word(12, "BANANAS", 0.48),
        _word(21, "-1.00", 0.48, x=0.8),
    ]

    assert handler.find_milk_line(lines, words=words, labels=[]) == (
        "WHOLE MILK",
        10,
    )


def test_unlabeled_single_word_product_keeps_its_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    words = [
        _word(9, "WHOLE MILK", 0.50),
        _word(10, "BANANAS", 0.51),
        _word(20, "4,2", 0.50, x=0.8),
        _word(21, "1.00", 0.51, x=0.8),
    ]

    result = handler.find_price_on_visual_line(9, words, [])

    assert result.price is None
    assert result.source == "ambiguous"


@pytest.mark.parametrize("word_price", [None, "4.29"])
@pytest.mark.parametrize(
    "embedding_line,embedding_text",
    [
        (20, "SKIM MILK 7.99"),
        (9, "OAT MILK WHOLE MILK 5.49"),
        (9, "WHOLE MILK 11,4 7.95"),
    ],
)
def test_handler_never_prices_selected_milk_from_embedding_text(
    monkeypatch: pytest.MonkeyPatch,
    word_price: str | None,
    embedding_line: int,
    embedding_text: str,
) -> None:
    handler = _load_handler(monkeypatch)
    monkeypatch.setattr(handler, "LOCAL_CACHE_OUTPUT", None)
    words = [_word(9, "WHOLE MILK", 0.50)]
    if word_price:
        words.append(_word(10, word_price, 0.50, x=0.8))
    elif "11,4" in embedding_text:
        words.append(_word(10, "11,4", 0.50, x=0.8))
    client = MagicMock()
    client.get_receipt_details.return_value = SimpleNamespace(
        receipt=SimpleNamespace(),
        place=None,
        lines=[SimpleNamespace(line_id=9, text="WHOLE MILK 4.29")],
        words=words,
        labels=[],
    )
    client.get_receipt_line_items_from_receipt.return_value = []
    handler.DynamoClient.return_value = client
    monkeypatch.setattr(handler, "receipt_to_dict", lambda receipt: {})
    monkeypatch.setattr(
        handler,
        "_fetch_lines_from_dynamo",
        lambda *args: {
            "ids": ["first-row"],
            "metadatas": [
                {
                    "image_id": "example",
                    "receipt_id": 1,
                    "line_id": embedding_line,
                    "text": embedding_text,
                }
            ],
        },
    )

    assert handler.handler({}, None)["statusCode"] == 200

    cache = json.loads(handler.s3_client.put_object.call_args.kwargs["Body"])
    assert cache["receipts"][0]["line_id"] == 9
    assert cache["receipts"][0]["price"] == word_price
    assert cache["receipts"][0]["price_source"] == (
        "row_geometry" if word_price else "missing"
    )


@pytest.mark.parametrize(
    "single_word,word_y,price_y,expected",
    [
        ("DAIRY", 0.514, 0.514, "8.49"),
        ("BANANAS", 0.508, 0.522, None),
    ],
)
def test_single_word_competitor_follows_local_receipt_skew(
    monkeypatch: pytest.MonkeyPatch,
    single_word: str,
    word_y: float,
    price_y: float,
    expected: str | None,
) -> None:
    handler = _load_handler(monkeypatch)
    words = [
        _word(9, "WHOLE", 0.50, x=0.1),
        _word(9, "MILK", 0.504, x=0.3, word_id=2),
        _word(10, single_word, word_y, x=0.1),
        _word(20, "8.49", price_y, x=0.8),
    ]

    assert handler.find_price_on_visual_line(9, words, []).price == expected


@pytest.mark.parametrize(
    "section_status,product_evidence,expected_price",
    [
        ("VALID", False, "10.99"),
        ("PENDING", False, None),
        (None, False, None),
        ("VALID", True, None),
    ],
)
def test_handler_excludes_only_validated_headers_from_price_competitors(
    monkeypatch: pytest.MonkeyPatch,
    section_status: str | None,
    product_evidence: bool,
    expected_price: str | None,
) -> None:
    handler = _load_handler(monkeypatch)
    monkeypatch.setattr(handler, "LOCAL_CACHE_OUTPUT", None)
    words = [
        _word(8, "BANANAS" if product_evidence else "DAIRY", 0.508),
        _word(9, "WHOLE MILK", 0.50),
        _word(20, "10.99", 0.507, x=0.8),
    ]
    labels = [_label(8, "PRODUCT_NAME")] if product_evidence else []
    sections = (
        [
            SimpleNamespace(
                section_type="SECTION_HEADER",
                validation_status=section_status,
                line_ids=[8],
            )
        ]
        if section_status
        else []
    )
    client = MagicMock()
    client.get_receipt_details.return_value = SimpleNamespace(
        receipt=SimpleNamespace(),
        place=None,
        lines=[SimpleNamespace(line_id=9, text="WHOLE MILK")],
        words=words,
        labels=labels,
        sections=sections,
    )
    client.get_receipt_line_items_from_receipt.return_value = []
    handler.DynamoClient.return_value = client
    monkeypatch.setattr(handler, "receipt_to_dict", lambda receipt: {})
    bbox = MagicMock(return_value=None)
    monkeypatch.setattr(handler, "calculate_product_bbox", bbox)
    monkeypatch.setattr(
        handler,
        "_fetch_lines_from_dynamo",
        lambda *args: {
            "ids": ["row"],
            "metadatas": [
                {
                    "image_id": "example",
                    "receipt_id": 1,
                    "line_id": 9,
                    "text": "WHOLE MILK",
                }
            ],
        },
    )

    assert handler.handler({}, None)["statusCode"] == 200

    cache = json.loads(handler.s3_client.put_object.call_args.kwargs["Body"])
    assert cache["receipts"][0]["price"] == expected_price
    assert cache["receipts"][0]["price_source"] == (
        "row_geometry" if expected_price else "ambiguous"
    )
    # Header handling is limited to ownership; crops retain the full words.
    assert bbox.call_args.args[1] is words
    assert len(words) == 3


def test_validated_milk_header_cannot_be_selected_or_own_a_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = _load_handler(monkeypatch)
    lines = [SimpleNamespace(line_id=9, text="MILK")]
    words = [_word(9, "MILK", 0.50), _word(20, "4.29", 0.50, x=0.8)]
    non_product_line_ids = {9}

    assert (
        handler.find_milk_line(
            lines,
            words=words,
            labels=[],
            non_product_line_ids=non_product_line_ids,
        )
        is None
    )
    result = handler.find_price_on_visual_line(
        9, words, [], non_product_line_ids=non_product_line_ids
    )
    assert result.price is None
    assert result.source == "missing"
