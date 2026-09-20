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
from receipt_dynamo.entities.product_alias_observation import (
    ProductAliasObservation,
    product_alias_id,
)

pytestmark = [pytest.mark.integration]

IMAGE_ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"


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


def observation(
    decision: ProductAlias | None = None, **changes: Any
) -> ProductAliasObservation:
    decision = decision or alias()
    fields: dict[str, Any] = dict(
        alias_id=decision.alias_id,
        merchant_slug=decision.merchant_slug,
        kind=decision.kind,
        text=decision.text,
        status=decision.status,
        alias_revision=decision.revision,
        product_id=decision.product_id,
        product_revision=decision.product_revision,
        image_id=IMAGE_ID,
        receipt_id=1,
        item_index=0,
        observed_at="2026-09-09T00:00:00+00:00",
    )
    fields.update(changes)
    return ProductAliasObservation(**fields)


def expectation(
    decision: ProductAlias, revision: int | None
) -> tuple[str, str, str, int | None]:
    return (decision.merchant_slug, decision.kind, decision.text, revision)


def publish(
    client: DynamoClient,
    observations: list[ProductAliasObservation],
    expectations: list[tuple[str, str, str, int | None]],
) -> None:
    client.publish_alias_observations(
        observations,
        alias_expectations=expectations,
        expected_table_name=client.table_name,
    )


def pointers(
    client: DynamoClient, alias_id: str
) -> list[ProductAliasObservation]:
    rows, cursor = client.list_alias_observations(alias_id, limit=100)
    assert cursor is None
    return rows


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


def test_nutrition_alias_pins_the_exact_product_revision(
    nutrition_client: DynamoClient,
) -> None:
    """A product identity alone is not enough: the revision row must exist."""
    client, stored, unstored = nutrition_client, product(), product(2)
    client.add_food_product(stored, expected_table_name=client.table_name)
    assert stored.product_id == unstored.product_id
    assert stored.revision != unstored.revision
    with pytest.raises(NutritionConflictError):
        save(client, alias(unstored))
    assert client.get_product_alias("merchant", "TEXT", "test item") is None
    save(client, alias(stored))


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


def test_nutrition_observations_publish_with_matching_revisions(
    nutrition_client: DynamoClient,
) -> None:
    client, fact = nutrition_client, product()
    client.add_food_product(fact, expected_table_name=client.table_name)
    matched = save(client, alias(fact))
    absent = alias(kind="ITEM", text="12345")
    text_pointer = observation(matched)
    item_pointer = observation(
        absent,
        status="pending",
        alias_revision=0,
        product_id=None,
        product_revision=None,
    )
    publish(
        client,
        [text_pointer, item_pointer],
        [expectation(matched, 1), expectation(absent, None)],
    )
    assert pointers(client, matched.alias_id) == [text_pointer]
    assert pointers(client, absent.alias_id) == [item_pointer]
    assert pointers(client, "f" * 64) == []
    # Publishing the same line again replaces the pointer unconditionally.
    changed = observation(
        matched, status="rejected", product_id=None, product_revision=None
    )
    publish(client, [changed], [expectation(matched, 1)])
    assert pointers(client, matched.alias_id) == [changed]


def test_nutrition_stale_alias_revision_publishes_nothing(
    nutrition_client: DynamoClient,
) -> None:
    client = nutrition_client
    first = save(client, alias())
    absent = alias(kind="ITEM", text="12345")
    stale = observation(first)
    fresh = observation(
        absent, status="pending", alias_revision=0, item_index=1
    )
    save(
        client,
        replace(
            first,
            revision=2,
            status="rejected",
            method="user",
            confirmed_by_user=True,
            expires_at=None,
        ),
        1,
    )
    with pytest.raises(NutritionConflictError):
        publish(
            client,
            [stale, fresh],
            [expectation(first, 1), expectation(absent, None)],
        )
    assert pointers(client, first.alias_id) == []
    assert pointers(client, absent.alias_id) == []
    current = observation(first, status="rejected", alias_revision=2)
    publish(
        client,
        [current, fresh],
        [expectation(first, 2), expectation(absent, None)],
    )
    assert pointers(client, first.alias_id) == [current]
    assert pointers(client, absent.alias_id) == [fresh]


def test_nutrition_absent_expectation_fails_on_existing_alias(
    nutrition_client: DynamoClient,
) -> None:
    client = nutrition_client
    first = save(client, alias())
    late = observation(first, alias_revision=0)
    with pytest.raises(NutritionConflictError):
        publish(client, [late], [expectation(first, None)])
    assert pointers(client, first.alias_id) == []


def test_nutrition_observation_pagination(
    nutrition_client: DynamoClient,
) -> None:
    client = nutrition_client
    first = save(client, alias())
    for batch in range(2):
        publish(
            client,
            [
                observation(first, item_index=batch * 40 + index)
                for index in range(40)
            ],
            [expectation(first, 1)],
        )
    cursor = None
    seen: set[int] = set()
    while True:
        rows, cursor = client.list_alias_observations(
            first.alias_id, limit=7, last_evaluated_key=cursor
        )
        indexes = {row.item_index for row in rows}
        assert not indexes & seen
        seen.update(indexes)
        if not cursor:
            break
    assert seen == set(range(80))
    default_page, cursor = client.list_alias_observations(first.alias_id)
    assert len(default_page) == 25 and cursor is not None
    with pytest.raises(EntityValidationError, match="scope"):
        client.list_alias_observations(
            first.alias_id,
            last_evaluated_key={"PK": {"S": "other"}, "SK": {"S": "x"}},
        )
    with pytest.raises(EntityValidationError):
        client.list_alias_observations("not-a-hash")


def test_nutrition_observation_validation_before_writes(
    nutrition_client: DynamoClient,
) -> None:
    client = nutrition_client
    first = save(client, alias())
    pointer = observation(first)
    with pytest.raises(EntityValidationError, match="mismatch"):
        client.publish_alias_observations(
            [pointer],
            alias_expectations=[expectation(first, 1)],
            expected_table_name="wrong",
        )
    with pytest.raises(EntityValidationError, match="at least one"):
        publish(client, [], [expectation(first, 1)])
    with pytest.raises(EntityValidationError, match="ProductAliasObservation"):
        publish(client, [first], [expectation(first, 1)])
    with pytest.raises(EntityValidationError, match="lacks"):
        publish(client, [pointer], [])
    with pytest.raises(EntityValidationError, match="differs"):
        publish(client, [pointer], [expectation(first, 2)])
    with pytest.raises(EntityValidationError, match="differs"):
        publish(client, [pointer], [expectation(first, None)])
    with pytest.raises(EntityValidationError, match="duplicate"):
        publish(client, [pointer, pointer], [expectation(first, 1)])
    with pytest.raises(EntityValidationError, match="conflicting"):
        publish(
            client, [pointer], [expectation(first, 1), expectation(first, 2)]
        )
    with pytest.raises(EntityValidationError, match="invalid alias"):
        publish(client, [pointer], [("merchant", "TEXT")])
    with pytest.raises(EntityValidationError, match="100-action"):
        publish(
            client,
            [observation(first, item_index=index) for index in range(100)],
            [expectation(first, 1)],
        )
    assert pointers(client, first.alias_id) == []


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
@pytest.mark.parametrize(
    "operation", ["put", "get", "list", "alias", "publish", "pointers"]
)
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
        "publish": "transact_write_items",
        "pointers": "query",
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
            elif operation == "publish":
                publish(client, [observation()], [expectation(alias(), 1)])
            elif operation == "pointers":
                client.list_alias_observations(
                    product_alias_id("m", "TEXT", "x")
                )
            else:
                save(client, alias())
