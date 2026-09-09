"""Moto proves price observations append only and list whole partitions."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
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
from receipt_dynamo.entities.price_observation import (
    PriceEvidence,
    PriceObservation,
)

pytestmark = [pytest.mark.integration]


@pytest.fixture
def nutrition_client(dynamodb_table: str) -> DynamoClient:
    return DynamoClient(dynamodb_table)


def price_observation(**changes: Any) -> PriceObservation:
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


def add(client: DynamoClient, row: PriceObservation) -> PriceObservation:
    return client.add_price_observation(
        row, expected_table_name=client.table_name
    )


def test_price_observations_are_append_only(
    nutrition_client: DynamoClient,
) -> None:
    client = nutrition_client
    first = add(client, price_observation())
    with pytest.raises(EntityAlreadyExistsError):
        add(client, price_observation(price_per_unit=Decimal("7.49")))
    with pytest.raises(EntityAlreadyExistsError):
        add(client, price_observation())
    correction = price_observation(
        observed_on=date(2026, 9, 10),
        price_per_unit=Decimal("7.49"),
        effective_on=date(2026, 9, 4),
        supersedes=first.sort_key,
    )
    add(client, correction)
    with pytest.raises(EntityAlreadyExistsError):
        add(client, replace(correction, price_per_unit=Decimal("1")))
    rows = client.list_price_observations("costco-wholesale", "ITEM", "36946")
    assert rows == [first, correction]
    assert rows[0].price_per_unit == Decimal("6.99")


def test_price_correction_must_name_existing_row(
    nutrition_client: DynamoClient,
) -> None:
    client = nutrition_client
    dangling = price_observation(supersedes="DATE#2026-09-01#sticker#000")
    with pytest.raises(NutritionConflictError):
        add(client, dangling)
    assert (
        client.list_price_observations("costco-wholesale", "ITEM", "36946")
        == []
    )
    with pytest.raises(EntityValidationError, match="table mismatch"):
        client.add_price_observation(
            price_observation(), expected_table_name=""
        )
    with pytest.raises(EntityValidationError):
        client.add_price_observation(
            None, expected_table_name=client.table_name
        )


def test_price_observations_list_whole_partition(
    nutrition_client: DynamoClient,
) -> None:
    client = nutrition_client
    rows = [
        price_observation(
            observed_on=date(2026, 1, 1 + day % 28), seq=day // 28
        )
        for day in range(40)
    ]
    for row in rows:
        add(client, row)
    add(client, price_observation(key_text="other"))
    add(client, price_observation(key_kind="TEXT", key_text="36946"))
    original = client._client.query
    calls: list[dict[str, Any]] = []

    def paged(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return original(**kwargs, Limit=7)

    with patch.object(client._client, "query", side_effect=paged):
        listed = client.list_price_observations(
            "costco-wholesale", "ITEM", "36946"
        )
    assert listed == sorted(rows, key=lambda row: row.sort_key)
    assert len(calls) >= 6
    assert all("Limit" not in call for call in calls)
    assert all(call["ConsistentRead"] for call in calls)
    assert all(
        call["ExpressionAttributeValues"][":pk"]["S"].startswith(
            "PRICE_OBS#costco-wholesale#ITEM#36946"
        )
        for call in calls
    )
    assert client.list_price_observations("nobody", "ITEM", "1") == []


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
@pytest.mark.parametrize("operation", ["put", "transact", "list"])
def test_price_observation_service_errors_mapped(
    nutrition_client: DynamoClient,
    code: str,
    exception: type[Exception],
    operation: str,
) -> None:
    client = nutrition_client
    method = {
        "put": "put_item",
        "transact": "transact_write_items",
        "list": "query",
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
                add(client, price_observation())
            elif operation == "transact":
                add(
                    client,
                    price_observation(
                        supersedes="DATE#2026-09-01#sticker#000"
                    ),
                )
            else:
                client.list_price_observations("merchant", "ITEM", "1")
