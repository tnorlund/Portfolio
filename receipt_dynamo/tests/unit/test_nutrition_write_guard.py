"""No nutrition mutation reaches a prohibited table by any accessor."""

import inspect
from typing import Any
from unittest.mock import MagicMock

import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.data._nutrition_catalog import (
    PROHIBITED_NUTRITION_WRITE_TABLES,
    _NutritionCatalog,
)
from receipt_dynamo.data._receipt_nutrition import _ReceiptNutrition
from receipt_dynamo.data.shared_exceptions import EntityValidationError

pytestmark = [pytest.mark.unit]

MUTATING_PREFIXES = ("add_", "save_", "put_", "publish_", "delete_", "update_")
SPELLINGS = [
    "{name}",
    "arn:aws:dynamodb:us-east-1:123456789012:table/{name}",
    "arn:aws:dynamodb:us-east-1:123456789012:table/{name}/stream/2026",
]
KNOWN_MUTATORS = {
    "add_food_product",
    "save_product_alias",
    "publish_alias_observations",
    "save_receipt_nutrition",
}


class _NoIO:
    """Any attribute access means a write path reached boto3; fail loudly."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"DynamoDB call attempted: {name}")


def mutating_methods() -> list[str]:
    names: set[str] = set()
    for mixin in (_NutritionCatalog, _ReceiptNutrition):
        for name, member in inspect.getmembers(mixin, inspect.isfunction):
            if name.startswith(MUTATING_PREFIXES):
                names.add(name)
    return sorted(names)


def client_for(table_name: str) -> DynamoClient:
    client = DynamoClient.__new__(DynamoClient)
    client.table_name = table_name
    client._client = _NoIO()  # pylint: disable=protected-access
    return client


def call_with_table(
    client: DynamoClient, name: str, expected_table_name: str
) -> None:
    method = getattr(client, name)
    kwargs: dict[str, Any] = {}
    signature = inspect.signature(inspect.unwrap(method))
    for parameter in signature.parameters.values():
        if parameter.name == "self":
            continue
        if parameter.name == "expected_table_name":
            kwargs[parameter.name] = expected_table_name
        else:
            kwargs[parameter.name] = MagicMock()
    method(**kwargs)


def test_mutator_discovery_is_not_vacuous() -> None:
    assert KNOWN_MUTATORS <= set(mutating_methods())
    for name in mutating_methods():
        raw = inspect.unwrap(getattr(DynamoClient, name))
        assert "expected_table_name" in inspect.signature(raw).parameters


def prohibited_spellings() -> list[str]:
    return [
        spelling.format(name=name)
        for name in sorted(PROHIBITED_NUTRITION_WRITE_TABLES)
        for spelling in SPELLINGS
    ]


@pytest.mark.parametrize("name", mutating_methods())
@pytest.mark.parametrize("table_name", prohibited_spellings())
def test_prohibited_client_table_refuses_every_mutation(
    name: str, table_name: str
) -> None:
    client = client_for(table_name)
    with pytest.raises(EntityValidationError, match="prohibited"):
        call_with_table(client, name, table_name)


@pytest.mark.parametrize("name", mutating_methods())
@pytest.mark.parametrize("table_name", prohibited_spellings())
def test_prohibited_expected_table_refuses_every_mutation(
    name: str, table_name: str
) -> None:
    client = client_for("ReceiptsTable-dc5be22")
    with pytest.raises(EntityValidationError, match="prohibited"):
        call_with_table(client, name, table_name)


@pytest.mark.parametrize("name", mutating_methods())
def test_non_string_table_refuses_every_mutation(name: str) -> None:
    client = client_for("ReceiptsTable-dc5be22")
    with pytest.raises(EntityValidationError, match="prohibited"):
        call_with_table(client, name, None)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", mutating_methods())
def test_mismatched_table_still_refuses(name: str) -> None:
    client = client_for("ReceiptsTable-dc5be22")
    with pytest.raises(EntityValidationError, match="mismatch"):
        call_with_table(client, name, "MyMockedTable")


@pytest.mark.parametrize("table_name", prohibited_spellings())
def test_nutrition_transaction_helper_refuses_prohibited_tables(
    table_name: str,
) -> None:
    action = {"Put": {"TableName": table_name, "Item": {"PK": {"S": "x"}}}}
    with pytest.raises(EntityValidationError, match="prohibited"):
        client_for(table_name)._nutrition_transact([action])
    with pytest.raises(EntityValidationError, match="prohibited"):
        client_for("ReceiptsTable-dc5be22")._nutrition_transact([action])
