"""Hash-stable payload round-trip through DynamoDB number normalization.

Reproduces the G1 live-mint failure: real DynamoDB normalizes number
representations (``0.0`` is stored and returned as ``0``), so a payload
stored as a native AttributeValue tree changes its canonical JSON across
the write/read round-trip and fails ``content_hash`` verification on
read-back (seen live on costco_wholesale ``C#typography`` via
``bitmap_thin: 0.0``). moto preserves the literal number string, which is
why the original moto suite could not catch it — these tests simulate the
real service's normalization explicitly.
"""

from decimal import Decimal
from typing import Any

import pytest

from receipt_dynamo.entities.dynamodb_utils import to_dynamodb_value
from receipt_dynamo.entities.merchant_truth import (
    MerchantTruthComponent,
    canonical_json_bytes,
)

pytestmark = pytest.mark.unit

LEGACY_PROVENANCE = {
    "source_kind": "migration",
    "provenance_completeness": "legacy",
    "written_by": {
        "kind": "migration",
        "name": "merchant_truth_v1",
        "version": "1",
    },
}

# The real Costco Wholesale profile slice that broke the G1 seal:
# ``bitmap_thin: 0.0`` plus integral floats (display heading scales),
# non-integral floats, nested maps, and lists — taken verbatim from
# scripts/merchant_profiles.json at the time of the failure.
COSTCO_TYPOGRAPHY_PAYLOAD: dict[str, Any] = {
    "typography": {
        "bitmap_thin": 0.0,
        "bitmap_font": {
            "regular": "bitMatrix-C2.glyphs.npz",
            "heavy": "bitMatrix-C2-heavy.glyphs.npz",
        },
        "condense": 0.93,
    },
    "section_scale": {"FOOTER": 0.5},
}
COSTCO_FLAGS_PAYLOAD: dict[str, Any] = {
    "typography": {
        "display_headings": {
            "SELF-CHECKOUT": 1.7,
            "SELF CHECKOUT": 1.7,
            "THANK YOU": 1.4,
            "PLEASE COME AGAIN": 1.4,
            "ITEMS SOLD:": 1.8,
        },
        "reverse_total": True,
        "reverse_date_after_items": True,
        "dashed_separators": True,
        "heading_bleed_phrase": "ITEMS SOLD:",
        "reverse_date_anchor": "NUMBER OF ITEMS SOLD",
        "dash_after_amount_date": True,
        "dash_around_phrases": ["SHOP CARD"],
        "face_source": "stylemap",
    }
}
COSTCO_LAYOUT_PAYLOAD: dict[str, Any] = {
    "available": True,
    "template": {
        "version": 1,
        "measured": {
            "receipts": 12,
            "tool_git_sha": "d9ce9cb82ecf",
            "tool_dirty": False,
        },
        "columns": {
            "barcode": [
                {
                    "role": "desc",
                    "anchor": "left",
                    "x": 0.2479,
                    "spread": 0.007,
                    "support": 7,
                }
            ],
            "footer": [
                {
                    "role": "desc",
                    "anchor": "left",
                    "x": 0.0171,
                    "spread": 0.0,
                    "support": 12,
                }
            ],
        },
    },
}


def normalize_numbers(value: dict[str, Any]) -> dict[str, Any]:
    """Simulate real DynamoDB's stored number representation.

    DynamoDB treats ``0.0`` and ``0`` as the same number and returns the
    normalized form without a trailing fractional part; moto returns the
    literal string it was given.
    """
    if "N" in value:
        number = Decimal(value["N"])
        integral = number.to_integral_value()
        if number == integral:
            return {"N": str(integral)}
        return {"N": str(number.normalize())}
    if "M" in value:
        return {
            "M": {
                key: normalize_numbers(item)
                for key, item in value["M"].items()
            }
        }
    if "L" in value:
        return {"L": [normalize_numbers(item) for item in value["L"]]}
    return value


def component(name: str, payload: Any) -> MerchantTruthComponent:
    return MerchantTruthComponent(
        slug="costco_wholesale",
        version=1,
        name=name,
        payload=payload,
        provenance=dict(LEGACY_PROVENANCE),
    )


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("typography", COSTCO_TYPOGRAPHY_PAYLOAD),
        ("flags", COSTCO_FLAGS_PAYLOAD),
        ("layout", COSTCO_LAYOUT_PAYLOAD),
    ],
)
def test_real_costco_payload_survives_dynamodb_number_normalization(
    name: str, payload: Any
) -> None:
    """The exact G1 failure: fails on AttributeValue-encoded payloads."""
    original = component(name, payload)

    round_tripped = MerchantTruthComponent.from_item(
        normalize_numbers({"M": original.to_item()})["M"]
    )

    assert round_tripped.payload == original.payload
    assert round_tripped.content_hash == original.content_hash


def test_payload_is_stored_as_the_exact_hashed_canonical_json_string() -> None:
    original = component("typography", COSTCO_TYPOGRAPHY_PAYLOAD)

    item = original.to_item()

    assert item["payload"] == {
        "S": canonical_json_bytes(COSTCO_TYPOGRAPHY_PAYLOAD).decode("utf-8")
    }
    assert '"bitmap_thin":0.0' in item["payload"]["S"]


def test_zero_point_zero_float_form_is_preserved_verbatim() -> None:
    original = component("typography", {"bitmap_thin": 0.0})

    restored = MerchantTruthComponent.from_item(
        normalize_numbers({"M": original.to_item()})["M"]
    )

    assert repr(restored.payload["bitmap_thin"]) == "0.0"
    assert restored.content_hash == original.content_hash


def test_legacy_attributevalue_payload_is_rejected_with_clear_error() -> None:
    """The orphaned G1 costco rows must fail loudly, not with a hash riddle."""
    original = component("typography", COSTCO_TYPOGRAPHY_PAYLOAD)
    legacy_item = original.to_item()
    legacy_item["payload"] = to_dynamodb_value(COSTCO_TYPOGRAPHY_PAYLOAD)

    with pytest.raises(ValueError, match="legacy AttributeValue"):
        MerchantTruthComponent.from_item(legacy_item)
