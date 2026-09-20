"""Seeding products and aliases from a pilot lookup file is safe and idempotent."""

import importlib.util
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from pydantic import ValidationError
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.product_alias import ProductAlias

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "seed_nutrition_pilot.py"
)
spec = importlib.util.spec_from_file_location("seed_nutrition_pilot", SCRIPT)
seed = importlib.util.module_from_spec(spec)
assert spec.loader is not None
# Registering the module lets pydantic resolve the script's deferred
# annotations the same way it does when the script runs as __main__.
sys.modules[spec.name] = seed
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
    assert product.household is not None
    assert (product.household.value, product.household.unit) == (
        Decimal("0.25"),
        "cup",
    )


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
    first = seed.seed(records, client, table, apply=True)
    assert first["products_new"] == first["products_written"] == 1
    assert first["alias_new"] == first["alias_written"] == 3
    assert first["alias:matched"] == 1 and first["alias:pending"] == 1
    assert first["alias:not_food"] == 1
    second = seed.seed(records, client, table, apply=True)
    assert second["alias_unchanged"] == 3
    assert second["products_existing"] == 1
    assert "alias_written" not in second and second["writes_proposed"] == 0

    alias = client.get_product_alias(
        "trader-joe-s", "TEXT", "TATER BITES POTATO WITH"
    )
    assert alias is not None and alias.status == "matched"
    assert alias.product_id == "tj:084621"
    stored = client.get_food_product(alias.product_id, alias.product_revision)
    assert stored is not None

    # A user decision on the same text is never overwritten by a later seed.
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
    third = seed.seed(records, client, table, apply=True)
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


@pytest.mark.parametrize("serving", [".5 g", "1/2 g", "1/4 cup"])
def test_partial_numbers_never_become_a_serving(serving):
    product, note = seed.build_product(record(serving_size=serving))
    assert note == "serving_unparsed_identity_only"
    assert product is not None and product.serving is None


def test_small_calories_unit_is_dropped_not_scaled():
    product, _ = seed.build_product(
        record(nutrients={"208": {"value": 1000, "unit": "cal"}})
    )
    assert product is not None and product.nutrients == ()


def test_missing_fetched_at_is_stable_across_runs():
    first, _ = seed.build_product(record(fetched_at=None))
    second, _ = seed.build_product(record(fetched_at=None))
    assert first is not None and second is not None
    assert first.content_hash == second.content_hash
    assert first.evidence[0].observed_on == seed.PILOT_OBSERVED_ON


def test_numeric_size_still_yields_pending_alias():
    alias = seed.alias_for(
        record(**{"class": "ambiguous", "size": 12.0}),
        None,
        None,
        1,
        datetime.now(timezone.utc),
    )
    assert alias.status == "pending"
    assert '"size":"12.0"' in alias.applicability_json


def test_multipack_size_abstains():
    product, _ = seed.build_product(record(size="6 x 12 oz"))
    assert product is not None and product.net_amount is None
    single, _ = seed.build_product(record(size="12 oz"))
    assert single is not None and single.net_amount is not None


def test_unparseable_nutrient_value_is_skipped_not_fatal():
    product, note = seed.build_product(
        record(
            serving_size="1/4 cup",
            nutrients={"208": {"value": "unknown", "unit": "kcal"}},
        )
    )
    assert note in ("ok", "serving_unparsed_identity_only")
    assert product is not None and product.nutrients == ()


def test_prod_table_is_refused_even_without_apply(capsys):
    with pytest.raises(SystemExit):
        seed.main([str(SCRIPT), "--table", "copy-d7ff76a-of-prod"])
    assert "refusing to seed the prod table" in capsys.readouterr().err


def test_changed_facts_repoint_existing_alias(table):
    client = DynamoClient(table)
    seed.seed([record()], client, table, apply=True)
    before = client.get_product_alias(
        "trader-joe-s", "TEXT", "TATER BITES POTATO WITH"
    )
    changed = record(nutrients={"208": {"value": 300.0, "unit": "kcal"}})
    counts = seed.seed([changed], client, table, apply=True)
    assert counts["products_new"] == 1 and counts["alias_repin"] == 1
    assert counts["alias_written"] == 1
    after = client.get_product_alias(
        "trader-joe-s", "TEXT", "TATER BITES POTATO WITH"
    )
    assert before is not None and after is not None
    assert after.revision == before.revision + 1
    assert after.product_revision != before.product_revision


@pytest.mark.parametrize("serving", ["1 / 2 g", "2 / 3 cup (1 / 2 g)"])
def test_spaced_fraction_serving_abstains(serving):
    product, note = seed.build_product(record(serving_size=serving))
    assert note == "serving_unparsed_identity_only"
    assert product is not None and product.serving is None


@pytest.mark.parametrize("size", [".5 oz", "1/2 lb", "1 / 2 lb"])
def test_partial_number_size_abstains(size):
    product, _ = seed.build_product(record(size=size))
    assert product is not None and product.net_amount is None


@pytest.mark.parametrize("count", ["about 2 1/2", "2 1/2", "Serves 2 to 3"])
def test_fractional_or_ranged_servings_abstain(count):
    product, _ = seed.build_product(record(servings_per_container=count))
    assert product is not None and product.servings_per_container is None
    plain, _ = seed.build_product(record(servings_per_container="Serves 4"))
    assert plain is not None and str(plain.servings_per_container) == "4"


def test_float_noise_in_facts_is_quantised_not_fatal():
    noisy = {"203": {"value": 0.30000000000000004, "unit": "g"}}
    product, note = seed.build_product(record(nutrients=noisy))
    assert note == "ok" and product is not None
    (fact,) = product.nutrients
    assert str(fact.amount) == "0.3"
    identity, note = seed.build_product(
        record(serving_size="1/4 cup", nutrients=noisy)
    )
    assert identity is not None and identity.nutrients == ()


@pytest.mark.parametrize(
    "serving",
    ["1 /  2 g", "2-3 g", "2 \u2013 3 g", "1 to 2 fl oz", "1 TO 2 oz"],
)
def test_ranges_and_wide_fractions_abstain(serving):
    product, note = seed.build_product(record(serving_size=serving))
    assert note == "serving_unparsed_identity_only"
    assert product is not None and product.serving is None


def test_leading_decimal_servings_count_abstains():
    product, _ = seed.build_product(record(servings_per_container=".5"))
    assert product is not None and product.servings_per_container is None


def test_exponent_nutrient_value_is_skipped_not_fatal():
    product, _ = seed.build_product(
        record(
            serving_size="1/4 cup",
            nutrients={"208": {"value": "1e100", "unit": "kcal"}},
        )
    )
    assert product is not None and product.nutrients == ()


def test_colliding_alias_keys_are_written_once(table):
    client = DynamoClient(table)
    rows = [
        record(merchant="Trader Joe's"),
        record(
            merchant="Trader Joe s",
            nutrients={"208": {"value": 300.0, "unit": "kcal"}},
        ),
    ]
    first = seed.seed(rows, client, table, apply=True)
    assert first["alias_written"] == 1
    assert first["alias_duplicate_key_skipped"] == 1
    assert first["products_written"] == 2
    second = seed.seed(rows, client, table, apply=True)
    assert second["alias_unchanged"] == 1 and "alias_written" not in second
    assert second["writes_proposed"] == 0


def test_text_collision_keeps_distinct_item_aliases(table):
    client = DynamoClient(table)
    rows = [
        costco_record(),
        costco_record(
            normalized="BEEF-BULGOGI",
            line_text="E 22222 BEEF-BULGOGI",
            source_id="22222",
        ),
    ]
    counts = seed.seed(rows, client, table, apply=True)
    assert counts["alias_duplicate_key_skipped"] == 1
    assert counts["alias_new:TEXT"] == 1 and counts["alias_new:ITEM"] == 2
    second = client.get_product_alias("costco-wholesale", "ITEM", "22222")
    assert second is not None and second.product_id == "costco:22222"
    assert seed.seed(rows, client, table)["writes_proposed"] == 0


def test_plan_counts_a_shared_revision_once(table):
    client = DynamoClient(table)
    rows = [record(), record(normalized="TATER BITES")]
    plan = seed.seed(rows, client, table)
    applied = seed.seed(rows, client, table, apply=True)
    assert plan["writes_proposed"] == applied["writes_proposed"] == 3
    assert plan["products_new"] == applied["products_written"] == 1
    assert plan["products_existing"] == 1
    assert seed.seed(rows, client, table)["writes_proposed"] == 0


@pytest.mark.parametrize(
    ("serving", "grams"),
    [
        ("1/2 cup, 100g", "100"),
        ("1 1/4 cup (140g)", "140"),
        ("1/2 CUP 121 Gram", "121"),
    ],
)
def test_household_fraction_does_not_block_explicit_grams(serving, grams):
    product, note = seed.build_product(record(serving_size=serving))
    assert note == "ok" and product is not None
    assert product.serving is not None and str(product.serving.value) == grams


def test_oz_is_mass_and_fl_oz_is_volume_without_rounding():
    mass = seed.parse_serving("approximately 5 oz")
    volume = seed.parse_serving("5 fl oz")
    assert mass is not None and volume is not None
    assert (str(mass.value), mass.unit) == ("141.747615625", "g")
    assert (str(volume.value), volume.unit) == ("147.8676478125", "ml")
    fluid = seed.parse_serving("12.0 Fluid ounce (US)")
    assert fluid is not None and fluid.unit == "ml"


def test_printed_metric_beats_customary_on_the_same_label():
    both = seed.parse_serving("6.75 oz/191g")
    assert both is not None and (str(both.value), both.unit) == ("191", "g")
    spoon = seed.parse_serving("1 Tbsp/14g")
    assert spoon is not None and str(spoon.value) == "14"


def test_customary_serving_stores_facts_instead_of_identity_only():
    product, note = seed.build_product(
        record(
            lane="costco", source_id="36946", serving_size="approximately 5 oz"
        )
    )
    assert note == "ok" and product is not None
    assert product.serving is not None
    assert str(product.serving.value) == "141.747615625"
    assert len(product.nutrients) == 4


@pytest.mark.parametrize(
    "changes",
    [
        {"product_name": "Choice Beef Bulgogi (Korean BBQ) Per Lb"},
        {"product_name": "Pork Loin Tenderloin, Per Lb."},
        {"example_price": "6.99/lb"},
        {"size": "per lb"},
        {"weighed": True},
    ],
)
def test_per_pound_text_or_weighed_flag_marks_sold_by_weight(changes):
    product, _ = seed.build_product(
        record(lane="costco", source_id="36946", **changes)
    )
    assert product is not None
    assert product.sold_by == "weight"
    assert product.sold_by_source_ref == "src"


def test_pound_bag_is_not_sold_by_weight():
    product, _ = seed.build_product(record(size="2 Lb", weighed=False))
    assert product is not None and product.sold_by is None
    assert product.sold_by_source_ref is None
    assert product.net_amount is not None
    assert str(product.net_amount.value) == "907.18474"


@pytest.mark.parametrize(
    "size", ["1 lb", "1.0 lb", "1lb", "1 pound", "1 Lbs.", "  1 LB "]
)
def test_instacart_one_pound_default_net_is_dropped_and_counted(size):
    drops = Counter()
    product, _ = seed.build_product(
        record(
            lane="sprouts",
            source="sprouts",
            source_id="17855632",
            size=size,
            weighed=True,
        ),
        drops,
    )
    assert product is not None
    assert product.net_amount is None
    assert product.sold_by == "weight"
    assert drops["instacart_default_net"] == 1
    # The same text from a lane without the Instacart default is a package,
    # and a real Instacart size is kept.
    bag, _ = seed.build_product(record(size="1 Lb"))
    assert bag is not None and bag.net_amount is not None
    assert str(bag.net_amount.value) == "453.59237"
    packaged, _ = seed.build_product(
        record(lane="sprouts", source="sprouts", source_id="1", size="2 lb")
    )
    assert packaged is not None and packaged.net_amount is not None
    assert str(packaged.net_amount.value) == "907.18474"


@pytest.mark.parametrize(
    ("text", "value", "unit"),
    [
        ("1/4 cup", "0.25", "cup"),
        ("1 tbsp", "1", "tbsp"),
        ("2 tsp", "2", "tsp"),
        ("1 each", "1", "each"),
        ("1 1/4 cup (140g)", "1.25", "cup"),
        ("2 TBSP  36 Gram", "2", "tbsp"),
        ("1.5 cup serving (140g)", "1.5", "cup"),
    ],
)
def test_household_text_parses_to_equivalence(text, value, unit):
    household, reason = seed.parse_household(text)
    assert reason == "ok" and household is not None
    assert str(household.value) == value and household.unit == unit
    assert household.parser == "household-v1" and household.raw == text
    assert household.source_ref == "src"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("2/3 CUP  170 Gram", "household_inexact_fraction"),
        ("1-2 cups", "household_unparsed"),
        ("1 to 2 cups", "household_unparsed"),
        ("1/0 cup (140g)", "household_unparsed"),
        ("1 EGG  50 Gram", "household_unparsed"),
        ("1 container (130g)", "household_unparsed"),
        ("1 CUP POPPED", "ok"),
    ],
)
def test_household_text_that_cannot_be_exact_is_dropped(text, reason):
    household, actual = seed.parse_household(text)
    assert actual == reason and (household is None) == (reason != "ok")


def test_drop_reasons_are_counted_per_field():
    drops = Counter()
    seed.build_product(
        record(
            serving_size="9 chips",
            size="6 x 12 oz",
            servings_per_container="2 to 3",
        ),
        drops,
    )
    assert drops["serving_unparsed_identity_only"] == 1
    assert drops["household_unparsed"] == 1
    assert drops["size_unparsed"] == 1
    assert drops["nutrient_unparsed"] == 1
    assert drops["servings_per_container_without_serving"] == 1
    assert "servings_per_container_unparsed" not in drops
    ranged = Counter()
    seed.build_product(record(servings_per_container="2 to 3"), ranged)
    assert ranged["servings_per_container_unparsed"] == 1
    counts = seed.seed([record(serving_size="9 chips")], None, None)
    assert counts["drop:serving_unparsed_identity_only"] == 1


def costco_record(**changes):
    base = record(
        merchant="costco wholesale",
        lane="costco",
        source="costco",
        source_id="36946",
        line_text="E 36946 BEEF BULGOGI",
        normalized="BEEF BULGOGI",
        product_name="Choice Beef Bulgogi (Korean BBQ) Per Lb",
        serving_size="approximately 5 oz",
        servings_per_container=None,
        size=None,
        candidates=None,
    )
    base.update(changes)
    return base


@pytest.mark.parametrize(
    ("changes", "identifier"),
    [
        ({}, "36946"),
        ({"line_text": "BEEF BULGOGI", "item_number": "36946"}, "36946"),
        ({"line_text": "BEEF BULGOGI"}, "36946"),
        (
            {
                "lane": "target",
                "merchant": "target",
                "source": "target",
                "line_text": "VITAL FARMS NF",
                "dpci": "284030027",
            },
            "284-03-0027",
        ),
        (
            {
                "lane": "target",
                "merchant": "target",
                "source": "target",
                "dpci": "284-03-0027",
            },
            "284-03-0027",
        ),
        (
            {
                "lane": "fallback",
                "merchant": "vons",
                "source": "off",
                "status_in_lane": "matched_upc",
                "line_text": "7766117461 GNGRBRD CARAMEL S",
                "code": "7766117461",
            },
            "7766117461",
        ),
        (
            {
                "lane": "fallback",
                "merchant": "vons",
                "source": "off",
                "status_in_lane": "matched_upc",
                "line_text": "S CILANTRO ORGANIC 3338390419",
            },
            "3338390419",
        ),
        ({"lane": "traderjoes", "source": "traderjoes"}, None),
        ({"lane": "target", "merchant": "target", "source": "target"}, None),
    ],
)
def test_item_identifier_convention(changes, identifier):
    assert seed.item_identifier(costco_record(**changes)) == identifier


def test_item_alias_is_written_beside_the_text_alias(table):
    client = DynamoClient(table)
    counts = seed.seed([costco_record()], client, table, apply=True)
    assert counts["alias_new:ITEM"] == counts["alias_new:TEXT"] == 1
    text = client.get_product_alias("costco-wholesale", "TEXT", "BEEF BULGOGI")
    item = client.get_product_alias("costco-wholesale", "ITEM", "36946")
    assert text is not None and item is not None
    assert item.status == "matched" and item.method == "identifier"
    assert (item.product_id, item.product_revision) == (
        text.product_id,
        text.product_revision,
    )
    assert json.loads(item.applicability_json)["identifier"] == "36946"
    again = seed.seed([costco_record()], client, table, apply=True)
    assert again["alias_unchanged"] == 2 and again["writes_proposed"] == 0


def manual_entry(**changes):
    base = {
        "product_id": "costco:36946",
        "observed_on": "2026-09-09",
        "reference": "photo:IMG_4021 back panel",
        "serving": {"amount": "145", "unit": "g"},
        "servings_per_container": None,
        "nutrients": {
            "208": {"amount": "310", "unit": "kcal"},
            "203": {"amount": "20", "unit": "g"},
            "307": {"amount": "1200", "unit": "mg"},
            "269": {"amount": 0, "unit": "g"},
        },
        "notes": "printed US label",
    }
    base.update(changes)
    return base


def test_manual_evidence_rejects_floats_and_unknown_fields():
    with pytest.raises(ValidationError):
        seed.load_manual_evidence(
            [manual_entry(serving={"amount": 145.0, "unit": "g"})]
        )
    with pytest.raises(ValidationError):
        seed.load_manual_evidence([manual_entry(photo="x")])
    with pytest.raises(ValueError):
        seed.load_manual_evidence([manual_entry(), manual_entry()])
    (entry,) = seed.load_manual_evidence([manual_entry()])
    assert entry.serving.amount == Decimal("145")


def test_manual_nutrient_unit_mismatch_fails_at_load_time():
    bad = manual_entry(nutrients={"307": {"amount": "1.2", "unit": "g"}})
    with pytest.raises(ValidationError, match="307 must be in mg"):
        seed.load_manual_evidence([bad])
    kcal_as_g = manual_entry(nutrients={"208": {"amount": "1", "unit": "g"}})
    with pytest.raises(ValidationError):
        seed.load_manual_evidence([kcal_as_g])


def test_manual_revision_clears_stale_household_unless_supplied():
    base, _ = seed.build_product(costco_record(serving_size="1/4 cup (30 g)"))
    assert base is not None and base.household is not None
    drops = Counter()
    (bigger,) = seed.load_manual_evidence(
        [manual_entry(serving={"amount": "60", "unit": "g"})]
    )
    minted = seed.manual_product(base, bigger, drops)
    assert minted.household is None
    assert drops["manual_household_cleared"] == 1
    (same,) = seed.load_manual_evidence(
        [manual_entry(serving={"amount": "30", "unit": "g"})]
    )
    kept = seed.manual_product(base, same, Counter())
    assert kept.household == base.household
    (own,) = seed.load_manual_evidence(
        [
            manual_entry(
                serving={"amount": "60", "unit": "g"},
                household={"value": "0.5", "unit": "cup"},
            )
        ]
    )
    supplied = seed.manual_product(base, own, drops)
    assert supplied.household is not None
    assert (
        str(supplied.household.value),
        supplied.household.unit,
        supplied.household.source_ref,
    ) == ("0.5", "cup", "manual")
    assert drops["manual_household_cleared"] == 1


def test_manual_evidence_mints_revision_and_repins(table):
    client = DynamoClient(table)
    seed.seed([costco_record()], client, table, apply=True)
    storefront = client.get_product_alias(
        "costco-wholesale", "TEXT", "BEEF BULGOGI"
    )
    assert storefront is not None
    (entry,) = seed.load_manual_evidence([manual_entry()])
    counts = seed.seed(
        [costco_record()], client, table, apply=True, manual=[entry]
    )
    assert counts["manual_revision"] == 1
    assert counts["products_new"] == 1 and counts["products_existing"] == 1
    assert counts["alias_repin"] == 2
    pinned = client.get_product_alias(
        "costco-wholesale", "TEXT", "BEEF BULGOGI"
    )
    item = client.get_product_alias("costco-wholesale", "ITEM", "36946")
    assert pinned is not None and item is not None
    assert pinned.product_revision != storefront.product_revision
    assert item.product_revision == pinned.product_revision
    minted = client.get_food_product("costco:36946", pinned.product_revision)
    assert minted is not None
    from receipt_nutrition.persistence import product_from_record

    product = product_from_record(minted)
    assert product.serving is not None
    assert (str(product.serving.value), product.serving.unit) == ("145", "g")
    assert {s.source for s in product.evidence} == {"costco", "manual"}
    assert product.evidence[-1].verification == "user"
    assert product.evidence[-1].public_allowed is False
    assert product.sold_by == "weight"
    assert {f.source_ref for f in product.nutrients} == {"manual"}
    assert product.package_source_ref == "manual"
    # The storefront revision is still there, append-only.
    old = client.get_food_product("costco:36946", storefront.product_revision)
    assert old is not None
    replan = seed.seed([costco_record()], client, table, manual=[entry])
    assert replan["writes_proposed"] == 0


def test_manual_evidence_never_overwrites_a_user_alias(table):
    client = DynamoClient(table)
    seed.seed([costco_record()], client, table, apply=True)
    current = client.get_product_alias(
        "costco-wholesale", "TEXT", "BEEF BULGOGI"
    )
    assert current is not None
    user = ProductAlias(
        merchant_slug="costco-wholesale",
        kind="TEXT",
        text="BEEF BULGOGI",
        revision=current.revision + 1,
        status="matched",
        method="user",
        changed_at=datetime.now(timezone.utc).isoformat(
            timespec="milliseconds"
        ),
        applicability_json='{"merchant": "costco wholesale"}',
        product_id=current.product_id,
        product_revision=current.product_revision,
        confirmed_by_user=True,
    )
    client.save_product_alias(
        user, expected_revision=current.revision, expected_table_name=table
    )
    (entry,) = seed.load_manual_evidence([manual_entry()])
    counts = seed.seed(
        [costco_record()], client, table, apply=True, manual=[entry]
    )
    assert counts["alias_kept_user"] == 1 and counts["alias_repin"] == 1
    kept = client.get_product_alias("costco-wholesale", "TEXT", "BEEF BULGOGI")
    assert kept is not None and kept.confirmed_by_user
    assert kept.product_revision == current.product_revision


def test_manual_evidence_for_unseeded_product_is_counted_not_guessed(table):
    client = DynamoClient(table)
    (entry,) = seed.load_manual_evidence(
        [manual_entry(product_id="costco:999")]
    )
    counts = seed.seed([record()], client, table, manual=[entry])
    assert counts["drop:manual_evidence_unknown_product"] == 1
    assert "manual_revision" not in counts


def _row_count(table):
    return (
        boto3.resource("dynamodb", region_name="us-east-1")
        .Table(table)
        .scan(Select="COUNT")["Count"]
    )


def test_plan_reads_without_writing_then_apply_then_replan_is_zero(
    table, tmp_path, capsys
):
    merged = tmp_path / "merged.json"
    merged.write_text(json.dumps([record(), costco_record()]))
    labels = tmp_path / "manual.json"
    labels.write_text(json.dumps([manual_entry()]))
    argv = [str(merged), "--table", table, "--manual-evidence", str(labels)]
    assert seed.main(argv + ["--plan"]) == 0
    plan = capsys.readouterr().out
    assert plan.startswith("PLAN: 2 records, 1 manual labels")
    assert "products_new                             3" in plan
    assert "alias_new                                3" in plan
    assert "6 writes proposed" in plan
    assert "instacart_default_net" not in plan
    assert _row_count(table) == 0
    assert seed.main(argv + ["--apply"]) == 0
    applied = capsys.readouterr().out
    assert "6 writes written" in applied
    assert _row_count(table) == 6
    assert seed.main(argv + ["--plan"]) == 0
    replan = capsys.readouterr().out
    assert "0 writes proposed" in replan
    assert "products_existing                        3" in replan
    assert "alias_unchanged                          3" in replan
    assert "alias_new" not in replan and "alias_repin" not in replan
    assert _row_count(table) == 6
    pinned = DynamoClient(table).get_product_alias(
        "costco-wholesale", "ITEM", "36946"
    )
    assert pinned is not None
    stored = DynamoClient(table).get_food_product(
        "costco:36946", pinned.product_revision
    )
    assert stored is not None and '"manual"' in stored.payload_json


def test_prod_table_is_refused_before_input_is_read(tmp_path, capsys):
    missing = tmp_path / "does-not-exist.json"
    for flags in (["--plan"], ["--apply"], []):
        with pytest.raises(SystemExit):
            seed.main(
                [str(missing), "--table", "ReceiptsTable-d7ff76a", *flags]
            )
        assert "refusing to seed the prod table" in capsys.readouterr().err
    assert not missing.exists()


def test_plan_and_apply_need_a_table_and_exclude_each_other(tmp_path, capsys):
    merged = tmp_path / "merged.json"
    merged.write_text("[]")
    with pytest.raises(SystemExit):
        seed.main([str(merged), "--plan"])
    assert "require --table" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        seed.main([str(merged), "--plan", "--apply", "--table", "t"])
    assert "exclusive" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        seed.main([str(merged), "--plan", "--apply"])
    with pytest.raises(ValueError):
        seed.seed([], None, None, apply=True)


def test_invalid_manual_evidence_is_a_usage_error(tmp_path, capsys):
    merged = tmp_path / "merged.json"
    merged.write_text("[]")
    labels = tmp_path / "manual.json"
    labels.write_text(
        json.dumps([manual_entry(serving={"amount": 1.5, "unit": "g"})])
    )
    with pytest.raises(SystemExit):
        seed.main([str(merged), "--manual-evidence", str(labels)])
    assert "invalid manual evidence" in capsys.readouterr().err


def test_dry_run_prints_drop_reasons(tmp_path, capsys):
    merged = tmp_path / "merged.json"
    merged.write_text(
        json.dumps(
            [
                record(serving_size="9 chips"),
                record(
                    normalized="PECANS",
                    lane="sprouts",
                    source="sprouts",
                    source_id="1",
                    size="1 lb",
                ),
            ]
        )
    )
    assert seed.main([str(merged)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("DRY RUN: 2 records, 0 manual labels")
    assert "Dropped or unparsed fields" in out
    assert "serving_unparsed_identity_only           1" in out
    assert "instacart_default_net                    1" in out
    assert "writes proposed" not in out
