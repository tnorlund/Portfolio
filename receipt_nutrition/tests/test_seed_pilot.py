"""Seeding products and aliases from a pilot lookup file is safe and idempotent."""

import importlib.util
from collections import Counter
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from receipt_dynamo import DynamoClient

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "seed_nutrition_pilot.py"
)
spec = importlib.util.spec_from_file_location("seed_nutrition_pilot", SCRIPT)
seed = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(seed)


def record(**changes):
    base = {
        "merchant": "trader joe's",
        "line_text": "TATER BITES POTATO WITH",
        "normalized": "TATER BITES POTATO WITH",
        "class": "panel",
        "lane": "traderjoes",
        "product_name": "Tater Bites with Cheese and Chives",
        "brand": "Trader Joe's",
        "source": "traderjoes",
        "source_id": "084621",
        "source_url": "https://www.traderjoes.com/home/products/pdp/x-084621",
        "size": "4.6 oz",
        "serving_size": "1 container (130g)",
        "servings_per_container": "Serves 1",
        "nutrients": {
            "208": {"value": 280.0, "unit": "kcal"},
            "204": {"value": 18.0, "unit": "g"},
            "307": {"value": 0.42, "unit": "g"},
            "203": {"value": 16.0, "unit": "g"},
            "999": {"value": 1.0, "unit": "g"},
        },
        "confidence": 0.97,
        "candidates": [{"sku": "084621", "score": 0.97}],
        "status_in_lane": "matched",
        "fetched_at": "2026-09-08T20:00:00+00:00",
    }
    base.update(changes)
    return base


def test_retailer_panel_becomes_per_serving_product():
    product, note = seed.build_product(record())
    assert note == "ok"
    assert product is not None
    assert product.product_id == "tj:084621"
    facts = {f.nutrient_id: f for f in product.nutrients}
    assert set(facts) == {"208", "204", "307", "203"}
    assert facts["307"].unit == "mg" and str(facts["307"].amount) == "420"
    assert all(f.basis == "serving" for f in facts.values())
    assert product.serving is not None and product.serving.unit == "g"
    assert str(product.serving.value) == "130"
    assert product.net_amount is not None and product.net_amount.unit == "g"
    assert str(product.servings_per_container) == "1"
    assert product.evidence[0].source == "tj"
    assert product.evidence[0].public_allowed is False


def test_unparsed_serving_keeps_identity_and_drops_facts():
    product, note = seed.build_product(
        record(serving_size="1/4 cup", servings_per_container="8")
    )
    assert note == "serving_unparsed_identity_only"
    assert product is not None
    assert product.nutrients == ()
    assert product.serving is None
    assert product.servings_per_container is None
    assert product.serving_household == "1/4 cup"


def test_fallback_generic_is_per_100g_and_public():
    product, note = seed.build_product(
        record(
            lane="fallback",
            source="fdc",
            source_id="171287",
            status_in_lane="matched_generic",
            product_name="Bananas, raw",
            serving_size="100",
            serving_unit="g",
            size=None,
            servings_per_container=None,
            nutrients={"208": 89.0, "203": 1.09},
            source_url=None,
        )
    )
    assert note == "ok"
    assert product is not None
    assert product.identity_kind == "generic"
    assert product.evidence[0].source == "fdc"
    assert product.evidence[0].public_allowed is True
    assert {f.basis for f in product.nutrients} == {"100g"}


@pytest.mark.parametrize("cls", ["ambiguous", "not_food", "no_source"])
def test_non_matches_store_no_product(cls):
    product, note = seed.build_product(record(**{"class": cls}))
    assert product is None and note == cls


@pytest.fixture
def table():
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        name = "SeedPilotTable"
        dynamodb.create_table(
            TableName=name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        dynamodb.Table(name).wait_until_exists()
        yield name


def test_seed_is_idempotent_and_respects_user_decisions(table):
    client = DynamoClient(table)
    records = [
        record(),
        record(
            **{
                "class": "ambiguous",
                "normalized": "LEMON EACH",
                "line_text": "LEMON EACH",
            }
        ),
        record(
            **{
                "class": "not_food",
                "normalized": "BUTTER CHARD",
                "line_text": "BUTTER CHARD",
            }
        ),
    ]
    first = seed.seed(records, client, table)
    assert first["product_written"] == 1
    assert first["alias_written"] == 3
    assert first["alias:matched"] == 1 and first["alias:pending"] == 1
    assert first["alias:not_food"] == 1
    second = seed.seed(records, client, table)
    assert second["alias_unchanged"] == 3 and second["product_exists"] == 1
    assert "alias_written" not in second

    alias = client.get_product_alias(
        "trader-joe-s", "TEXT", "TATER BITES POTATO WITH"
    )
    assert alias is not None and alias.status == "matched"
    assert alias.product_id == "tj:084621"
    stored = client.get_food_product(alias.product_id, alias.product_revision)
    assert stored is not None

    # A user decision on the same text is never overwritten by a later seed.
    from datetime import datetime, timezone

    from receipt_dynamo.entities.product_alias import ProductAlias

    user = ProductAlias(
        merchant_slug="trader-joe-s",
        kind="TEXT",
        text="TATER BITES POTATO WITH",
        revision=alias.revision + 1,
        status="not_food",
        method="user",
        changed_at=datetime.now(timezone.utc).isoformat(
            timespec="milliseconds"
        ),
        applicability_json='{"merchant": "trader joe\'s"}',
        confirmed_by_user=True,
    )
    client.save_product_alias(
        user, expected_revision=alias.revision, expected_table_name=table
    )
    third = seed.seed(records, client, table)
    assert third["alias_kept_user"] == 1
    kept = client.get_product_alias(
        "trader-joe-s", "TEXT", "TATER BITES POTATO WITH"
    )
    assert (
        kept is not None
        and kept.status == "not_food"
        and kept.confirmed_by_user
    )


def test_prod_table_is_refused(capsys):
    with pytest.raises(SystemExit):
        seed.main([str(SCRIPT), "--apply", "--table", "ReceiptsTable-d7ff76a"])
    assert "refusing to seed the prod table" in capsys.readouterr().err
