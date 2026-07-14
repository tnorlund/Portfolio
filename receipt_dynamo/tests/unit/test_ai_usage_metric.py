"""Contract tests for :class:`AIUsageMetric`."""

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest

from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric

NOW = datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc)


def make_metric(**overrides):
    values = {
        "service": "OpenAI",
        "model": "gpt-4",
        "operation": "completion",
        "timestamp": NOW,
        "request_id": "request-1",
    }
    values.update(overrides)
    return AIUsageMetric(**values)


def test_minimal_construction_normalizes_and_computes_dimensions():
    metric = make_metric(request_id=None)

    assert metric.service == "openai"
    assert metric.request_id
    assert (metric.date, metric.month, metric.hour) == (
        "2024-01-15",
        "2024-01",
        "2024-01-15-10",
    )
    assert metric.api_calls == 1
    assert metric.metadata == {}


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        ({"input_tokens": 4, "output_tokens": 6}, 10),
        ({"input_tokens": 0, "output_tokens": 0}, 0),
        ({"input_tokens": 4, "output_tokens": None}, 4),
        ({"input_tokens": None, "output_tokens": 6}, 6),
        (
            {"input_tokens": 4, "output_tokens": 6, "total_tokens": 99},
            99,
        ),
    ],
)
def test_total_token_derivation(inputs, expected):
    assert make_metric(**inputs).total_tokens == expected


@pytest.mark.parametrize(
    ("field", "bad_value", "message"),
    [
        ("service", "", "service must be a non-empty string"),
        ("service", 1, "service must be a non-empty string"),
        ("model", "", "model must be a non-empty string"),
        ("operation", None, "operation must be a non-empty string"),
        ("timestamp", "2024-01-01", "timestamp must be a datetime"),
        ("request_id", 1, "request_id must be a string or None"),
        ("input_tokens", True, "input_tokens must be a non-negative"),
        ("output_tokens", -1, "output_tokens must be a non-negative"),
        ("total_tokens", 1.5, "total_tokens must be a non-negative"),
        ("api_calls", True, "api_calls must be a positive integer"),
        ("api_calls", 0, "api_calls must be a positive integer"),
        ("cost_usd", True, "cost_usd must be a finite"),
        ("cost_usd", float("nan"), "cost_usd must be a finite"),
        ("cost_usd", float("inf"), "cost_usd must be a finite"),
        ("cost_usd", -0.1, "cost_usd must be a finite"),
        ("latency_ms", -1, "latency_ms must be a non-negative"),
        ("github_pr", False, "github_pr must be a non-negative"),
        ("environment", 1, "environment must be a non-empty string or None"),
        ("metadata", [], "metadata must be a dictionary or None"),
    ],
)
def test_rejects_invalid_field_boundaries(field, bad_value, message):
    with pytest.raises(ValueError, match=message):
        make_metric(**{field: bad_value})


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ({"job_id": "j", "user_id": "u", "batch_id": "b"}, "JOB#j"),
        ({"user_id": "u", "batch_id": "b"}, "USER#u"),
        ({"batch_id": "b", "environment": "prod"}, "BATCH#b"),
        ({"environment": "prod"}, "ENV#prod"),
        ({}, None),
    ],
)
def test_gsi3_scope_priority(scope, expected):
    metric = make_metric(**scope)

    assert metric.gsi3pk == expected
    assert (metric.gsi3sk is None) is (expected is None)


def test_exact_keys_and_all_field_serialization():
    metric = make_metric(
        input_tokens=100,
        output_tokens=50,
        api_calls=2,
        cost_usd=0.003,
        latency_ms=1500,
        user_id="user-1",
        job_id="job-1",
        batch_id="batch-1",
        github_pr=42,
        environment="test",
        error="timeout",
        metadata={"nested": {"values": [1, True, None]}},
    )

    item = metric.to_item()

    assert item["PK"] == {"S": "AI_USAGE#openai#gpt-4"}
    assert item["SK"] == {"S": "USAGE#2024-01-15T10:30:00+00:00#request-1"}
    assert item["GSI1PK"] == {"S": "AI_USAGE#openai"}
    assert item["GSI1SK"] == {"S": "DATE#2024-01-15"}
    assert item["GSI2PK"] == {"S": "AI_USAGE_COST"}
    assert item["GSI2SK"] == {"S": "COST#2024-01-15#openai"}
    assert item["GSI3PK"] == {"S": "JOB#job-1"}
    assert item["GSI3SK"] == {"S": "AI_USAGE#2024-01-15T10:30:00+00:00"}
    assert item["input_tokens"] == {"N": "100"}
    assert item["output_tokens"] == {"N": "50"}
    assert item["total_tokens"] == {"N": "150"}
    assert Decimal(item["cost_usd"]["N"]) == Decimal("0.003")
    assert AIUsageMetric.from_item(item) == metric


def test_minimal_serialization_omits_optional_fields_and_gsi3():
    item = make_metric().to_dynamodb_item()

    optional = {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cost_usd",
        "latency_ms",
        "user_id",
        "job_id",
        "batch_id",
        "github_pr",
        "environment",
        "error",
        "metadata",
        "GSI3PK",
        "GSI3SK",
    }
    assert optional.isdisjoint(item)


def test_metadata_roundtrip_is_nested_and_defensively_copied():
    metadata = {
        "string": "value",
        "int": 2,
        "float": 1.25,
        "bool": True,
        "none": None,
        "list": [1, {"deep": ["value"]}],
    }
    metric = make_metric(metadata=metadata)
    metadata["list"][1]["deep"].append("caller mutation")

    restored = AIUsageMetric.from_item(metric.to_item())

    assert restored.metadata["list"][1]["deep"] == ["value"]
    assert hash(restored) == hash(metric)


def test_unsupported_nested_value_uses_string_representation():
    class CustomValue:
        def __str__(self):
            return "custom-value"

    restored = AIUsageMetric.from_item(
        make_metric(metadata={"custom": CustomValue()}).to_item()
    )

    assert restored.metadata == {"custom": "custom-value"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("PK", {"S": "AI_USAGE#wrong#gpt-4"}),
        ("SK", {"S": "USAGE#wrong#request-1"}),
        ("TYPE", {"S": "WRONG"}),
        ("GSI1PK", {"S": "AI_USAGE#wrong"}),
        ("GSI2SK", {"S": "COST#wrong"}),
    ],
)
def test_deserialization_rejects_inconsistent_keys(field, value):
    item = make_metric().to_item()
    item[field] = value

    with pytest.raises(ValueError, match="inconsistent keys"):
        AIUsageMetric.from_item(item)


def test_deserialization_rejects_missing_required_field():
    item = make_metric().to_item()
    del item["operation"]

    with pytest.raises(ValueError, match="missing required keys"):
        AIUsageMetric.from_item(item)


def test_persistence_revalidates_mutations_and_refreshes_dimensions():
    metric = make_metric()
    metric.timestamp = datetime(2025, 2, 3, 4, tzinfo=timezone.utc)
    assert metric.to_item()["date"] == {"S": "2025-02-03"}

    metric.api_calls = False
    with pytest.raises(ValueError, match="api_calls must be a positive"):
        metric.to_item()


def test_query_by_service_date_executes_expected_query_and_parses_items():
    expected = make_metric()
    client = Mock(table_name="table")
    client.query.return_value = {"Items": [expected.to_item()]}

    actual = AIUsageMetric.query_by_service_date(
        client, "openai", "2024-01-15", "2024-01-16"
    )

    assert actual == [expected]
    client.query.assert_called_once_with(
        TableName="table",
        IndexName="GSI1",
        KeyConditionExpression=(
            "GSI1PK = :pk AND GSI1SK BETWEEN :start AND :end"
        ),
        ExpressionAttributeValues={
            ":pk": {"S": "AI_USAGE#openai"},
            ":start": {"S": "DATE#2024-01-15"},
            ":end": {"S": "DATE#2024-01-16"},
        },
    )


def test_cost_query_aggregates_services_and_keeps_zero_cost():
    metrics = [
        make_metric(service="openai", request_id="1", cost_usd=0.0),
        make_metric(service="openai", request_id="2", cost_usd=0.25),
        make_metric(service="anthropic", request_id="3", cost_usd=1.5),
    ]
    client = Mock(table_name="table")
    client.query.return_value = {
        "Items": [metric.to_item() for metric in metrics]
    }

    costs = AIUsageMetric.get_total_cost_by_date(client, "2024-01-15")

    assert costs == {"openai": 0.25, "anthropic": 1.5}
    assert client.query.call_args.kwargs["IndexName"] == "GSI2"


def test_repr_and_cost_precision():
    metric = make_metric(total_tokens=150, cost_usd=0.000123456789)

    assert "150 tokens" in repr(metric)
    restored = AIUsageMetric.from_item(metric.to_item())
    assert restored.cost_usd == metric.cost_usd


def test_metadata_none_gets_an_independent_default():
    first = make_metric(metadata=None)
    second = make_metric(metadata=None, request_id="request-2")

    first.metadata["changed"] = True

    assert second.metadata == {}


def test_malformed_metadata_falls_back_to_empty_mapping():
    item = make_metric().to_item()
    item["metadata"] = {"UNKNOWN": "value"}

    restored = AIUsageMetric.from_item(item)

    assert restored.metadata == {}
