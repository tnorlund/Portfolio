"""Tests for the raw ReceiptPlace census script."""

import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "list_receipt_places.py"
SPEC = importlib.util.spec_from_file_location(
    "list_receipt_places", SCRIPT_PATH
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
list_all_places = MODULE.list_all_places


def test_list_all_places_uses_canonical_place_pages():
    first = {
        "image_id": {"S": "image-1"},
        "receipt_id": {"N": "1"},
        "merchant_name": {"S": "First Merchant"},
    }
    second = {
        "image_id": {"S": "image-2"},
        "receipt_id": {"N": "1"},
        "merchant_name": {"S": "Second Merchant"},
    }

    class PlaceOnlyClient:
        def __init__(self):
            self.calls = []

        def query(self, **kwargs):
            self.calls.append(kwargs)
            if "ExclusiveStartKey" not in kwargs:
                return {
                    "Items": [first],
                    "LastEvaluatedKey": {"PK": {"S": "next"}},
                }
            return {"Items": [second]}

    client = PlaceOnlyClient()

    places = list_all_places(client, "ReceiptsTable-dc5be22")

    assert [place["merchant_name"] for place in places] == [
        "First Merchant",
        "Second Merchant",
    ]
    assert all(call["IndexName"] == "GSITYPE" for call in client.calls)
    assert all(
        call["ExpressionAttributeValues"][":entity_type"]
        == {"S": "RECEIPT_PLACE"}
        for call in client.calls
    )
    assert client.calls[1]["ExclusiveStartKey"] == {"PK": {"S": "next"}}


def test_list_all_places_applies_limit_across_pages():
    items = [
        {
            "image_id": {"S": f"image-{index}"},
            "receipt_id": {"N": "1"},
        }
        for index in range(3)
    ]

    class PlaceOnlyClient:
        def query(self, **kwargs):
            assert kwargs["Limit"] == 2
            return {
                "Items": items,
                "LastEvaluatedKey": {"PK": {"S": "unused"}},
            }

    places = list_all_places(
        PlaceOnlyClient(), "ReceiptsTable-dc5be22", limit=2
    )

    assert [place["image_id"] for place in places] == ["image-0", "image-1"]
