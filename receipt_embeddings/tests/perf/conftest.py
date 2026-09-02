"""Opt-in performance suite for the Dynamo embedding writer and readers.

Skipped unless ``RECEIPT_EMBEDDINGS_PERF=1`` or ``pytest -m performance``.
CI already excludes the ``performance`` marker. Live SearchVectors latency
is measured by ``scripts/similarity_harness/evaluate.py --backend dynamo
--measure-wall-latency``, not this moto suite.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

_PERF_ENV = "RECEIPT_EMBEDDINGS_PERF"
TABLE = "ReceiptsTable-dc5be22"
REGION = "us-east-1"


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if os.environ.get(_PERF_ENV) == "1":
        return
    markexpr = (config.option.markexpr or "").strip()
    if "performance" in markexpr and "not performance" not in markexpr:
        return
    skip = pytest.mark.skip(
        reason="opt-in: RECEIPT_EMBEDDINGS_PERF=1 or -m performance"
    )
    for item in items:
        if item.get_closest_marker("performance") is not None:
            item.add_marker(skip)


def _aws_test_env() -> None:
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    os.environ.setdefault("AWS_DEFAULT_REGION", REGION)
    os.environ.pop("DYNAMODB_ENDPOINT_URL", None)


def _create_table(client: Any, *, with_gsitype: bool) -> None:
    attribute_definitions = [
        {"AttributeName": "PK", "AttributeType": "S"},
        {"AttributeName": "SK", "AttributeType": "S"},
    ]
    kwargs: dict[str, Any] = {
        "TableName": TABLE,
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": attribute_definitions,
        "BillingMode": "PAY_PER_REQUEST",
    }
    if with_gsitype:
        attribute_definitions.append(
            {"AttributeName": "TYPE", "AttributeType": "S"}
        )
        kwargs["GlobalSecondaryIndexes"] = [
            {
                "IndexName": "GSITYPE",
                "KeySchema": [{"AttributeName": "TYPE", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ]
    client.create_table(**kwargs)
    client.get_waiter("table_exists").wait(TableName=TABLE)


@pytest.fixture
def receipts_client() -> Iterator[Any]:
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    _aws_test_env()
    with moto.mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        _create_table(client, with_gsitype=False)
        yield client


@pytest.fixture
def gsitype_client() -> Iterator[Any]:
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    _aws_test_env()
    with moto.mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        _create_table(client, with_gsitype=True)
        yield client
