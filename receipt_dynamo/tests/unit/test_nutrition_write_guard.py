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
from receipt_dynamo.data.base_operations.mixins import (
    BatchOperationsMixin,
    TransactionalOperationsMixin,
)
from receipt_dynamo.data.base_operations.nutrition_guard import (
    payload_touches_nutrition,
)
from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.food_product import FoodProduct
from receipt_dynamo.entities.product_alias import ProductAlias
from receipt_dynamo.entities.product_alias_observation import (
    ProductAliasObservation,
    product_alias_id,
)

IMAGE_ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"

pytestmark = [pytest.mark.unit]

MUTATING_PREFIXES = ("add_", "save_", "put_", "publish_", "delete_", "update_")
SPELLINGS = [
    "{name}",
    "arn:aws:dynamodb:us-east-1:123456789012:table/{name}",
    "arn:aws:dynamodb:us-east-1:123456789012:table/{name}/stream/2026",
]
KNOWN_MUTATORS = {
    "add_food_product",
    "add_price_observation",
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


class _ReceiptLike:
    """A non-nutrition entity: the guard must let it through untouched."""

    key = {"PK": {"S": f"IMAGE#{IMAGE_ID}"}, "SK": {"S": "RECEIPT#00001"}}

    def to_item(self) -> dict[str, Any]:
        return {**self.key, "TYPE": {"S": "RECEIPT"}}


def nutrition_entities() -> list[Any]:
    fact = FoodProduct("fdc:test", '{"product_id":"fdc:test"}')
    decision = ProductAlias(
        merchant_slug="test",
        kind="TEXT",
        text="milk",
        revision=1,
        status="matched",
        method="user",
        confirmed_by_user=True,
        changed_at="2026-09-09T00:00:00+00:00",
        applicability_json='{"size":"unknown"}',
        product_id=fact.product_id,
        product_revision=fact.revision,
    )
    pointer = ProductAliasObservation(
        alias_id=product_alias_id("test", "TEXT", "milk"),
        merchant_slug="test",
        kind="TEXT",
        text="milk",
        status="pending",
        alias_revision=0,
        image_id=IMAGE_ID,
        receipt_id=1,
        item_index=0,
        observed_at="2026-09-09T00:00:00+00:00",
    )
    return [fact, decision, pointer]


class _Legacy(BatchOperationsMixin, TransactionalOperationsMixin):
    def __init__(self, table_name: str) -> None:
        self.table_name = table_name
        self._client = _NoIO()  # pylint: disable=protected-access


def generic_writes(entity: Any) -> list[tuple[str, tuple[Any, ...]]]:
    item = entity.to_item()
    return [
        ("_add_entity", (entity,)),
        ("_update_entity", (entity,)),
        ("_delete_entity", (entity,)),
        ("_delete_entities", ([entity],)),
        ("_batch_write_with_retry", ([{"PutRequest": {"Item": item}}],)),
        (
            "_batch_write_with_retry",
            ([{"DeleteRequest": {"Key": entity.key}}],),
        ),
        (
            "_transact_write_with_chunking",
            ([{"Put": {"TableName": "t", "Item": item}}],),
        ),
        (
            "_transact_write_with_chunking",
            ([{"Delete": {"TableName": "t", "Key": entity.key}}],),
        ),
    ]


@pytest.mark.parametrize("table_name", prohibited_spellings())
def test_generic_write_paths_refuse_nutrition_rows(table_name: str) -> None:
    client = client_for(table_name)
    for entity in nutrition_entities():
        for method, args in generic_writes(entity):
            with pytest.raises(EntityValidationError, match="prohibited"):
                getattr(client, method)(*args)
    # Legacy mixins are not composed into DynamoClient; guard them directly.
    legacy = _Legacy(table_name)
    with pytest.raises(EntityValidationError, match="prohibited"):
        legacy._batch_write_with_retry_dict(
            {table_name: [{"PutRequest": {"Item": entity.to_item()}}]}
        )
    with pytest.raises(EntityValidationError, match="prohibited"):
        legacy._transact_write_items(
            [{"Put": {"TableName": table_name, "Item": entity.to_item()}}]
        )


@pytest.mark.parametrize("table_name", prohibited_spellings())
def test_generic_write_paths_pass_other_rows(table_name: str) -> None:
    client = client_for(table_name)
    client._client = MagicMock()
    client._client.batch_write_item.return_value = {}
    for method, args in generic_writes(_ReceiptLike()):
        getattr(client, method)(*args)
    # A receipt cascade deletes its derived summary row by design.
    client._batch_write_with_retry(
        [
            {
                "DeleteRequest": {
                    "Key": {
                        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
                        "SK": {"S": "RECEIPT#00001#NUTRITION_SUMMARY"},
                    }
                }
            }
        ]
    )
    calls = client._client.method_calls
    assert {call[0] for call in calls} == {
        "put_item",
        "delete_item",
        "batch_write_item",
        "transact_write_items",
    }


def test_generic_write_paths_untouched_on_allowed_table() -> None:
    client = client_for("ReceiptsTable-dc5be22")
    client._client = MagicMock()
    client._client.batch_write_item.return_value = {}
    for entity in nutrition_entities():
        for method, args in generic_writes(entity):
            getattr(client, method)(*args)
    assert client._client.put_item.call_count == 6


def test_payload_walker_recognises_every_shape() -> None:
    fact = nutrition_entities()[0]
    assert payload_touches_nutrition(fact)
    assert payload_touches_nutrition(fact.to_item())
    assert payload_touches_nutrition({"Update": {"Key": fact.key}})
    assert payload_touches_nutrition(
        {"TYPE": {"S": "RECEIPT_NUTRITION_SUMMARY"}}
    )
    assert payload_touches_nutrition({"PK": "PRICE_OBS#costco#ITEM#1"})
    assert not payload_touches_nutrition(_ReceiptLike())
    assert not payload_touches_nutrition([])
    assert not payload_touches_nutrition({"Put": {"Item": {"x": "y"}}})
