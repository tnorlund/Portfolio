"""Price observations serialize exactly and refuse prohibited tables."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.data._nutrition_catalog import (
    PROHIBITED_NUTRITION_WRITE_TABLES,
)
from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.price_observation import (
    PriceEvidence,
    PriceObservation,
    item_to_price_observation,
)

pytestmark = [pytest.mark.unit]


def price_observation(**changes: object) -> PriceObservation:
    fields = dict(
        merchant_slug="costco-wholesale",
        key_kind="ITEM",
        key_text="36946",
        observed_on=date(2026, 9, 4),
        seq=0,
        unit="lb",
        price_per_unit=Decimal("6.99"),
        source="tracker",
        evidence=PriceEvidence("tracker:bulgogi", date(2026, 9, 4)),
    )
    fields.update(changes)
    return PriceObservation(**fields)


def test_price_observation_roundtrip_defaults_and_keys() -> None:
    row = price_observation()
    item = row.to_item()
    assert item_to_price_observation(item) == row
    assert row.effective_on == date(2026, 9, 4)
    assert item["PK"] == {"S": "PRICE_OBS#costco-wholesale#ITEM#36946"}
    assert item["SK"] == {"S": "DATE#2026-09-04#tracker#000"}
    assert item["TYPE"] == {"S": "PRICE_OBSERVATION"}
    assert item["price_per_unit"] == {"S": "6.99"}
    assert not {"GSI1PK", "GSI2PK", "GSI3PK", "GSI4PK"} & set(item)
    correction = price_observation(
        observed_on=date(2026, 9, 10),
        seq=7,
        price_per_unit=Decimal("7.490"),
        source="owner",
        verification="user",
        effective_on=date(2026, 9, 4),
        supersedes=row.sort_key,
        evidence=PriceEvidence("photo:IMG_1", date(2026, 9, 10), "a" * 64),
    )
    item = correction.to_item()
    assert item["SK"] == {"S": "DATE#2026-09-10#owner#007"}
    assert item["price_per_unit"] == {"S": "7.49"}
    assert item["supersedes"] == {"S": "DATE#2026-09-04#tracker#000"}
    assert item_to_price_observation(item) == correction
    assert item_to_price_observation(item).price_per_unit == Decimal("7.49")


def test_price_observation_key_escaping() -> None:
    row = price_observation(
        merchant_slug="trader joe's", key_kind="TEXT", key_text="rice #1/é"
    )
    pk = row.to_item()["PK"]["S"]
    assert pk == "PRICE_OBS#trader%20joe%27s#TEXT#rice%20%231%2F%C3%A9"
    assert pk.count("#") == 3
    assert item_to_price_observation(row.to_item()) == row


@pytest.mark.parametrize(
    "changes",
    [
        {"price_per_unit": 6.99},
        {"price_per_unit": "6.99"},
        {"price_per_unit": Decimal("0")},
        {"price_per_unit": Decimal("-1")},
        {"price_per_unit": Decimal("NaN")},
        {"unit": "fl oz"},
        {"unit": "g"},
        {"source": "guess"},
        {"verification": "model"},
        {"source": "owner"},
        {"currency": "EUR"},
        {"key_kind": "UPC"},
        {"seq": 1000},
        {"seq": True},
        {"observed_on": "2026-09-04"},
        {"effective_on": datetime(2026, 9, 4)},
        {"supersedes": "DATE#2026-09-04#tracker#000"},
        {"supersedes": "REV#abc"},
        {"evidence": {"reference": "x"}},
    ],
)
def test_price_observation_rejects_invalid(changes: dict) -> None:
    with pytest.raises(EntityValidationError):
        price_observation(**changes)


def test_price_observation_read_integrity() -> None:
    item = price_observation().to_item()
    item["price_per_unit"] = {"N": "6.99"}
    with pytest.raises(EntityValidationError):
        item_to_price_observation(item)
    item = price_observation().to_item()
    item["SK"] = {"S": "DATE#2026-09-05#tracker#000"}
    with pytest.raises(EntityValidationError, match="integrity"):
        item_to_price_observation(item)
    item = price_observation().to_item()
    item["TYPE"] = {"S": "PRODUCT_ALIAS"}
    with pytest.raises(EntityValidationError, match="integrity"):
        item_to_price_observation(item)
    with pytest.raises(EntityValidationError):
        PriceEvidence("ref", date(2026, 9, 4), "not-a-hash")


class _NoIO:
    """Any attribute access means a write path reached boto3; fail loudly."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"DynamoDB call attempted: {name}")


@pytest.mark.parametrize("table", sorted(PROHIBITED_NUTRITION_WRITE_TABLES))
def test_price_observation_refuses_prohibited_table(table: str) -> None:
    client = DynamoClient.__new__(DynamoClient)
    client.table_name = table
    client._client = _NoIO()  # pylint: disable=protected-access
    for row in (
        price_observation(),
        price_observation(supersedes="DATE#2026-09-01#owner#000"),
    ):
        with pytest.raises(EntityValidationError, match="prohibited"):
            client.add_price_observation(row, expected_table_name=table)
        with pytest.raises(EntityValidationError, match="prohibited"):
            client.add_price_observation(
                row,
                expected_table_name=(
                    f"arn:aws:dynamodb:us-east-1:123456789012:table/{table}"
                ),
            )
