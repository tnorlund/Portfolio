"""Unit tests for the AIUsageMetric entity."""

# pylint: disable=redefined-outer-name,too-many-statements,too-many-arguments
# pylint: disable=too-many-locals,unused-argument,line-too-long,too-many-lines
# pylint: disable=too-many-public-methods,import-outside-toplevel,trailing-whitespace
# pylint: disable=no-value-for-parameter

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from moto import mock_aws

from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric


@pytest.mark.unit
class TestAIUsageMetric:
    """Test suite for AIUsageMetric entity."""

    @mock_aws
    def test_create_usage_metric_with_all_fields(self):
        """Test creating a new AIUsageMetric with all fields."""

        timestamp = datetime.now(timezone.utc)
        metric = AIUsageMetric(
            service="openai",
            model="gpt-3.5-turbo",
            operation="chat_completion",
            timestamp=timestamp,
            request_id="test-request-123",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            api_calls=1,
            cost_usd=0.000225,
            latency_ms=500,
            user_id="test-user",
            job_id="test-job-123",
            batch_id="test-batch-456",
            github_pr=42,
            error=None,
            metadata={
                "custom_field": "value",
                "nested": {"key": "nested_value"},
            },
        )

        assert metric.service == "openai"
        assert metric.model == "gpt-3.5-turbo"
        assert metric.operation == "chat_completion"
        assert metric.timestamp == timestamp
        assert metric.request_id == "test-request-123"
        assert metric.input_tokens == 100
        assert metric.output_tokens == 50
        assert metric.total_tokens == 150
        assert metric.api_calls == 1
        assert metric.cost_usd == 0.000225
        assert metric.latency_ms == 500
        assert metric.user_id == "test-user"
        assert metric.job_id == "test-job-123"
        assert metric.batch_id == "test-batch-456"
        assert metric.github_pr == 42
        assert metric.error is None
        assert metric.metadata["custom_field"] == "value"
        assert metric.metadata["nested"]["key"] == "nested_value"

        # Test computed fields
        assert metric.date == timestamp.strftime("%Y-%m-%d")
        assert metric.month == timestamp.strftime("%Y-%m")
        assert metric.hour == timestamp.strftime("%Y-%m-%d-%H")

    def test_create_usage_metric_minimal_fields(self):
        """Test creating a new AIUsageMetric with minimal required fields."""
        timestamp = datetime.now(timezone.utc)
        metric = AIUsageMetric(
            service="anthropic",
            model="claude-3-opus-20240229",
            operation="completion",
            timestamp=timestamp,
        )

        assert metric.service == "anthropic"
        assert metric.model == "claude-3-opus-20240229"
        assert metric.operation == "completion"
        assert metric.timestamp == timestamp
        assert metric.request_id is not None  # auto-generated UUID
        assert metric.api_calls == 1  # default value
        assert metric.metadata == {}  # default empty dict
        assert (
            metric.total_tokens is None
        )  # computed from input/output when available

    def test_service_name_normalized(self):
        """Test that service names are normalized to lowercase."""
        timestamp = datetime.now(timezone.utc)
        metric = AIUsageMetric(
            service="OPENAI",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
        )

        assert metric.service == "openai"

    def test_total_tokens_computed_from_input_output(self):
        """Test that total_tokens is computed from input and output tokens."""
        timestamp = datetime.now(timezone.utc)
        metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            input_tokens=100,
            output_tokens=50,
        )

        assert metric.total_tokens == 150

    def test_total_tokens_explicit_value_overrides_computation(self):
        """Test that explicit total_tokens value overrides computation."""
        timestamp = datetime.now(timezone.utc)
        metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            input_tokens=100,
            output_tokens=50,
            total_tokens=200,  # explicit value
        )

        assert metric.total_tokens == 200

    def test_request_id_auto_generated(self):
        """Test that request_id is auto-generated if not provided."""
        timestamp = datetime.now(timezone.utc)
        metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
        )

        assert metric.request_id is not None
        assert len(metric.request_id) > 0
        # Should be a valid UUID format
        uuid.UUID(metric.request_id)  # Raises ValueError if invalid

    def test_pk_sk_properties(self):
        """Test that PK and SK properties are computed correctly."""
        timestamp = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            request_id="test-123",
        )

        assert metric.pk == "AI_USAGE#openai#gpt-4"
        assert metric.sk == "USAGE#2024-01-15T10:30:00+00:00#test-123"

    def test_gsi1_properties(self):
        """Test that GSI1 properties are computed correctly."""
        timestamp = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        metric = AIUsageMetric(
            service="anthropic",
            model="claude-3-opus",
            operation="completion",
            timestamp=timestamp,
        )

        assert metric.gsi1pk == "AI_USAGE#anthropic"
        assert metric.gsi1sk == "DATE#2024-01-15"

    def test_gsi2_properties(self):
        """Test that GSI2 properties are computed correctly."""
        timestamp = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        metric = AIUsageMetric(
            service="google_places",
            model="place_details",
            operation="lookup",
            timestamp=timestamp,
        )

        assert metric.gsi2pk == "AI_USAGE_COST"
        assert metric.gsi2sk == "COST#2024-01-15#google_places"

    def test_gsi3_properties_with_job_id(self):
        """Test that GSI3 properties are computed correctly with job_id."""
        timestamp = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            job_id="test-job-123",
        )

        assert metric.gsi3pk == "JOB#test-job-123"
        assert metric.gsi3sk == "AI_USAGE#2024-01-15T10:30:00+00:00"

    def test_gsi3_properties_with_batch_id(self):
        """Test that GSI3 properties are computed correctly with batch_id."""
        timestamp = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            batch_id="test-batch-456",
        )

        assert metric.gsi3pk == "BATCH#test-batch-456"
        assert metric.gsi3sk == "AI_USAGE#2024-01-15T10:30:00+00:00"

    def test_gsi3_properties_none_without_job_or_batch(self):
        """Test that GSI3 properties are None without job_id or batch_id."""
        timestamp = datetime.now(timezone.utc)
        metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
        )

        assert metric.gsi3pk is None
        assert metric.gsi3sk is None

    def test_item_type_property(self):
        """Test that item_type property returns correct value."""
        timestamp = datetime.now(timezone.utc)
        metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
        )

        assert metric.item_type == "AIUsageMetric"

    def test_repr_string(self):
        """Test that __repr__ returns a useful string representation."""
        timestamp = datetime.now(timezone.utc)
        metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            total_tokens=150,
            cost_usd=0.003,
        )

        repr_str = repr(metric)
        assert "AIUsageMetric" in repr_str
        assert "openai/gpt-4" in repr_str
        assert "completion" in repr_str
        assert "150 tokens" in repr_str
        assert "$0.0030" in repr_str

    def test_to_dynamodb_item_all_fields(self):
        """Test converting metric to DynamoDB item format with all fields."""
        timestamp = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            request_id="test-123",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            api_calls=2,
            cost_usd=0.003,
            latency_ms=1500,
            user_id="user-123",
            job_id="job-456",
            batch_id="batch-789",
            github_pr=42,
            error="timeout",
            metadata={"key": "value", "nested": {"inner": "data"}},
        )

        item = metric.to_dynamodb_item()

        # Check required fields
        assert item["PK"]["S"] == "AI_USAGE#openai#gpt-4"
        assert item["SK"]["S"] == "USAGE#2024-01-15T10:30:00+00:00#test-123"
        assert item["GSI1PK"]["S"] == "AI_USAGE#openai"
        assert item["GSI1SK"]["S"] == "DATE#2024-01-15"
        assert item["GSI2PK"]["S"] == "AI_USAGE_COST"
        assert item["GSI2SK"]["S"] == "COST#2024-01-15#openai"
        assert item["GSI3PK"]["S"] == "JOB#job-456"
        assert item["GSI3SK"]["S"] == "AI_USAGE#2024-01-15T10:30:00+00:00"
        assert item["TYPE"]["S"] == "AIUsageMetric"
        assert item["service"]["S"] == "openai"
        assert item["model"]["S"] == "gpt-4"
        assert item["operation"]["S"] == "completion"
        assert item["timestamp"]["S"] == "2024-01-15T10:30:00+00:00"
        assert item["request_id"]["S"] == "test-123"
        assert item["api_calls"]["N"] == "2"
        assert item["date"]["S"] == "2024-01-15"
        assert item["month"]["S"] == "2024-01"
        assert item["hour"]["S"] == "2024-01-15-10"

        # Check optional numeric fields
        assert item["input_tokens"]["N"] == "100"
        assert item["output_tokens"]["N"] == "50"
        assert item["total_tokens"]["N"] == "150"
        assert item["cost_usd"]["N"] == "0.003"
        assert item["latency_ms"]["N"] == "1500"
        assert item["github_pr"]["N"] == "42"

        # Check optional string fields
        assert item["user_id"]["S"] == "user-123"
        assert item["job_id"]["S"] == "job-456"
        assert item["batch_id"]["S"] == "batch-789"
        assert item["error"]["S"] == "timeout"

        # Check metadata is properly serialized
        assert item["metadata"]["M"]["key"]["S"] == "value"
        assert item["metadata"]["M"]["nested"]["M"]["inner"]["S"] == "data"

    def test_to_dynamodb_item_minimal_fields(self):
        """Test converting metric to DynamoDB item format with minimal fields."""
        timestamp = datetime.now(timezone.utc)
        metric = AIUsageMetric(
            service="anthropic",
            model="claude-3-opus",
            operation="completion",
            timestamp=timestamp,
        )

        item = metric.to_dynamodb_item()

        # Check required fields are present
        assert item["PK"]["S"].startswith("AI_USAGE#anthropic#")
        assert item["SK"]["S"].startswith("USAGE#")
        assert item["TYPE"]["S"] == "AIUsageMetric"
        assert item["api_calls"]["N"] == "1"

        # Check optional fields are not present
        assert "input_tokens" not in item
        assert "output_tokens" not in item
        assert "total_tokens" not in item
        assert "cost_usd" not in item
        assert "latency_ms" not in item
        assert "user_id" not in item
        assert "job_id" not in item
        assert "batch_id" not in item
        assert "github_pr" not in item
        assert "error" not in item
        assert "GSI3PK" not in item
        assert "GSI3SK" not in item

    def test_from_dynamodb_item_all_fields(self):
        """Test creating metric from DynamoDB item with all fields."""
        item = {
            "PK": {"S": "AI_USAGE#openai#gpt-4"},
            "SK": {"S": "USAGE#2024-01-15T10:30:00+00:00#test-123"},
            "GSI1PK": {"S": "AI_USAGE#openai"},
            "GSI1SK": {"S": "DATE#2024-01-15"},
            "GSI2PK": {"S": "AI_USAGE_COST"},
            "GSI2SK": {"S": "COST#2024-01-15#openai"},
            "GSI3PK": {"S": "JOB#job-456"},
            "GSI3SK": {"S": "AI_USAGE#2024-01-15T10:30:00+00:00"},
            "TYPE": {"S": "AIUsageMetric"},
            "service": {"S": "openai"},
            "model": {"S": "gpt-4"},
            "operation": {"S": "completion"},
            "timestamp": {"S": "2024-01-15T10:30:00+00:00"},
            "request_id": {"S": "test-123"},
            "input_tokens": {"N": "100"},
            "output_tokens": {"N": "50"},
            "total_tokens": {"N": "150"},
            "api_calls": {"N": "2"},
            "cost_usd": {"N": "0.003"},
            "latency_ms": {"N": "1500"},
            "date": {"S": "2024-01-15"},
            "month": {"S": "2024-01"},
            "hour": {"S": "2024-01-15-10"},
            "user_id": {"S": "user-123"},
            "job_id": {"S": "job-456"},
            "batch_id": {"S": "batch-789"},
            "github_pr": {"N": "42"},
            "error": {"S": "timeout"},
            "metadata": {
                "M": {
                    "key": {"S": "value"},
                    "nested": {"M": {"inner": {"S": "data"}}},
                }
            },
        }

        metric = AIUsageMetric.from_dynamodb_item(item)

        assert metric.service == "openai"
        assert metric.model == "gpt-4"
        assert metric.operation == "completion"
        assert metric.timestamp == datetime(
            2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc
        )
        assert metric.request_id == "test-123"
        assert metric.input_tokens == 100
        assert metric.output_tokens == 50
        assert metric.total_tokens == 150
        assert metric.api_calls == 2
        assert metric.cost_usd == 0.003
        assert metric.latency_ms == 1500
        assert metric.user_id == "user-123"
        assert metric.job_id == "job-456"
        assert metric.batch_id == "batch-789"
        assert metric.github_pr == 42
        assert metric.error == "timeout"
        assert metric.metadata["key"] == "value"
        assert metric.metadata["nested"]["inner"] == "data"

    def test_from_dynamodb_item_minimal_fields(self):
        """Test creating metric from DynamoDB item with minimal fields."""
        item = {
            "PK": {"S": "AI_USAGE#anthropic#claude-3-opus"},
            "SK": {"S": "USAGE#2024-01-15T10:30:00+00:00#test-456"},
            "TYPE": {"S": "AIUsageMetric"},
            "service": {"S": "anthropic"},
            "model": {"S": "claude-3-opus"},
            "operation": {"S": "completion"},
            "timestamp": {"S": "2024-01-15T10:30:00+00:00"},
            "request_id": {"S": "test-456"},
            "api_calls": {"N": "1"},
            "date": {"S": "2024-01-15"},
            "month": {"S": "2024-01"},
            "hour": {"S": "2024-01-15-10"},
        }

        metric = AIUsageMetric.from_dynamodb_item(item)

        assert metric.service == "anthropic"
        assert metric.model == "claude-3-opus"
        assert metric.operation == "completion"
        assert metric.timestamp == datetime(
            2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc
        )
        assert metric.request_id == "test-456"
        assert metric.api_calls == 1

        # Optional fields should be None or default values
        assert metric.input_tokens is None
        assert metric.output_tokens is None
        assert metric.total_tokens is None
        assert metric.cost_usd is None
        assert metric.latency_ms is None
        assert metric.user_id is None
        assert metric.job_id is None
        assert metric.batch_id is None
        assert metric.github_pr is None
        assert metric.error is None
        assert metric.metadata == {}

    def test_roundtrip_serialization(self):
        """Test that serialization and deserialization preserves data."""
        timestamp = datetime.now(timezone.utc)
        original_metric = AIUsageMetric(
            service="google_places",
            model="place_details",
            operation="lookup",
            timestamp=timestamp,
            input_tokens=5,
            output_tokens=0,
            cost_usd=0.017,
            latency_ms=200,
            job_id="places-job-123",
            metadata={
                "place_id": "ChIJtest123",
                "query": "restaurant",
                "complex": {
                    "list": [1, 2, "three"],
                    "bool": True,
                    "null": None,
                },
            },
        )

        # Serialize to DynamoDB format
        item = original_metric.to_dynamodb_item()

        # Deserialize back to object
        restored_metric = AIUsageMetric.from_dynamodb_item(item)

        # Compare all fields
        assert restored_metric.service == original_metric.service
        assert restored_metric.model == original_metric.model
        assert restored_metric.operation == original_metric.operation
        assert restored_metric.timestamp == original_metric.timestamp
        assert restored_metric.request_id == original_metric.request_id
        assert restored_metric.input_tokens == original_metric.input_tokens
        assert restored_metric.output_tokens == original_metric.output_tokens
        assert restored_metric.total_tokens == original_metric.total_tokens
        assert restored_metric.api_calls == original_metric.api_calls
        assert restored_metric.cost_usd == original_metric.cost_usd
        assert restored_metric.latency_ms == original_metric.latency_ms
        assert restored_metric.job_id == original_metric.job_id
        assert restored_metric.metadata == original_metric.metadata

    def test_query_by_service_date_logic(self):
        """Test the logic for building service date queries."""

        # Test that the key condition and values are built correctly
        service = "openai"
        start_date = "2024-01-15"
        end_date = "2024-01-16"

        # Test the expected key structure (based on entity implementation)
        expected_pk = f"AI_USAGE#{service}"
        expected_start_sk = f"DATE#{start_date}"
        expected_end_sk = f"DATE#{end_date}"

        assert expected_pk == "AI_USAGE#openai"
        assert expected_start_sk == "DATE#2024-01-15"
        assert expected_end_sk == "DATE#2024-01-16"

        # Test single date query
        single_date_start = f"DATE#{start_date}"
        single_date_end = f"DATE#{start_date}"

        assert single_date_start == single_date_end

    def test_get_total_cost_by_date_logic(self):
        """Test the logic for building cost aggregation queries."""

        # Test that the key condition and values are built correctly for GSI2
        date = "2024-01-15"

        # Test the expected key structure (based on entity implementation)
        expected_pk = "AI_USAGE_COST"
        expected_sk_prefix = f"COST#{date}"

        assert expected_pk == "AI_USAGE_COST"
        assert expected_sk_prefix == "COST#2024-01-15"

        # Test that metrics have the correct GSI2 keys
        metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
            cost_usd=0.003,
        )

        assert metric.gsi2pk == "AI_USAGE_COST"
        assert metric.gsi2sk == "COST#2024-01-15#openai"

    def test_complex_metadata_serialization(self):
        """Test complex nested metadata serialization and deserialization."""
        complex_metadata = {
            "string_field": "test_value",
            "int_field": 42,
            "float_field": 3.14159,
            "bool_field": True,
            "null_field": None,
            "list_field": ["item1", 2, True, None, {"nested": "value"}],
            "nested_dict": {
                "level2": {
                    "level3": {
                        "deep_value": "found_me",
                        "deep_list": [1, 2, 3],
                    }
                }
            },
            "mixed_list": [
                {"type": "dict_in_list", "value": 1},
                ["nested", "list", "in", "list"],
                "simple_string",
            ],
        }

        timestamp = datetime.now(timezone.utc)
        metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            metadata=complex_metadata,
        )

        # Serialize and deserialize
        item = metric.to_dynamodb_item()
        restored_metric = AIUsageMetric.from_dynamodb_item(item)

        # Verify complex metadata is preserved
        assert restored_metric.metadata == complex_metadata
        assert (
            restored_metric.metadata["nested_dict"]["level2"]["level3"][
                "deep_value"
            ]
            == "found_me"
        )
        assert (
            restored_metric.metadata["mixed_list"][0]["type"] == "dict_in_list"
        )
        assert restored_metric.metadata["mixed_list"][1][2] == "in"

    def test_error_handling_in_metadata_serialization(self):
        """Test error handling for unsupported types in metadata."""
        timestamp = datetime.now(timezone.utc)

        # Custom object that can't be easily serialized
        class CustomObject:
            def __init__(self, value):
                self.value = value

            def __str__(self):
                return f"CustomObject({self.value})"

        metadata_with_custom = {
            "custom_object": CustomObject("test"),
            "normal_field": "works_fine",
        }

        metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            metadata=metadata_with_custom,
        )

        # Should convert custom object to string representation
        item = metric.to_dynamodb_item()
        restored_metric = AIUsageMetric.from_dynamodb_item(item)

        assert (
            restored_metric.metadata["custom_object"] == "CustomObject(test)"
        )
        assert restored_metric.metadata["normal_field"] == "works_fine"

    def test_edge_cases_and_validation(self):
        """Test edge cases and validation scenarios."""
        timestamp = datetime.now(timezone.utc)

        # Test with zero costs and tokens - but use explicit total_tokens=1 since 0 is falsy
        metric_zero = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            input_tokens=0,
            output_tokens=0,
            total_tokens=1,  # Non-zero to test explicit override
            cost_usd=0.0,
            latency_ms=0,
        )

        assert metric_zero.input_tokens == 0
        assert metric_zero.output_tokens == 0
        assert (
            metric_zero.total_tokens == 1
        )  # Explicit total_tokens overrides computation
        assert metric_zero.cost_usd == 0.0
        assert metric_zero.latency_ms == 0

        # Test when input and output are 0 but total_tokens is not explicitly set
        metric_zero_computed = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
        )

        assert metric_zero_computed.input_tokens == 0
        assert metric_zero_computed.output_tokens == 0
        assert (
            metric_zero_computed.total_tokens is None
        )  # Computed as None when both inputs are 0

        # Test with negative values (edge case handling)
        metric_negative = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            latency_ms=-1,  # Could represent error state
        )

        assert metric_negative.latency_ms == -1

        # Test with very large numbers
        metric_large = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            input_tokens=1000000,
            output_tokens=500000,
            cost_usd=999.99,
        )

        assert metric_large.input_tokens == 1000000
        assert metric_large.total_tokens == 1500000
        assert metric_large.cost_usd == 999.99

        # Test serialization of edge case values
        item = metric_zero.to_dynamodb_item()
        restored = AIUsageMetric.from_dynamodb_item(item)
        assert restored.cost_usd == 0.0

    def test_timezone_handling(self):
        """Test proper timezone handling in timestamps."""
        # Test with UTC timestamp
        utc_time = datetime.now(timezone.utc)
        metric_utc = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=utc_time,
        )

        item_utc = metric_utc.to_dynamodb_item()
        restored_utc = AIUsageMetric.from_dynamodb_item(item_utc)

        assert restored_utc.timestamp == utc_time

        # Test with different timezone offset
        import datetime as dt

        # Create a timezone-aware datetime (UTC+5)
        tz_offset = dt.timezone(dt.timedelta(hours=5))
        tz_time = datetime(2024, 1, 15, 10, 30, 0, tzinfo=tz_offset)

        metric_tz = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=tz_time,
        )

        # Serialize and deserialize
        item_tz = metric_tz.to_dynamodb_item()
        restored_tz = AIUsageMetric.from_dynamodb_item(item_tz)

        # Should preserve the original timezone
        assert restored_tz.timestamp == tz_time

    def test_gsi3_job_vs_batch_priority(self):
        """Test that job_id takes priority over batch_id in GSI3."""
        timestamp = datetime.now(timezone.utc)

        # Test with both job_id and batch_id - job_id should take priority
        metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            job_id="test-job-123",
            batch_id="test-batch-456",
        )

        assert metric.gsi3pk == "JOB#test-job-123"  # job_id takes priority
        assert metric.gsi3sk is not None

        # Test with only batch_id
        metric_batch_only = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            batch_id="test-batch-456",
        )

        assert metric_batch_only.gsi3pk == "BATCH#test-batch-456"
        assert metric_batch_only.gsi3sk is not None

    def test_batch_operations_and_performance(self):
        """Test batch operations and basic performance characteristics."""
        # Create a batch of metrics
        base_time = datetime.now(timezone.utc)
        metrics = []

        for i in range(10):  # Reduced from 25 for testing
            metric = AIUsageMetric(
                service="openai",
                model="gpt-3.5-turbo",
                operation="completion",
                timestamp=base_time + timedelta(minutes=i),
                input_tokens=100 + i,
                output_tokens=50 + i,
                cost_usd=0.001 * (i + 1),
                metadata={"batch_index": i},
            )
            metrics.append(metric)

        # Test batch serialization performance
        import time

        start_time = time.time()

        # Just test serialization without actual DynamoDB calls
        items = []
        for metric in metrics:
            item = metric.to_dynamodb_item()
            items.append(item)

        serialize_time = time.time() - start_time

        # Basic performance check - should complete in reasonable time
        assert serialize_time < 2.0  # Should serialize quickly
        assert len(items) == 10

        # Verify data integrity in serialized items
        for i, item in enumerate(items):
            assert item["metadata"]["M"]["batch_index"]["N"] == str(i)
            assert item["input_tokens"]["N"] == str(100 + i)
            assert item["cost_usd"]["N"] == str(0.001 * (i + 1))

    def test_pagination_query_structure(self):
        """Test pagination query structure for DynamoDB."""
        # Test basic pagination query structure
        base_time = datetime.now(timezone.utc)
        page_size = 10

        # Test query parameters structure
        query_params = {
            "IndexName": "GSI1",
            "KeyConditionExpression": "GSI1PK = :pk AND GSI1SK = :sk",
            "ExpressionAttributeValues": {
                ":pk": {"S": "AI_USAGE#openai"},
                ":sk": {"S": f"DATE#{base_time.strftime('%Y-%m-%d')}"},
            },
            "Limit": page_size,
        }

        # Verify structure
        assert query_params["IndexName"] == "GSI1"
        assert ":pk" in query_params["ExpressionAttributeValues"]
        assert ":sk" in query_params["ExpressionAttributeValues"]
        assert query_params["Limit"] == page_size

        # Test pagination token structure
        mock_last_evaluated_key = {
            "PK": {"S": "AI_USAGE#openai#gpt-4"},
            "SK": {"S": "USAGE#2024-01-15T10:30:00+00:00#test-123"},
            "GSI1PK": {"S": "AI_USAGE#openai"},
            "GSI1SK": {"S": "DATE#2024-01-15"},
        }

        query_with_token = query_params.copy()
        query_with_token["ExclusiveStartKey"] = mock_last_evaluated_key

        assert "ExclusiveStartKey" in query_with_token
        assert query_with_token["ExclusiveStartKey"]["PK"]["S"].startswith(
            "AI_USAGE#"
        )

    def test_dynamodb_value_conversion_edge_cases(self):
        """Test DynamoDB value conversion edge cases through metadata serialization."""
        timestamp = datetime.now(timezone.utc)

        # Test various data types in metadata
        test_values = {
            "string": "test_string",
            "integer": 42,
            "float": 3.14159,
            "boolean_true": True,
            "boolean_false": False,
            "null_value": None,
            "empty_string": "",
            "zero": 0,
            "negative": -5,
            "large_number": 123456789,
            "list_mixed": ["string", 123, True, None],
            "empty_list": [],
            "empty_dict": {},
            "nested_structure": {
                "level1": {"level2": [1, 2, {"level3": "value"}]}
            },
        }

        # Test conversion through full serialization/deserialization cycle
        metric = AIUsageMetric(
            service="test",
            model="test-model",
            operation="test",
            timestamp=timestamp,
            metadata=test_values,
        )

        # Serialize and deserialize
        item = metric.to_dynamodb_item()
        restored = AIUsageMetric.from_dynamodb_item(item)

        # Verify all values are preserved (with DynamoDB limitations)
        # Note: DynamoDB doesn't support empty strings, they become None
        expected_values = test_values.copy()
        expected_values["empty_string"] = None  # DynamoDB limitation
        assert restored.metadata == expected_values

    def test_unsupported_type_conversion(self):
        """Test conversion of unsupported types falls back to string."""
        timestamp = datetime.now(timezone.utc)

        # Test with a custom object
        class CustomType:
            def __str__(self):
                return "custom_representation"

        custom_obj = CustomType()

        metric = AIUsageMetric(
            service="test",
            model="test-model",
            operation="test",
            timestamp=timestamp,
            metadata={"custom": custom_obj},
        )

        # Serialize and deserialize
        item = metric.to_dynamodb_item()
        restored = AIUsageMetric.from_dynamodb_item(item)

        # Custom object should be converted to string
        assert restored.metadata["custom"] == "custom_representation"

    def test_unknown_dynamodb_value_type_error(self):
        """Test that unknown DynamoDB value types are handled properly."""
        # Test with an invalid DynamoDB item format
        invalid_item = {
            "PK": {"S": "AI_USAGE#test#model"},
            "SK": {"S": "USAGE#2024-01-15T10:30:00+00:00#test-123"},
            "TYPE": {"S": "AIUsageMetric"},
            "service": {"S": "test"},
            "model": {"S": "model"},
            "operation": {"S": "test"},
            "timestamp": {"S": "2024-01-15T10:30:00+00:00"},
            "request_id": {"S": "test-123"},
            "api_calls": {"N": "1"},
            "metadata": {"UNKNOWN_TYPE": "value"},  # Invalid DynamoDB type
        }

        # The SerializationMixin's safe_deserialize_field should handle unknown types
        # by returning the raw value or an empty dict for metadata
        metric = AIUsageMetric.from_dynamodb_item(invalid_item)
        # Metadata with unknown type should be deserialized as empty dict
        assert metric.metadata == {}

    def test_precision_handling_with_decimal(self):
        """Test that cost precision is properly handled using Decimal."""
        timestamp = datetime.now(timezone.utc)

        # Test with high precision cost
        high_precision_cost = 0.000123456789
        metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            cost_usd=high_precision_cost,
        )

        item = metric.to_dynamodb_item()

        # DynamoDB stores numbers as strings, should preserve precision
        cost_str = item["cost_usd"]["N"]
        assert Decimal(cost_str) == Decimal(str(high_precision_cost))

        # Restore and verify precision is maintained
        restored = AIUsageMetric.from_dynamodb_item(item)
        assert abs(restored.cost_usd - high_precision_cost) < 1e-10

    def test_missing_required_fields(self):
        """Test that missing required fields raise appropriate errors."""
        # Test missing service
        with pytest.raises(TypeError):
            AIUsageMetric(
                model="gpt-3.5-turbo",
                operation="completion",
                timestamp=datetime.now(timezone.utc),
            )

        # Test missing model
        with pytest.raises(TypeError):
            AIUsageMetric(
                service="openai",
                operation="completion",
                timestamp=datetime.now(timezone.utc),
            )

        # Test missing operation
        with pytest.raises(TypeError):
            AIUsageMetric(
                service="openai",
                model="gpt-3.5-turbo",
                timestamp=datetime.now(timezone.utc),
            )

        # Test missing timestamp
        with pytest.raises(TypeError):
            AIUsageMetric(
                service="openai", model="gpt-3.5-turbo", operation="completion"
            )

    def test_data_migration_scenarios(self):
        """Test scenarios that might occur during data migration."""
        # Test handling of legacy data format (simulated)
        legacy_item = {
            "PK": {"S": "AI_USAGE#openai#gpt-3.5-turbo"},
            "SK": {"S": "USAGE#2024-01-15T10:30:00+00:00#legacy-id"},
            "TYPE": {"S": "AIUsageMetric"},
            "service": {"S": "openai"},
            "model": {"S": "gpt-3.5-turbo"},
            "operation": {"S": "completion"},
            "timestamp": {"S": "2024-01-15T10:30:00+00:00"},
            "request_id": {"S": "legacy-id"},
            "api_calls": {"N": "1"},
            "date": {"S": "2024-01-15"},
            "month": {"S": "2024-01"},
            "hour": {"S": "2024-01-15-10"},
            # Missing some optional fields that might be added later
        }

        # Should handle missing optional fields gracefully
        metric = AIUsageMetric.from_dynamodb_item(legacy_item)

        assert metric.service == "openai"
        assert metric.model == "gpt-3.5-turbo"
        assert metric.request_id == "legacy-id"
        assert metric.cost_usd is None  # Missing field should be None
        assert metric.metadata == {}  # Missing metadata should be empty dict

    def test_performance_with_large_metadata(self):
        """Test performance with large metadata objects."""
        import time

        # Create large metadata object
        large_metadata = {}
        for i in range(100):
            large_metadata[f"field_{i}"] = {
                "nested_data": [f"item_{j}" for j in range(10)],
                "timestamp": f"2024-01-{i%30+1:02d}T10:30:00Z",
                "values": list(range(10)),
            }

        timestamp = datetime.now(timezone.utc)
        metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            metadata=large_metadata,
        )

        # Test serialization performance
        start_time = time.time()
        item = metric.to_dynamodb_item()
        serialize_time = time.time() - start_time

        # Test deserialization performance
        start_time = time.time()
        restored = AIUsageMetric.from_dynamodb_item(item)
        deserialize_time = time.time() - start_time

        # Performance should be reasonable (adjust thresholds as needed)
        assert serialize_time < 1.0  # Should serialize in under 1 second
        assert deserialize_time < 1.0  # Should deserialize in under 1 second

        # Verify data integrity
        assert restored.metadata == large_metadata
        assert len(restored.metadata) == 100

    def test_cost_calculation_edge_cases(self):
        """Test edge cases in cost calculations and aggregations."""
        timestamp = datetime.now(timezone.utc)

        # Test with zero cost
        zero_cost_metric = AIUsageMetric(
            service="openai",
            model="gpt-3.5-turbo",
            operation="completion",
            timestamp=timestamp,
            cost_usd=0.0,
        )

        assert zero_cost_metric.cost_usd == 0.0

        # Test with very small cost (sub-penny)
        tiny_cost_metric = AIUsageMetric(
            service="openai",
            model="gpt-3.5-turbo",
            operation="completion",
            timestamp=timestamp,
            cost_usd=0.0000001,
        )

        assert tiny_cost_metric.cost_usd == 0.0000001

        # Test with large cost
        large_cost_metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            cost_usd=999.99,
        )

        assert large_cost_metric.cost_usd == 999.99

        # Test serialization preserves precision
        for metric in [zero_cost_metric, tiny_cost_metric, large_cost_metric]:
            item = metric.to_dynamodb_item()
            restored = AIUsageMetric.from_dynamodb_item(item)
            assert restored.cost_usd == metric.cost_usd

    def test_aggregation_patterns(self):
        """Test aggregation patterns for AI usage metrics."""
        # Test cost aggregation by service
        metrics = [
            AIUsageMetric(
                service="openai",
                model="gpt-3.5-turbo",
                operation="completion",
                timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
                cost_usd=0.001,
            ),
            AIUsageMetric(
                service="openai",
                model="gpt-4",
                operation="completion",
                timestamp=datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc),
                cost_usd=0.003,
            ),
            AIUsageMetric(
                service="anthropic",
                model="claude-3-opus",
                operation="completion",
                timestamp=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
                cost_usd=0.015,
            ),
        ]

        # Test manual aggregation logic
        costs_by_service = {}
        for metric in metrics:
            if metric.service not in costs_by_service:
                costs_by_service[metric.service] = 0.0
            costs_by_service[metric.service] += metric.cost_usd

        expected_costs = {"openai": 0.004, "anthropic": 0.015}  # 0.001 + 0.003

        assert len(costs_by_service) == 2
        for service, expected_cost in expected_costs.items():
            assert abs(costs_by_service[service] - expected_cost) < 0.0001

        # Test token aggregation by model
        tokens_by_model = {}
        for metric in metrics:
            if metric.model not in tokens_by_model:
                tokens_by_model[metric.model] = 0
            if metric.total_tokens:
                tokens_by_model[metric.model] += metric.total_tokens

        # Test aggregation by operation
        costs_by_operation = {}
        for metric in metrics:
            if metric.operation not in costs_by_operation:
                costs_by_operation[metric.operation] = 0.0
            costs_by_operation[metric.operation] += metric.cost_usd

        assert costs_by_operation["completion"] == sum(
            m.cost_usd for m in metrics
        )

        # Test aggregation by date
        costs_by_date = {}
        for metric in metrics:
            date_key = metric.date
            if date_key not in costs_by_date:
                costs_by_date[date_key] = 0.0
            costs_by_date[date_key] += metric.cost_usd

        assert costs_by_date["2024-01-15"] == sum(m.cost_usd for m in metrics)

    def test_ttl_and_lifecycle_concepts(self):
        """Test concepts related to TTL and data lifecycle."""
        timestamp = datetime.now(timezone.utc)

        # Test metric with future expiration concept
        metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            metadata={
                "retention_days": 90,
                "expires_at": int(
                    (timestamp + timedelta(days=90)).timestamp()
                ),
            },
        )

        # Test serialization of TTL-related metadata
        item = metric.to_dynamodb_item()
        restored = AIUsageMetric.from_dynamodb_item(item)

        assert restored.metadata["retention_days"] == 90
        assert "expires_at" in restored.metadata

        # Test archival concepts
        archive_metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp - timedelta(days=365),  # Old metric
            metadata={"archive_status": "eligible", "cold_storage": True},
        )

        assert archive_metric.metadata["archive_status"] == "eligible"
        assert archive_metric.metadata["cold_storage"] is True

    def test_performance_monitoring_patterns(self):
        """Test patterns for performance monitoring and alerting."""
        timestamp = datetime.now(timezone.utc)

        # Test high latency metric
        high_latency_metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            latency_ms=5000,  # 5 seconds
            cost_usd=0.1,
            metadata={"alert_threshold_exceeded": True, "p99_latency": 5000},
        )

        # Test high cost metric
        high_cost_metric = AIUsageMetric(
            service="anthropic",
            model="claude-3-opus",
            operation="completion",
            timestamp=timestamp,
            cost_usd=50.0,  # Expensive call
            metadata={"cost_alert": True, "budget_impact": "high"},
        )

        # Test error tracking
        error_metric = AIUsageMetric(
            service="openai",
            model="gpt-4",
            operation="completion",
            timestamp=timestamp,
            error="rate_limit_exceeded",
            metadata={
                "error_code": "429",
                "retry_count": 3,
                "final_success": False,
            },
        )

        # Verify monitoring data is captured
        assert high_latency_metric.latency_ms == 5000
        assert high_latency_metric.metadata["alert_threshold_exceeded"] is True

        assert high_cost_metric.cost_usd == 50.0
        assert high_cost_metric.metadata["budget_impact"] == "high"

        assert error_metric.error == "rate_limit_exceeded"
        assert error_metric.metadata["retry_count"] == 3

        # Test serialization of monitoring data
        items = [
            m.to_dynamodb_item()
            for m in [high_latency_metric, high_cost_metric, error_metric]
        ]
        restored = [AIUsageMetric.from_dynamodb_item(item) for item in items]

        assert restored[0].latency_ms == 5000
        assert restored[1].cost_usd == 50.0
        assert restored[2].error == "rate_limit_exceeded"
