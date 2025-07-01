"""
Integration tests for AI Usage API Lambda handler.

Tests the complete Lambda handler functionality including:
- Query parameter parsing and validation
- DynamoDB queries and pagination
- Aggregation of metrics by various dimensions
- Error handling and response formatting
"""

import json
import os
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from boto3.dynamodb.conditions import Key

from .index import (
    aggregate_by_day,
    aggregate_by_hour,
    aggregate_by_model,
    aggregate_by_operation,
    aggregate_by_service,
    aggregate_metrics,
    lambda_handler,
    query_ai_usage_metrics,
)


@pytest.fixture
def mock_dynamodb_table():
    """Mock DynamoDB table for testing."""
    table = MagicMock()
    table.table_name = "test-table"
    return table


@pytest.fixture
def sample_metrics():
    """Sample AI usage metrics for testing."""
    base_time = datetime(2024, 1, 15, 10, 0, 0)
    metrics = []

    # OpenAI metrics
    for i in range(5):
        metrics.append(
            {
                "PK": {"S": "AI_USAGE#openai#gpt-3.5-turbo"},
                "SK": {"S": f"USAGE#{base_time.isoformat()}#{i}"},
                "service": "openai",
                "model": "gpt-3.5-turbo",
                "operation": "completion",
                "timestamp": (base_time + timedelta(hours=i)).isoformat(),
                "date": "2024-01-15",
                "hour": f"2024-01-15-{10 + i:02d}",
                "inputTokens": 100 + i * 10,
                "outputTokens": 50 + i * 5,
                "totalTokens": 150 + i * 15,
                "costUSD": Decimal(str(0.000225 * (1 + i * 0.1))),
                "latencyMs": 500 + i * 100,
                "userId": "test-user",
                "jobId": f"job-{i}",
                "apiCalls": 1,
            }
        )

    # GPT-4 metrics
    for i in range(3):
        metrics.append(
            {
                "PK": {"S": "AI_USAGE#openai#gpt-4"},
                "SK": {"S": f"USAGE#{base_time.isoformat()}#{i}"},
                "service": "openai",
                "model": "gpt-4",
                "operation": "completion",
                "timestamp": (base_time + timedelta(hours=i)).isoformat(),
                "date": "2024-01-15",
                "hour": f"2024-01-15-{10 + i:02d}",
                "inputTokens": 200 + i * 20,
                "outputTokens": 100 + i * 10,
                "totalTokens": 300 + i * 30,
                "costUSD": Decimal(str(0.009 * (1 + i * 0.1))),
                "latencyMs": 1000 + i * 200,
                "userId": "test-user",
                "jobId": f"job-{i}",
                "apiCalls": 1,
            }
        )

    # Anthropic metrics
    for i in range(2):
        metrics.append(
            {
                "PK": {"S": "AI_USAGE#anthropic#claude-3-opus"},
                "SK": {"S": f"USAGE#{base_time.isoformat()}#{i}"},
                "service": "anthropic",
                "model": "claude-3-opus-20240229",
                "operation": "completion",
                "timestamp": (base_time + timedelta(hours=i)).isoformat(),
                "date": "2024-01-15",
                "hour": f"2024-01-15-{10 + i:02d}",
                "inputTokens": 150 + i * 15,
                "outputTokens": 75 + i * 10,
                "totalTokens": 225 + i * 25,
                "costUSD": Decimal(str(0.015 * (1 + i * 0.1))),
                "latencyMs": 800 + i * 150,
                "userId": "test-user",
                "apiCalls": 1,
            }
        )

    # Google Places metrics
    metrics.append(
        {
            "PK": {"S": "AI_USAGE#google_places#places_api"},
            "SK": {"S": f"USAGE#{base_time.isoformat()}#0"},
            "service": "google_places",
            "model": "places_api",
            "operation": "place_lookup",
            "timestamp": base_time.isoformat(),
            "date": "2024-01-15",
            "hour": "2024-01-15-10",
            "costUSD": Decimal("0.017"),
            "latencyMs": 200,
            "userId": "test-user",
            "apiCalls": 1,
        }
    )

    return metrics


@pytest.fixture
def mock_env():
    """Mock environment variables."""
    with patch.dict(os.environ, {"DYNAMODB_TABLE_NAME": "test-table"}):
        yield


@pytest.mark.integration
class TestAIUsageHandlerIntegration:
    """Integration tests for AI usage Lambda handler."""

    def test_lambda_handler_default_query(
        self, mock_dynamodb_table, sample_metrics, mock_env
    ):
        """Test Lambda handler with default query parameters."""
        # Mock DynamoDB responses for all services
        openai_metrics = [m for m in sample_metrics if m["service"] == "openai"]
        anthropic_metrics = [m for m in sample_metrics if m["service"] == "anthropic"]
        google_metrics = [m for m in sample_metrics if m["service"] == "google_places"]

        mock_dynamodb_table.query.side_effect = [
            {"Items": openai_metrics},
            {"Items": anthropic_metrics},
            {"Items": google_metrics},
        ]

        with patch("boto3.resource") as mock_boto3:
            mock_boto3.return_value.Table.return_value = mock_dynamodb_table

            # Call handler with no query parameters
            event = {"queryStringParameters": None}
            response = lambda_handler(event, None)

            # Verify response structure
            assert response["statusCode"] == 200
            assert "application/json" in response["headers"]["Content-Type"]

            body = json.loads(response["body"])
            assert "summary" in body
            assert "aggregations" in body
            assert "query" in body

            # Verify summary calculations
            summary = body["summary"]
            # Total is 8 OpenAI + 2 Anthropic + 1 Google Places = 11
            assert summary["total_api_calls"] == 11
            assert summary["total_tokens"] > 0
            assert summary["total_cost_usd"] > 0
            assert summary["average_cost_per_call"] > 0
            assert "date_range" in summary

    def test_lambda_handler_with_service_filter(
        self, mock_dynamodb_table, sample_metrics, mock_env
    ):
        """Test Lambda handler with service filter."""
        # Return only OpenAI metrics
        openai_metrics = [m for m in sample_metrics if m["service"] == "openai"]
        mock_dynamodb_table.query.return_value = {"Items": openai_metrics}

        with patch("boto3.resource") as mock_boto3:
            mock_boto3.return_value.Table.return_value = mock_dynamodb_table

            # Call handler with service filter
            event = {
                "queryStringParameters": {
                    "service": "openai",
                    "start_date": "2024-01-15",
                    "end_date": "2024-01-15",
                }
            }
            response = lambda_handler(event, None)

            assert response["statusCode"] == 200
            body = json.loads(response["body"])

            # Verify query was called with correct parameters
            assert mock_dynamodb_table.query.called
            call_args = mock_dynamodb_table.query.call_args
            assert call_args.kwargs["IndexName"] == "GSI1"

            # Verify only OpenAI metrics in response
            assert body["query"]["service"] == "openai"
            assert body["summary"]["total_api_calls"] == 8  # 5 GPT-3.5 + 3 GPT-4

    def test_lambda_handler_with_operation_filter(
        self, mock_dynamodb_table, sample_metrics, mock_env
    ):
        """Test Lambda handler with operation filter."""
        # Return all metrics but filter will be applied
        mock_dynamodb_table.query.side_effect = [
            {"Items": [m for m in sample_metrics if m["service"] == "openai"]},
            {"Items": [m for m in sample_metrics if m["service"] == "anthropic"]},
            {"Items": [m for m in sample_metrics if m["service"] == "google_places"]},
        ]

        with patch("boto3.resource") as mock_boto3:
            mock_boto3.return_value.Table.return_value = mock_dynamodb_table

            # Call handler with operation filter
            event = {
                "queryStringParameters": {
                    "operation": "completion",
                    "start_date": "2024-01-15",
                    "end_date": "2024-01-15",
                }
            }
            response = lambda_handler(event, None)

            assert response["statusCode"] == 200
            body = json.loads(response["body"])

            # Should exclude Google Places (place_lookup operation)
            assert body["summary"]["total_api_calls"] == 10  # 8 OpenAI + 2 Anthropic

    def test_lambda_handler_with_multiple_aggregations(
        self, mock_dynamodb_table, sample_metrics, mock_env
    ):
        """Test Lambda handler with multiple aggregation levels."""
        mock_dynamodb_table.query.side_effect = [
            {"Items": [m for m in sample_metrics if m["service"] == "openai"]},
            {"Items": [m for m in sample_metrics if m["service"] == "anthropic"]},
            {"Items": [m for m in sample_metrics if m["service"] == "google_places"]},
        ]

        with patch("boto3.resource") as mock_boto3:
            mock_boto3.return_value.Table.return_value = mock_dynamodb_table

            # Call handler with multiple aggregations
            event = {
                "queryStringParameters": {
                    "aggregation": "day,service,model,operation,hour",
                    "start_date": "2024-01-15",
                    "end_date": "2024-01-15",
                }
            }
            response = lambda_handler(event, None)

            assert response["statusCode"] == 200
            body = json.loads(response["body"])

            # Verify all aggregations are present
            aggregations = body["aggregations"]
            assert "by_day" in aggregations
            assert "by_service" in aggregations
            assert "by_model" in aggregations
            assert "by_operation" in aggregations
            assert "by_hour" in aggregations

            # Verify by_service aggregation
            by_service = aggregations["by_service"]
            assert "openai" in by_service
            assert "anthropic" in by_service
            assert "google_places" in by_service

            # Verify OpenAI service details
            openai_stats = by_service["openai"]
            assert openai_stats["api_calls"] == 8
            assert set(openai_stats["models"]) == {"gpt-3.5-turbo", "gpt-4"}
            assert openai_stats["cost_usd"] > 0

    def test_lambda_handler_with_pagination(
        self, mock_dynamodb_table, sample_metrics, mock_env
    ):
        """Test Lambda handler handles pagination correctly."""
        # Simulate paginated response
        first_page = sample_metrics[:5]
        second_page = sample_metrics[5:]

        mock_dynamodb_table.query.side_effect = [
            {"Items": first_page, "LastEvaluatedKey": {"PK": "continue"}},
            {"Items": second_page},
        ]

        with patch("boto3.resource") as mock_boto3:
            mock_boto3.return_value.Table.return_value = mock_dynamodb_table

            event = {
                "queryStringParameters": {
                    "service": "openai",
                    "start_date": "2024-01-15",
                    "end_date": "2024-01-15",
                }
            }
            response = lambda_handler(event, None)

            assert response["statusCode"] == 200

            # Verify pagination was handled
            assert mock_dynamodb_table.query.call_count == 2
            second_call = mock_dynamodb_table.query.call_args_list[1]
            assert "ExclusiveStartKey" in second_call.kwargs

    def test_lambda_handler_error_handling(self, mock_env):
        """Test Lambda handler error handling."""
        with patch("boto3.resource") as mock_boto3:
            # Make DynamoDB query fail
            mock_boto3.return_value.Table.return_value.query.side_effect = Exception(
                "DynamoDB error"
            )

            event = {"queryStringParameters": {"service": "openai"}}
            response = lambda_handler(event, None)

            # Should return 500 error
            assert response["statusCode"] == 500
            assert "application/json" in response["headers"]["Content-Type"]

            body = json.loads(response["body"])
            assert "error" in body
            assert "DynamoDB error" in body["error"]

    def test_query_ai_usage_metrics_all_services(
        self, mock_dynamodb_table, sample_metrics
    ):
        """Test querying metrics for all services."""
        # Mock responses for each service
        mock_dynamodb_table.query.side_effect = [
            {"Items": [m for m in sample_metrics if m["service"] == "openai"]},
            {"Items": [m for m in sample_metrics if m["service"] == "anthropic"]},
            {"Items": [m for m in sample_metrics if m["service"] == "google_places"]},
        ]

        metrics = query_ai_usage_metrics(
            mock_dynamodb_table,
            start_date="2024-01-15",
            end_date="2024-01-15",
        )

        assert len(metrics) == 11  # All metrics
        assert mock_dynamodb_table.query.call_count == 3  # One per service

    def test_aggregate_by_day_calculation(self, sample_metrics):
        """Test daily aggregation calculations."""
        result = aggregate_by_day(sample_metrics)

        assert "2024-01-15" in result
        daily = result["2024-01-15"]

        # Verify totals
        assert daily["api_calls"] == 11
        assert daily["tokens"] > 0
        assert daily["cost_usd"] > 0
        assert set(daily["services"]) == {
            "openai",
            "anthropic",
            "google_places",
        }

    def test_aggregate_by_service_calculation(self, sample_metrics):
        """Test service aggregation calculations."""
        result = aggregate_by_service(sample_metrics)

        # Verify all services present
        assert set(result.keys()) == {"openai", "anthropic", "google_places"}

        # Verify OpenAI aggregation
        openai = result["openai"]
        assert openai["api_calls"] == 8
        assert set(openai["models"]) == {"gpt-3.5-turbo", "gpt-4"}
        assert openai["operations"] == ["completion"]
        assert openai["average_cost_per_call"] > 0

    def test_aggregate_by_model_calculation(self, sample_metrics):
        """Test model aggregation calculations."""
        result = aggregate_by_model(sample_metrics)

        # Verify models present
        assert "gpt-3.5-turbo" in result
        assert "gpt-4" in result
        assert "claude-3-opus-20240229" in result

        # Verify GPT-3.5 aggregation
        gpt35 = result["gpt-3.5-turbo"]
        assert gpt35["api_calls"] == 5
        assert gpt35["service"] == "openai"
        assert gpt35["average_tokens_per_call"] > 0

    def test_aggregate_by_operation_calculation(self, sample_metrics):
        """Test operation aggregation calculations."""
        result = aggregate_by_operation(sample_metrics)

        # Verify operations
        assert "completion" in result
        assert "place_lookup" in result

        # Verify completion aggregation
        completion = result["completion"]
        assert completion["api_calls"] == 10  # 8 OpenAI + 2 Anthropic
        assert set(completion["services"]) == {"openai", "anthropic"}

    def test_aggregate_by_hour_calculation(self, sample_metrics):
        """Test hourly aggregation calculations."""
        result = aggregate_by_hour(sample_metrics)

        # Verify hours present (10, 11, 12, 13, 14)
        assert "10" in result
        assert "11" in result
        assert "12" in result

        # Verify hour 10 has multiple services
        hour_10 = result["10"]
        assert hour_10["api_calls"] >= 3  # At least one from each service

    @pytest.mark.integration
    def test_end_to_end_date_range_query(self, mock_dynamodb_table, mock_env):
        """Test end-to-end date range query with realistic data."""
        # Create metrics across multiple days
        metrics = []
        base_date = datetime(2024, 1, 10)

        for day in range(7):  # Week of data
            date = base_date + timedelta(days=day)
            date_str = date.strftime("%Y-%m-%d")

            for hour in range(0, 24, 6):  # 4 entries per day
                timestamp = date.replace(hour=hour)
                metrics.append(
                    {
                        "service": "openai",
                        "model": "gpt-3.5-turbo",
                        "operation": "completion",
                        "timestamp": timestamp.isoformat(),
                        "date": date_str,
                        "hour": f"{date_str}-{hour:02d}",
                        "inputTokens": 100,
                        "outputTokens": 50,
                        "totalTokens": 150,
                        "costUSD": Decimal("0.000225"),
                        "apiCalls": 1,
                    }
                )

        mock_dynamodb_table.query.return_value = {"Items": metrics}

        with patch("boto3.resource") as mock_boto3:
            mock_boto3.return_value.Table.return_value = mock_dynamodb_table

            event = {
                "queryStringParameters": {
                    "start_date": "2024-01-10",
                    "end_date": "2024-01-16",
                    "aggregation": "day",
                }
            }
            response = lambda_handler(event, None)

            assert response["statusCode"] == 200
            body = json.loads(response["body"])

            # Verify we have data for each day
            by_day = body["aggregations"]["by_day"]
            assert len(by_day) == 7

            # Each day should have some calls (mock returns all metrics for each service query)
            for date_str in by_day:
                assert by_day[date_str]["api_calls"] > 0

    @pytest.mark.integration
    def test_concurrent_request_handling(
        self, mock_dynamodb_table, sample_metrics, mock_env
    ):
        """Test handler can process concurrent requests correctly."""
        import threading
        from concurrent.futures import ThreadPoolExecutor

        mock_dynamodb_table.query.return_value = {"Items": sample_metrics}

        with patch("boto3.resource") as mock_boto3:
            mock_boto3.return_value.Table.return_value = mock_dynamodb_table

            def make_request(service):
                event = {
                    "queryStringParameters": {
                        "service": service,
                        "start_date": "2024-01-15",
                        "end_date": "2024-01-15",
                    }
                }
                return lambda_handler(event, None)

            # Make concurrent requests
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = []
                for service in ["openai", "anthropic", "google_places"]:
                    future = executor.submit(make_request, service)
                    futures.append(future)

                responses = [f.result() for f in futures]

            # Verify all requests succeeded
            assert all(r["statusCode"] == 200 for r in responses)

            # Each request should have its own response
            bodies = [json.loads(r["body"]) for r in responses]
            assert all(
                b["query"]["service"] in ["openai", "anthropic", "google_places"]
                for b in bodies
            )

    @pytest.mark.integration
    def test_large_dataset_aggregation_performance(self, mock_dynamodb_table, mock_env):
        """Test handler performance with large datasets."""
        # Create 1000 metrics
        large_metrics = []
        base_time = datetime(2024, 1, 1)

        for i in range(1000):
            timestamp = base_time + timedelta(minutes=i * 5)
            large_metrics.append(
                {
                    "service": ["openai", "anthropic", "google_places"][i % 3],
                    "model": ["gpt-3.5-turbo", "gpt-4", "claude-3-opus"][i % 3],
                    "operation": ["completion", "embedding", "place_lookup"][i % 3],
                    "timestamp": timestamp.isoformat(),
                    "date": timestamp.strftime("%Y-%m-%d"),
                    "hour": timestamp.strftime("%Y-%m-%d-%H"),
                    "totalTokens": 100 + (i % 100),
                    "costUSD": Decimal(str(0.001 * (1 + i % 10))),
                    "apiCalls": 1,
                }
            )

        mock_dynamodb_table.query.return_value = {"Items": large_metrics}

        with patch("boto3.resource") as mock_boto3:
            mock_boto3.return_value.Table.return_value = mock_dynamodb_table

            import time

            start_time = time.time()

            event = {
                "queryStringParameters": {
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-31",
                    "aggregation": "day,service,model,operation,hour",
                }
            }
            response = lambda_handler(event, None)

            elapsed = time.time() - start_time

            assert response["statusCode"] == 200
            assert elapsed < 5.0  # Should complete within 5 seconds

            body = json.loads(response["body"])
            # Should have processed a large number of requests
            assert body["summary"]["total_api_calls"] >= 1000

    def test_decimal_serialization(self, mock_dynamodb_table, mock_env):
        """Test Decimal values are properly serialized in JSON response."""
        metrics = [
            {
                "service": "openai",
                "model": "gpt-4",
                "operation": "completion",
                "costUSD": Decimal("10.123456789"),  # High precision decimal
                "totalTokens": 1000,
                "date": "2024-01-15",
                "hour": "2024-01-15-10",
                "apiCalls": 1,
            }
        ]

        mock_dynamodb_table.query.return_value = {"Items": metrics}

        with patch("boto3.resource") as mock_boto3:
            mock_boto3.return_value.Table.return_value = mock_dynamodb_table

            event = {"queryStringParameters": {"service": "openai"}}
            response = lambda_handler(event, None)

            # Should not raise JSON serialization error
            assert response["statusCode"] == 200
            body = json.loads(response["body"])

            # Verify decimal was converted to float
            assert isinstance(body["summary"]["total_cost_usd"], float)
            assert body["summary"]["total_cost_usd"] == 10.1235  # Rounded to 4 decimals
