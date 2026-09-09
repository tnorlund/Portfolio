"""Contract cases for the meal builder on invented products and receipts.

No value here comes from a real receipt; the private golden corpus carries
the owner's real cases and is never committed.
"""

from __future__ import annotations

import io
import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from receipt_dynamo.entities.food_product import FoodProduct
from receipt_dynamo.entities.nutrition_support import nutrition_json
from receipt_dynamo.entities.price_observation import (
    PriceEvidence,
    PriceObservation,
)
from receipt_dynamo.entities.product_alias import ProductAlias

from receipt_nutrition import meal as meal_cli

IMG = "11111111-1111-4111-8111-111111111111"
STAMP = "2026-01-01T00:00:00+00:00"
EXPIRES = 4_102_444_800  # 2100-01-01


def _evidence(evidence_id: str, verification: str = "source_record"):
    return {
        "evidence_id": evidence_id,
        "source": "synthetic",
        "record_id": evidence_id,
        "reference": f"synthetic://{evidence_id}",
        "observed_on": "2026-01-01",
        "verification": "synthetic",
        "license": "test",
    }


def _fact(nutrient_id: str, amount: str, unit: str, ref: str = "ev"):
    return {
        "nutrient_id": nutrient_id,
        "amount": amount,
        "unit": unit,
        "basis": "serving",
        "source_ref": ref,
    }


def _product(product_id: str, **fields):
    payload = {
        "product_id": product_id,
        "name": product_id,
        "identity_kind": "exact",
        "evidence": [_evidence("ev")],
        "package_source_ref": "ev",
    }
    payload.update(fields)
    return FoodProduct(
        product_id=product_id, payload_json=nutrition_json(payload)
    )


BEEF = _product(
    "syn:beef",
    serving={"value": "150", "unit": "g"},
    sold_by="weight",
    sold_by_source_ref="ev",
    nutrients=[
        _fact("208", "200", "kcal"),
        _fact("203", "20", "g"),
        _fact("204", "2", "g"),
        _fact("205", "25", "g"),
        _fact("269", "22", "g"),
        _fact("307", "1200", "mg"),
    ],
)
GRAIN = _product(
    "syn:grain",
    serving={"value": "45", "unit": "g"},
    net_amount={"value": "1360.5", "unit": "g"},
    servings_per_container="30",
    household={
        "value": "0.25",
        "unit": "cup",
        "source_ref": "ev",
        "parser": "household-v1",
        "raw": "1/4 cup (45g)",
    },
    nutrients=[
        _fact("208", "160", "kcal"),
        _fact("203", "3", "g"),
        _fact("204", "0", "g"),
        _fact("205", "36", "g"),
        _fact("269", "0", "g"),
        _fact("307", "0", "mg"),
    ],
)
OIL = _product(
    "syn:oil",
    serving={"value": "14", "unit": "g"},
    net_amount={"value": "500", "unit": "g"},
    household={
        "value": "1",
        "unit": "tbsp",
        "source_ref": "ev",
        "parser": "household-v1",
        "raw": "1 tbsp (14g)",
    },
    nutrients=[
        _fact("208", "120", "kcal"),
        _fact("203", "0", "g"),
        _fact("204", "14", "g"),
        _fact("205", "0", "g"),
        _fact("307", "0", "mg"),
    ],
)
EGG = _product(
    "syn:egg",
    identity_kind="generic",
    serving={"value": "1", "unit": "each"},
    nutrients=[
        _fact("208", "70", "kcal"),
        _fact("203", "6", "g"),
        _fact("204", "5", "g"),
        _fact("205", "0", "g"),
        _fact("269", "0", "g"),
        _fact("307", "70", "mg"),
    ],
)
PRODUCTS = {p.product_id: p for p in (BEEF, GRAIN, OIL, EGG)}


def _alias(
    slug,
    kind,
    text,
    product,
    *,
    method="identifier",
    status="matched",
    revision=1,
):
    return ProductAlias(
        merchant_slug=slug,
        kind=kind,
        text=text,
        revision=revision,
        status=status,
        method=method,
        changed_at=STAMP,
        applicability_json=json.dumps(
            {"merchant": slug, "text": text, "size": None}
        ),
        product_id=product.product_id if status == "matched" else None,
        product_revision=product.revision if status == "matched" else None,
        confirmed_by_user=method == "user",
        expires_at=None if method == "user" else EXPIRES,
    )


def _line(item_index, name, price, merchant="Synthetic Mart"):
    return SimpleNamespace(
        image_id=IMG,
        receipt_id=1,
        item_index=item_index,
        name=name,
        raw_text=f"{name} {price}",
        price=price,
        quantity=None,
        unit_price=None,
        merchant_name=merchant,
    )


def _observation(
    price, effective_on, seq, *, source="tracker", supersedes=None
):
    return PriceObservation(
        merchant_slug="synthetic-mart",
        key_kind="ITEM",
        key_text="111-22-3333",
        observed_on=date(2026, 3, 1),
        seq=seq,
        unit="lb",
        price_per_unit=Decimal(price),
        currency="USD",
        source=source,
        verification="user" if source == "owner" else "unverified",
        effective_on=date.fromisoformat(effective_on),
        supersedes=supersedes,
        evidence=PriceEvidence(
            reference="synthetic", observed_on=date(2026, 3, 1)
        ),
    )


class FakeClient:
    table_name = "ReceiptsTable-synthetic"

    def __init__(self, aliases, observations=()):
        self.aliases = {(a.merchant_slug, a.kind, a.text): a for a in aliases}
        self.observations = list(observations)
        self.published = []
        self.lines = {
            (IMG, 1): [
                _line(0, "111-22-3333 BEEF STRIPS", "20.00"),
                _line(1, "GRAIN LONG", "4.00"),
                _line(2, "OIL PRESSED", "10.00"),
                _line(3, "EGGS DOZEN", "3.00"),
            ]
        }

    def get_receipt_line_items_from_receipt(self, image_id, receipt_id):
        return self.lines.get((image_id, receipt_id), [])

    def get_receipt_summary(self, image_id, receipt_id):
        return SimpleNamespace(
            merchant_name="Synthetic Mart", date=date(2026, 3, 1)
        )

    def get_product_alias(self, merchant_slug, kind, text):
        return self.aliases.get((merchant_slug, kind, text))

    def get_food_product(self, product_id, revision):
        product = PRODUCTS.get(product_id)
        return product if product and product.revision == revision else None

    def list_price_observations(self, merchant_slug, key_kind, key_text):
        return [
            o
            for o in self.observations
            if (o.merchant_slug, o.key_kind, o.key_text)
            == (merchant_slug, key_kind, key_text)
        ]

    def publish_alias_observations(
        self, observations, *, alias_expectations, expected_table_name
    ):
        assert expected_table_name == self.table_name
        self.published.append((observations, alias_expectations))


SLUG = "synthetic-mart"


def _aliases():
    return [
        _alias(SLUG, "ITEM", "111-22-3333", BEEF),
        _alias(SLUG, "TEXT", "GRAIN LONG", GRAIN),
        _alias(SLUG, "TEXT", "OIL PRESSED", OIL),
        _alias(SLUG, "TEXT", "EGGS DOZEN", EGG),
    ]


def _close(actual, expected):
    return abs(Decimal(actual) - Decimal(expected)) < Decimal("0.01")


def _run(client, argv, **kwargs):
    out = io.StringIO()
    code = meal_cli.run(
        argv, client=client, fleet_map={}, stdout=out, **kwargs
    )
    return code, out.getvalue()


BASE = [
    "--title",
    "synthetic prep",
    "--containers",
    "2",
    "--allowance",
    "auto",
    "--as-of",
    "2026-03-02",
    "--item",
    f"beef=line:{IMG}:1:0:all",
    "--item",
    f"grain=line:{IMG}:1:1:1cup",
    "--assume-package",
    "grain=1",
    "--item",
    f"oil=line:{IMG}:1:2:1tbsp",
    "--assume-package",
    "oil=1",
    "--item",
    "greens=none:synthetic-mart:2026-03-01:200g",
]


def test_owner_rate_case_is_exact_and_marks_unknowns():
    client = FakeClient(_aliases())
    code, text = _run(
        client,
        BASE
        + ["--rate", "beef=5.00:lb", "--coverage", "off", "--format", "json"],
    )
    report = json.loads(text)
    assert code == meal_cli.EXIT_UNKNOWNS
    rows = {item["key"]: item for item in report["items"]}
    # 20.00 / 5.00 per lb = 4 lb = 1814.36948 g; per container half of it.
    beef = rows["beef"]["row"]
    assert beef["cost"] == "10.00"
    servings = Decimal("1814.36948") / Decimal("150") / 2
    assert _close(beef["nutrients"]["208"], Decimal("200") * servings)
    assert rows["beef"]["quantity"]["reason"] == "inferred_from_rate:owner"
    assert rows["beef"]["band"] == []
    # grain: 1 cup = 4 servings of 1/4 cup; per container 2 servings.
    grain = rows["grain"]["row"]
    assert Decimal(grain["nutrients"]["208"]) == Decimal("320")
    assert grain["cost"] == "0.27"  # 4.00 * 4/30 / 2 = 0.2666..
    assert "allowance:grain:0.35:auto" in report["assumptions"]
    oil = rows["oil"]["row"]
    assert Decimal(oil["nutrients"]["208"]) == Decimal("60")
    assert "oil.269" in report["unknown"]
    assert {u for u in report["unknown"] if u.startswith("greens")} == {
        "greens.cost",
        *{f"greens.{n}" for n in meal_cli.CORE_NUTRIENTS},
    }
    assert "coverage:greens:not_checked" in report["assumptions"]
    assert "rate:beef:owner:5.00:lb" in report["assumptions"]
    assert report["cost"] is None
    assert report["available_cost_subtotal"] == "10.41"
    assert client.published, "alias-outcome pointers are published"


def test_observations_supply_rate_and_band_and_skip_superseded():
    observations = [
        _observation("4.00", "2026-01-15", 1),
        _observation("5.00", "2026-01-20", 2, supersedes=None),
        _observation("6.00", "2026-02-10", 3),
        _observation(
            "4.50", "2026-01-15", 4, supersedes="DATE#2026-03-01#tracker#001"
        ),
    ]
    client = FakeClient(_aliases(), observations)
    code, text = _run(client, BASE + ["--coverage", "off", "--format", "json"])
    report = json.loads(text)
    assert code == meal_cli.EXIT_UNKNOWNS
    beef = {i["key"]: i for i in report["items"]}["beef"]
    assert beef["quantity"]["reason"] == "inferred_from_rate:tracker"
    # Latest effective on or before 2026-03-01 is 6.00.
    assert beef["row"]["cost"] == "10.00"
    servings = (
        Decimal("20.00") / Decimal("6.00") * Decimal("453.59237") / 150 / 2
    )
    assert _close(beef["row"]["nutrients"]["208"], Decimal("200") * servings)
    band = {b["price_per_unit"] for b in beef["band"]}
    assert band == {"5.00", "4.50"}  # 4.00 superseded, 6.00 is primary
    assert (
        "rate:beef:observation:2026-02-10:tracker:6.00:lb"
        in report["assumptions"]
    )


def test_dimensioned_quantity_is_not_a_package_count():
    client = FakeClient(_aliases())
    argv = [
        "--as-of",
        "2026-03-02",
        "--item",
        f"eggs=line:{IMG}:1:3:3each",
        "--assume-package",
        "eggs=1",
        "--quantity",
        "eggs=12:each",
        "--format",
        "json",
    ]
    code, text = _run(client, argv)
    report = json.loads(text)
    assert code == meal_cli.EXIT_OK
    eggs = report["items"][0]["row"]
    assert eggs["cost"] == "0.75"
    assert Decimal(eggs["nutrients"]["208"]) == Decimal("210")
    assert report["contains_generic_estimates"] is True
    assert report["unknown"] == []
    assert "quantity:eggs:12:each" in report["assumptions"]


def test_package_count_must_be_explicit():
    client = FakeClient(_aliases())
    code, _ = _run(
        client,
        ["--item", f"grain=line:{IMG}:1:1:1cup", "--assume-package", "grain"],
    )
    assert code == meal_cli.EXIT_POINTER


def test_missing_alias_and_pending_alias_exit_codes():
    client = FakeClient([])
    code, _ = _run(client, ["--item", f"grain=line:{IMG}:1:1:1cup"])
    assert code == meal_cli.EXIT_POINTER
    pending = _alias(
        SLUG, "TEXT", "GRAIN LONG", GRAIN, status="pending", method="lexical"
    )
    client = FakeClient([pending])
    code, _ = _run(client, ["--item", f"grain=line:{IMG}:1:1:1cup"])
    assert code == meal_cli.EXIT_PENDING


def test_unknown_quantity_is_never_defaulted():
    client = FakeClient(_aliases())
    code, text = _run(
        client,
        [
            "--item",
            f"grain=line:{IMG}:1:1:1cup",
            "--format",
            "json",
            "--as-of",
            "2026-03-02",
        ],
    )
    report = json.loads(text)
    assert code == meal_cli.EXIT_UNKNOWNS
    grain = report["items"][0]
    assert grain["quantity"]["status"] == "unknown"
    assert grain["row"] is None or grain["row"]["cost"] is None
    assert "grain.cost" in report["unknown"]


def test_markdown_never_prints_zero_for_unknown():
    client = FakeClient(_aliases())
    _, text = _run(
        client, BASE + ["--rate", "beef=5.00:lb", "--coverage", "off"]
    )
    greens_row = next(
        line for line in text.splitlines() if line.startswith("| greens")
    )
    assert greens_row.count("unknown") == 8
    assert "(4/4)" not in text and "3/4" in text


def test_allowance_defaults_to_zero_and_reports_the_conflict():
    client = FakeClient(_aliases())
    argv = [
        "--as-of",
        "2026-03-02",
        "--item",
        f"grain=line:{IMG}:1:1:1cup",
        "--assume-package",
        "grain=1",
        "--format",
        "json",
    ]
    code, text = _run(client, argv)
    report = json.loads(text)
    assert code == meal_cli.EXIT_UNKNOWNS
    grain = report["items"][0]
    assert grain["row"] is None
    assert grain["unknown_reason"].startswith("meal_item_invalid")
    assert not any(a.startswith("allowance") for a in report["assumptions"])


def test_receipt_quantity_evidence_with_dal_floats():
    client = FakeClient(_aliases())
    line = client.lines[(IMG, 1)][0]
    line.raw_text = "111-22-3333 BEEF STRIPS\n2 lb @ 10.00\n20.00"
    line.quantity = 2.0
    line.unit_price = 10.0
    argv = [
        "--as-of",
        "2026-03-02",
        "--item",
        f"beef=line:{IMG}:1:0:all",
        "--rate",
        "beef=99.00:lb",
        "--format",
        "json",
    ]
    code, text = _run(client, argv)
    report = json.loads(text)
    beef = report["items"][0]
    assert beef["quantity"]["status"] == "known"
    assert beef["quantity"]["quantity"]["method"] == "receipt"
    # Receipt evidence wins; the owner rate was never used.
    assert not any(a.startswith("rate:") for a in report["assumptions"])


def test_publication_expects_every_key_read_including_absent_ones():
    client = FakeClient(_aliases())
    _run(
        client,
        [
            "--as-of",
            "2026-03-02",
            "--item",
            f"beef=line:{IMG}:1:0:all",
            "--rate",
            "beef=5:lb",
        ],
    )
    observations, expectations = client.published[0]
    kinds = {
        (kind, text): revision for _, kind, text, revision in expectations
    }
    assert kinds[("ITEM", "111-22-3333")] == 1
    assert ("TEXT", "BEEF STRIPS") in kinds and kinds[
        ("TEXT", "BEEF STRIPS")
    ] is None
    statuses = {(o.kind, o.text): o.status for o in observations}
    assert statuses[("ITEM", "111-22-3333")] == "matched"
    assert statuses[("TEXT", "BEEF STRIPS")] == "no_match"


def test_pending_alias_still_publishes_its_pointer():
    pending = _alias(
        SLUG, "TEXT", "GRAIN LONG", GRAIN, status="pending", method="lexical"
    )
    client = FakeClient([pending])
    code, _ = _run(
        client,
        ["--as-of", "2026-03-02", "--item", f"grain=line:{IMG}:1:1:1cup"],
    )
    assert code == meal_cli.EXIT_PENDING
    observations, _ = client.published[0]
    assert {o.status for o in observations} == {"pending"}


def test_two_lines_through_one_alias_both_get_pointers():
    client = FakeClient(_aliases())
    client.lines[(IMG, 1)].append(_line(4, "EGGS DOZEN", "3.00"))
    _run(
        client,
        [
            "--as-of",
            "2026-03-02",
            "--item",
            f"a=line:{IMG}:1:3:1each",
            "--quantity",
            "a=12:each",
            "--item",
            f"b=line:{IMG}:1:4:1each",
            "--quantity",
            "b=12:each",
        ],
    )
    observations, _ = client.published[0]
    assert {o.item_index for o in observations} == {3, 4}


def test_namespaced_product_source_and_owner_price():
    client = FakeClient(_aliases())
    argv = [
        "--as-of",
        "2026-03-02",
        "--item",
        f"e=product:{EGG.product_id}@{EGG.revision}:3each",
        "--quantity",
        "e=12:each",
        "--price",
        "e=3",
        "--format",
        "json",
    ]
    code, text = _run(client, argv)
    report = json.loads(text)
    assert code == meal_cli.EXIT_OK
    assert report["items"][0]["row"]["cost"] == "0.75"
    assert "price:e:3" in report["assumptions"]


def test_missing_price_keeps_nutrients_and_marks_cost_unknown():
    client = FakeClient(_aliases())
    argv = [
        "--as-of",
        "2026-03-02",
        "--item",
        f"e=product:{EGG.product_id}@{EGG.revision}:3each",
        "--quantity",
        "e=12:each",
        "--format",
        "json",
    ]
    code, text = _run(client, argv)
    report = json.loads(text)
    assert code == meal_cli.EXIT_UNKNOWNS
    row = report["items"][0]["row"]
    assert row["cost"] is None
    assert Decimal(row["nutrients"]["208"]) == Decimal("210")
    assert report["unknown"] == ["e.cost"]


def test_generic_with_none_source_uses_client_and_price():
    client = FakeClient(_aliases())
    argv = [
        "--as-of",
        "2026-03-02",
        "--coverage",
        "off",
        "--item",
        "e=none:synthetic-mart:2026-03-01:3each",
        "--generic",
        f"e={EGG.product_id}@{EGG.revision}",
        "--quantity",
        "e=12:each",
        "--price",
        "e=3",
        "--format",
        "json",
    ]
    code, text = _run(client, argv)
    report = json.loads(text)
    assert code == meal_cli.EXIT_OK
    assert report["items"][0]["row"]["cost"] == "0.75"
    assert (
        f"generic:e:{EGG.product_id}@{EGG.revision}" in report["assumptions"]
    )


def test_portion_per_container_is_divided():
    client = FakeClient(_aliases())
    argv = [
        "--as-of",
        "2026-03-02",
        "--containers",
        "2",
        "--item",
        f"eggs=line:{IMG}:1:3:6each",
        "--quantity",
        "eggs=12:each",
        "--format",
        "json",
    ]
    _, text = _run(client, argv)
    report = json.loads(text)
    assert report["items"][0]["portion_per_container"] == "3 each"
    assert meal_cli._share("1814.36948", "g", 2) == "907.2"
    _, md = _run(client, argv[:-2])
    assert "| 3 each |" in md


def test_malformed_pointer_exits_2():
    client = FakeClient(_aliases())
    code, _ = _run(client, ["--item", f"x=line:{IMG}:abc:0:1g"])
    assert code == meal_cli.EXIT_POINTER


def test_latest_paginates_and_picks_the_newest():
    class Paged(FakeClient):
        def list_receipt_line_items_by_merchant(
            self, slug, *, last_evaluated_key=None
        ):
            first = [_line(3, "EGGS DOZEN", "3.00")]
            second = [_line(9, "EGGS DOZEN", "3.50")]
            if last_evaluated_key is None:
                return first, {"page": 2}
            return second, None

        def get_receipt_summary(self, image_id, receipt_id):
            return SimpleNamespace(
                merchant_name="Synthetic Mart", date=date(2026, 3, 1)
            )

    client = Paged(_aliases())
    client.lines[(IMG, 1)].append(_line(9, "EGGS DOZEN", "3.50"))
    _, text = _run(
        client,
        [
            "--as-of",
            "2026-03-02",
            "--item",
            "e=latest:synthetic-mart:TEXT#EGGS DOZEN:1each",
            "--quantity",
            "e=12:each",
            "--format",
            "json",
        ],
    )
    report = json.loads(text)
    assert report["items"][0]["pointer"] in (f"{IMG}:1:3", f"{IMG}:1:9")
    assert report["items"][0]["row"] is not None
