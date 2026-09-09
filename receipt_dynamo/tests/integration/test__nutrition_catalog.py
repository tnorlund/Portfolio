"""Moto verifies actual nutrition conditions, pagination, and error mapping."""

from dataclasses import replace
from typing import Any
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityAlreadyExistsError,
    EntityValidationError,
    NutritionConflictError,
    OperationError,
)
from receipt_dynamo.entities.food_product import FoodProduct
from receipt_dynamo.entities.nutrition_support import nutrition_json
from receipt_dynamo.entities.product_alias import ProductAlias

pytestmark = [pytest.mark.integration]


@pytest.fixture
def nutrition_client(dynamodb_table: str) -> DynamoClient:
    return DynamoClient(dynamodb_table)


def product(version: int = 1) -> FoodProduct:
    return FoodProduct(
        "fdc:test",
        nutrition_json({"product_id": "fdc:test", "version": version}),
    )


def alias(fact: FoodProduct | None = None, **changes: Any) -> ProductAlias:
    fields = dict(
        merchant_slug="merchant",
        kind="TEXT",
        text="test item",
        revision=1,
        status="matched" if fact else "pending",
        method="lexical",
        changed_at="2026-09-08T00:00:00+00:00",
        expires_at=2_000_000_000,
        applicability_json='{"size":"unknown"}',
        product_id=fact.product_id if fact else None,
        product_revision=fact.revision if fact else None,
    )
    fields.update(changes)
    return ProductAlias(**fields)


def save(
    client: DynamoClient, decision: ProductAlias, revision: int = 0
) -> ProductAlias:
    return client.save_product_alias(
        decision,
        expected_revision=revision,
        expected_table_name=client.table_name,
    )


def test_nutrition_product_is_append_only(
    nutrition_client: DynamoClient,
) -> None:
    client = nutrition_client
    original, changed = product(), product(2)
    assert (
        client.get_food_product(original.product_id, original.revision) is None
    )
    for fact in (original, changed):
        client.add_food_product(fact, expected_table_name=client.table_name)
    with pytest.raises(EntityAlreadyExistsError):
        client.add_food_product(
            original, expected_table_name=client.table_name
        )
    assert (
        client.get_food_product(original.product_id, original.revision)
        == original
    )
    assert (
        client.get_food_product(changed.product_id, changed.revision)
        == changed
    )


def test_nutrition_alias_requires_existing_product(
    nutrition_client: DynamoClient,
) -> None:
    client, fact = nutrition_client, product()
    with pytest.raises(NutritionConflictError):
        save(client, alias(fact))
    assert client.get_product_alias("merchant", "TEXT", "test item") is None
    client.add_food_product(fact, expected_table_name=client.table_name)
    decision = save(client, alias(fact))
    assert (
        client.get_product_alias("merchant", "TEXT", "test item") == decision
    )


def test_nutrition_user_correction_wins_model_races(
    nutrition_client: DynamoClient,
) -> None:
    client = nutrition_client
    first = save(client, alias())
    confirmed = replace(
        first,
        revision=2,
        status="rejected",
        method="user",
        confirmed_by_user=True,
        expires_at=None,
    )
    save(client, confirmed, 1)
    with pytest.raises(NutritionConflictError):
        save(client, replace(first, revision=2), 1)
    # Even a model which reads the current revision cannot replace a user.
    with pytest.raises(NutritionConflictError):
        save(client, replace(first, revision=3), 2)
    corrected = replace(confirmed, revision=3, status="not_food")
    save(client, corrected, 2)
    assert (
        client.get_product_alias("merchant", "TEXT", "test item") == corrected
    )


def test_nutrition_expired_alias_keeps_revision(
    nutrition_client: DynamoClient,
) -> None:
    client = nutrition_client
    old = save(client, alias())
    assert old.is_expired(2_000_000_000)
    with pytest.raises(NutritionConflictError):
        save(client, alias())
    save(client, replace(old, revision=2, expires_at=2_000_000_100), 1)


def test_nutrition_paginated_catalog_and_aliases(
    nutrition_client: DynamoClient,
) -> None:
    client = nutrition_client
    for number in range(100):
        client.add_food_product(
            product(number), expected_table_name=client.table_name
        )
        save(client, alias(text=f"test #{number:03d} é"))
    for list_page, scope, attribute in (
        (client.list_food_product_revisions, "fdc:test", "revision"),
        (client.list_product_aliases, "merchant", "text"),
    ):
        cursor = None
        seen: set[Any] = set()
        while True:
            rows, cursor = list_page(scope, limit=7, last_evaluated_key=cursor)
            keys = {getattr(row, attribute) for row in rows}
            assert not keys & seen
            seen.update(keys)
            if not cursor:
                break
        assert len(seen) == 100
    assert client.list_product_aliases("other")[0] == []


def test_nutrition_validation_before_writes(
    nutrition_client: DynamoClient,
) -> None:
    client = nutrition_client
    with pytest.raises(EntityValidationError):
        client.add_food_product(None, expected_table_name=client.table_name)
    with pytest.raises(EntityValidationError, match="table mismatch"):
        client.add_food_product(product(), expected_table_name="wrong")
    with pytest.raises(EntityValidationError, match="increment"):
        save(client, alias(revision=3), 0)
    with pytest.raises(EntityValidationError):
        client.list_product_aliases("merchant", limit=0)
    with pytest.raises(EntityValidationError, match="scope"):
        client.list_product_aliases(
            "merchant",
            last_evaluated_key={"PK": {"S": "other"}, "SK": {"S": "x"}},
        )


ERRORS = [
    ("ValidationException", EntityValidationError),
    ("ResourceNotFoundException", OperationError),
    ("ProvisionedThroughputExceededException", DynamoDBThroughputError),
    ("ThrottlingException", DynamoDBThroughputError),
    ("InternalServerError", DynamoDBServerError),
    ("ServiceUnavailable", DynamoDBServerError),
    ("AccessDeniedException", DynamoDBError),
]


@pytest.mark.parametrize(("code", "exception"), ERRORS)
@pytest.mark.parametrize("operation", ["put", "get", "list", "alias"])
def test_nutrition_service_errors_mapped(
    nutrition_client: DynamoClient,
    code: str,
    exception: type[Exception],
    operation: str,
) -> None:
    client = nutrition_client
    method = {
        "put": "put_item",
        "get": "get_item",
        "list": "query",
        "alias": "transact_write_items",
    }[operation]
    with patch.object(
        client._client,
        method,
        side_effect=ClientError(
            {"Error": {"Code": code, "Message": "test"}}, method
        ),
    ):
        with pytest.raises(exception):
            if operation == "put":
                client.add_food_product(
                    product(), expected_table_name=client.table_name
                )
            elif operation == "get":
                client.get_food_product("x", "a" * 64)
            elif operation == "list":
                client.list_product_aliases("merchant")
            else:
                save(client, alias())
