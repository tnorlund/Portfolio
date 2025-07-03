"""Comprehensive unit tests for the AIUsageTracker class."""

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import pytest
from freezegun import freeze_time
from openai import OpenAI

from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric

# Add the parent directory to the path to access the tests utils
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from receipt_label.utils.ai_usage_tracker import AIUsageTracker
from receipt_label.utils.cost_calculator import AICostCalculator

from tests.utils.ai_usage_helpers import (
    create_mock_anthropic_response,
    create_mock_openai_response,
    create_test_tracking_context,
)


@pytest.mark.unit
class TestAIUsageTrackerInitialization:
    """Test AIUsageTracker initialization and configuration."""

    def test_init_with_all_parameters(self):
        """Test initialization with all parameters provided."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            user_id="test-user",
            track_to_dynamo=True,
            track_to_file=True,
            log_file="/tmp/test.jsonl",
        )

        assert tracker.dynamo_client == mock_dynamo
        assert tracker.table_name == "test-table"
        assert tracker.user_id == "test-user"
        assert tracker.track_to_dynamo is True
        assert tracker.track_to_file is True
        assert tracker.log_file == "/tmp/test.jsonl"
        assert tracker.current_job_id is None
        assert tracker.current_batch_id is None
        assert tracker.github_pr is None

    def test_init_with_defaults(self):
        """Test initialization with default values."""
        tracker = AIUsageTracker()

        assert tracker.dynamo_client is None

        # If DYNAMODB_TABLE_NAME is set in environment, it uses that as base name
        # Otherwise defaults to "AIUsageMetrics" with environment suffix
        if "DYNAMODB_TABLE_NAME" in os.environ:
            # In CI/test environments, use the explicit table name
            env_table_name = os.environ["DYNAMODB_TABLE_NAME"]
            # Should end with environment suffix
            assert tracker.table_name.endswith(
                "-development"
            ) or tracker.table_name.endswith("-cicd")
            # Should start with the base name from environment
            assert tracker.table_name.startswith(env_table_name.split("-")[0])
        else:
            # Default behavior when no explicit table name is set
            assert tracker.table_name.startswith("AIUsageMetrics-")
            assert tracker.table_name in [
                "AIUsageMetrics-development",
                "AIUsageMetrics-cicd",
            ]

        assert tracker.user_id == "default"
        assert (
            tracker.track_to_dynamo is False
        )  # False because no dynamo client
        assert tracker.track_to_file is False
        assert tracker.log_file == "/tmp/ai_usage.jsonl"

    def test_init_with_env_variables(self):
        """Test initialization reading from environment variables."""
        with patch.dict(
            os.environ,
            {
                "DYNAMODB_TABLE_NAME": "env-table",
                "USER_ID": "env-user",
            },
        ):
            tracker = AIUsageTracker()
            # Table name should have environment suffix (could be -development or -cicd depending on environment)
            assert tracker.table_name.startswith("env-table-")
            assert tracker.table_name in [
                "env-table-development",
                "env-table-cicd",
            ]
            assert tracker.user_id == "env-user"

    def test_track_to_dynamo_requires_client(self):
        """Test that track_to_dynamo is False when no dynamo client provided."""
        tracker = AIUsageTracker(track_to_dynamo=True)
        assert tracker.track_to_dynamo is False


@pytest.mark.unit
class TestAIUsageTrackerContext:
    """Test context management functionality."""

    def test_set_context_all_fields(self):
        """Test setting all context fields."""
        tracker = AIUsageTracker()
        tracker.set_tracking_context(
            job_id="job-123",
            batch_id="batch-456",
            github_pr=789,
            user_id="new-user",
        )

        assert tracker.current_job_id == "job-123"
        assert tracker.current_batch_id == "batch-456"
        assert tracker.github_pr == 789
        assert tracker.user_id == "new-user"

    def test_set_context_partial_fields(self):
        """Test setting only some context fields."""
        tracker = AIUsageTracker(user_id="original-user")
        tracker.current_job_id = "original-job"

        tracker.set_tracking_context(batch_id="new-batch")

        assert tracker.current_job_id == "original-job"  # Unchanged
        assert tracker.current_batch_id == "new-batch"
        assert tracker.user_id == "original-user"  # Unchanged

    def test_set_context_none_values_ignored(self):
        """Test that None values don't overwrite existing context."""
        tracker = AIUsageTracker()
        tracker.set_tracking_context(job_id="job-123", batch_id="batch-456")

        tracker.set_tracking_context(job_id=None, batch_id="batch-789")

        assert tracker.current_job_id == "job-123"  # Not overwritten
        assert tracker.current_batch_id == "batch-789"  # Updated


@pytest.mark.unit
class TestAIUsageTrackerStorage:
    """Test metric storage functionality."""

    def test_store_metric_to_dynamo_success(self):
        """Test successful storage to DynamoDB."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        metric = AIUsageMetric(
            service="openai",
            model="gpt-3.5-turbo",
            operation="completion",
            timestamp=datetime.now(timezone.utc),
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001,
            user_id="test-user",
        )

        tracker._store_metric(metric)

        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        assert call_args.kwargs["TableName"] == "test-table"
        assert "Item" in call_args.kwargs

    def test_store_metric_to_dynamo_failure(self, capsys):
        """Test handling of DynamoDB storage failure."""
        mock_dynamo = Mock()
        mock_dynamo.put_item.side_effect = Exception("DynamoDB error")

        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        metric = AIUsageMetric(
            service="openai",
            model="gpt-3.5-turbo",
            operation="completion",
            timestamp=datetime.now(timezone.utc),
            user_id="test-user",
        )

        tracker._store_metric(metric)

        captured = capsys.readouterr()
        assert (
            "Failed to store metric in DynamoDB: DynamoDB error"
            in captured.out
        )

    def test_store_metric_to_file_success(self):
        """Test successful storage to file."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            temp_file = f.name

        try:
            tracker = AIUsageTracker(
                track_to_file=True,
                log_file=temp_file,
            )

            metric = AIUsageMetric(
                service="openai",
                model="gpt-3.5-turbo",
                operation="completion",
                timestamp=datetime.now(timezone.utc),
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                cost_usd=0.001,
                latency_ms=500,
                user_id="test-user",
                job_id="job-123",
                batch_id="batch-456",
            )

            tracker._store_metric(metric)

            # Read and verify the file
            with open(temp_file, "r") as f:
                line = f.readline()
                data = json.loads(line)

            assert data["service"] == "openai"
            assert data["model"] == "gpt-3.5-turbo"
            assert data["operation"] == "completion"
            assert data["input_tokens"] == 100
            assert data["output_tokens"] == 50
            assert data["total_tokens"] == 150
            assert data["cost_usd"] == 0.001
            assert data["latency_ms"] == 500
            assert data["user_id"] == "test-user"
            assert data["job_id"] == "job-123"
            assert data["batch_id"] == "batch-456"

        finally:
            os.unlink(temp_file)

    def test_store_metric_to_file_failure(self, capsys):
        """Test handling of file storage failure."""
        tracker = AIUsageTracker(
            track_to_file=True,
            log_file="/invalid/path/file.jsonl",
        )

        metric = AIUsageMetric(
            service="openai",
            model="gpt-3.5-turbo",
            operation="completion",
            timestamp=datetime.now(timezone.utc),
            user_id="test-user",
        )

        tracker._store_metric(metric)

        captured = capsys.readouterr()
        assert "Failed to log metric to file:" in captured.out

    def test_store_metric_to_both_backends(self):
        """Test storing to both DynamoDB and file."""
        mock_dynamo = Mock()
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            temp_file = f.name

        try:
            tracker = AIUsageTracker(
                dynamo_client=mock_dynamo,
                table_name="test-table",
                validate_table_environment=False,  # Disable validation for test table
                track_to_dynamo=True,
                track_to_file=True,
                log_file=temp_file,
            )

            metric = AIUsageMetric(
                service="openai",
                model="gpt-3.5-turbo",
                operation="completion",
                timestamp=datetime.now(timezone.utc),
                user_id="test-user",
            )

            tracker._store_metric(metric)

            # Verify DynamoDB call
            mock_dynamo.put_item.assert_called_once()

            # Verify file write
            with open(temp_file, "r") as f:
                line = f.readline()
                data = json.loads(line)
            assert data["service"] == "openai"

        finally:
            os.unlink(temp_file)


@pytest.mark.unit
class TestOpenAICompletionTracking:
    """Test OpenAI completion tracking functionality."""

    @freeze_time("2024-01-01 12:00:00")
    def test_track_openai_completion_success(self):
        """Test successful OpenAI completion tracking."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
            user_id="test-user",
        )
        tracker.set_tracking_context(job_id="job-123", batch_id="batch-456")

        # Create a mock response
        mock_response = create_mock_openai_response(
            prompt_tokens=100,
            completion_tokens=50,
            model="gpt-3.5-turbo",
            content="Test response",
        )

        @tracker.track_openai_completion
        def call_openai(**kwargs):
            return mock_response

        # Call the wrapped function
        response = call_openai(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello"}],
            temperature=0.7,
            max_tokens=100,
        )

        assert response == mock_response

        # Verify metric was stored
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]

        # Verify metric fields
        assert item["service"]["S"] == "openai"
        assert item["model"]["S"] == "gpt-3.5-turbo"
        assert item["operation"]["S"] == "completion"
        assert item["inputTokens"]["N"] == "100"
        assert item["outputTokens"]["N"] == "50"
        assert item["totalTokens"]["N"] == "150"
        assert item["userId"]["S"] == "test-user"
        assert item["jobId"]["S"] == "job-123"
        assert item["batchId"]["S"] == "batch-456"

        # Verify metadata
        metadata_map = item["metadata"]["M"]
        assert metadata_map["function"]["S"] == "call_openai"
        assert float(metadata_map["temperature"]["N"]) == 0.7
        assert int(metadata_map["max_tokens"]["N"]) == 100

    def test_track_openai_completion_error(self):
        """Test OpenAI completion tracking with error."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        @tracker.track_openai_completion
        def call_openai(**kwargs):
            raise Exception("API Error")

        # Call should raise the exception
        with pytest.raises(Exception, match="API Error"):
            call_openai(model="gpt-3.5-turbo")

        # Metric should still be stored with error
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        assert item["error"]["S"] == "API Error"

    def test_track_openai_completion_with_batch_pricing(self):
        """Test OpenAI completion tracking with batch pricing."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        mock_response = create_mock_openai_response(
            prompt_tokens=1000,
            completion_tokens=500,
            model="gpt-3.5-turbo",
        )

        @tracker.track_openai_completion
        def call_openai(**kwargs):
            return mock_response

        # Regular pricing
        call_openai(model="gpt-3.5-turbo", is_batch=False)
        regular_cost = float(
            mock_dynamo.put_item.call_args.kwargs["Item"]["costUSD"]["N"]
        )

        # Batch pricing (should be 50% off)
        call_openai(model="gpt-3.5-turbo", is_batch=True)
        batch_cost = float(
            mock_dynamo.put_item.call_args.kwargs["Item"]["costUSD"]["N"]
        )

        assert batch_cost == regular_cost / 2

    def test_track_openai_completion_without_usage(self):
        """Test OpenAI completion tracking when response has no usage data."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        # Response without usage attribute
        mock_response = Mock()
        mock_response.model = "gpt-3.5-turbo"
        mock_response.usage = None

        @tracker.track_openai_completion
        def call_openai(**kwargs):
            return mock_response

        call_openai(model="gpt-3.5-turbo")

        # Verify metric was stored with None values
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]

        assert "input_tokens" not in item
        assert "output_tokens" not in item
        assert "total_tokens" not in item
        assert "cost_usd" not in item


@pytest.mark.unit
class TestOpenAIEmbeddingTracking:
    """Test OpenAI embedding tracking functionality."""

    def test_track_openai_embedding_success(self):
        """Test successful OpenAI embedding tracking."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
            user_id="test-user",
        )

        # Create a mock embedding response
        mock_response = Mock()
        mock_response.model = "text-embedding-3-small"
        mock_response.usage = Mock(total_tokens=1500)

        @tracker.track_openai_embedding
        def create_embedding(**kwargs):
            return mock_response

        # Call the wrapped function
        response = create_embedding(
            model="text-embedding-3-small",
            input=["text1", "text2", "text3"],
        )

        assert response == mock_response

        # Verify metric was stored
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]

        assert item["service"]["S"] == "openai"
        assert item["model"]["S"] == "text-embedding-3-small"
        assert item["operation"]["S"] == "embedding"
        assert item["totalTokens"]["N"] == "1500"

        # Verify metadata
        metadata_map = item["metadata"]["M"]
        assert metadata_map["function"]["S"] == "create_embedding"
        assert int(metadata_map["input_count"]["N"]) == 3

    def test_track_openai_embedding_error(self):
        """Test OpenAI embedding tracking with error."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        @tracker.track_openai_embedding
        def create_embedding(**kwargs):
            raise ValueError("Invalid input")

        with pytest.raises(ValueError, match="Invalid input"):
            create_embedding(input=["text"])

        # Verify error was tracked
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        assert item["error"]["S"] == "Invalid input"

    def test_track_openai_embedding_batch_pricing(self):
        """Test OpenAI embedding tracking with batch pricing."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        mock_response = Mock()
        mock_response.model = "text-embedding-3-large"
        mock_response.usage = Mock(total_tokens=2000)

        @tracker.track_openai_embedding
        def create_embedding(**kwargs):
            return mock_response

        # Test batch pricing
        create_embedding(
            model="text-embedding-3-large",
            input=["text"],
            is_batch=True,
        )

        # Calculate expected batch cost
        expected_cost = AICostCalculator.calculate_openai_cost(
            model="text-embedding-3-large",
            total_tokens=2000,
            is_batch=True,
        )

        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        actual_cost = float(item["costUSD"]["N"])

        assert actual_cost == expected_cost


@pytest.mark.unit
class TestAnthropicTracking:
    """Test Anthropic Claude tracking functionality."""

    def test_track_anthropic_completion_success(self):
        """Test successful Anthropic completion tracking."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
            user_id="test-user",
        )
        tracker.set_tracking_context(github_pr=123)

        # Create a mock Anthropic response
        mock_response = create_mock_anthropic_response(
            input_tokens=200,
            output_tokens=100,
            model="claude-3-opus-20240229",
            content="Test response",
        )

        @tracker.track_anthropic_completion
        def call_claude(**kwargs):
            return mock_response

        response = call_claude(
            model="claude-3-opus-20240229",
            max_tokens=1000,
        )

        assert response == mock_response

        # Verify metric was stored
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]

        assert item["service"]["S"] == "anthropic"
        assert item["model"]["S"] == "claude-3-opus-20240229"
        assert item["operation"]["S"] == "completion"
        assert item["inputTokens"]["N"] == "200"
        assert item["outputTokens"]["N"] == "100"
        assert item["githubPR"]["N"] == "123"

        # Verify metadata
        metadata_map = item["metadata"]["M"]
        assert int(metadata_map["max_tokens"]["N"]) == 1000

    def test_track_anthropic_completion_error(self):
        """Test Anthropic completion tracking with error."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        @tracker.track_anthropic_completion
        def call_claude(**kwargs):
            raise RuntimeError("Rate limit exceeded")

        with pytest.raises(RuntimeError, match="Rate limit exceeded"):
            call_claude(model="claude-3-haiku")

        # Verify error was tracked
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        assert item["error"]["S"] == "Rate limit exceeded"

    def test_track_anthropic_different_models(self):
        """Test tracking different Anthropic models."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        models = [
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
            "claude-3.5-sonnet-20240620",
        ]

        for model in models:
            mock_response = create_mock_anthropic_response(
                input_tokens=100,
                output_tokens=50,
                model=model,
            )

            @tracker.track_anthropic_completion
            def call_claude(**kwargs):
                return mock_response

            call_claude(model=model)

        assert mock_dynamo.put_item.call_count == len(models)

        # Verify each model was tracked correctly
        for i, model in enumerate(models):
            call_args = mock_dynamo.put_item.call_args_list[i]
            item = call_args.kwargs["Item"]
            assert item["model"]["S"] == model


@pytest.mark.unit
class TestGooglePlacesTracking:
    """Test Google Places API tracking functionality."""

    def test_track_google_places_success(self):
        """Test successful Google Places API tracking."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
            user_id="test-user",
        )

        @tracker.track_google_places("Place Details")
        def get_place_details(place_id):
            return {"result": {"name": "Test Place"}}

        result = get_place_details("ChIJtest123")

        assert result == {"result": {"name": "Test Place"}}

        # Verify metric was stored
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]

        assert item["service"]["S"] == "google_places"
        assert item["model"]["S"] == "places_api"
        assert item["operation"]["S"] == "Place Details"
        assert item["apiCalls"]["N"] == "1"

        # Verify cost calculation
        expected_cost = AICostCalculator.calculate_google_places_cost(
            operation="Place Details", api_calls=1
        )
        if "costUSD" in item:
            actual_cost = float(item["costUSD"]["N"])
            assert actual_cost == expected_cost
        else:
            # Cost might be None/0, which gets omitted
            assert expected_cost == 0 or expected_cost is None

    def test_track_google_places_error(self):
        """Test Google Places API tracking with error."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        @tracker.track_google_places("Nearby Search")
        def search_nearby(**kwargs):
            raise Exception("Invalid API key")

        with pytest.raises(Exception, match="Invalid API key"):
            search_nearby(location=(37.7749, -122.4194))

        # Verify error was tracked
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        assert item["error"]["S"] == "Invalid API key"

    def test_track_google_places_all_operations(self):
        """Test tracking all Google Places operation types."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        operations = [
            "Place Details",
            "Find Place",
            "Nearby Search",
            "Text Search",
            "Place Photos",
            "Place Autocomplete",
            "Query Autocomplete",
        ]

        for operation in operations:

            @tracker.track_google_places(operation)
            def api_call():
                return {"status": "OK"}

            api_call()

        assert mock_dynamo.put_item.call_count == len(operations)

        # Verify each operation was tracked with correct cost
        for i, operation in enumerate(operations):
            call_args = mock_dynamo.put_item.call_args_list[i]
            item = call_args.kwargs["Item"]
            assert item["operation"]["S"] == operation

            expected_cost = AICostCalculator.calculate_google_places_cost(
                operation=operation, api_calls=1
            )
            if "costUSD" in item:
                actual_cost = float(item["costUSD"]["N"])
                assert actual_cost == expected_cost
            else:
                # Cost might be None/0, which gets omitted
                assert expected_cost == 0 or expected_cost is None


@pytest.mark.unit
class TestGitHubClaudeReview:
    """Test GitHub Claude review tracking."""

    def test_track_github_claude_review_default(self):
        """Test tracking GitHub Claude review with defaults."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        tracker.track_github_claude_review(pr_number=123)

        # Verify metric was stored
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]

        assert item["service"]["S"] == "anthropic"
        assert item["model"]["S"] == "claude-3-opus"
        assert item["operation"]["S"] == "code_review"
        assert item["inputTokens"]["N"] == "3500"  # 70% of 5000
        assert item["outputTokens"]["N"] == "1500"  # 30% of 5000
        assert item["totalTokens"]["N"] == "5000"
        assert item["githubPR"]["N"] == "123"
        assert item["userId"]["S"] == "github-actions"

        # Verify metadata
        metadata_map = item["metadata"]["M"]
        assert int(metadata_map["pr_number"]["N"]) == 123
        assert metadata_map["workflow"]["S"] == "claude-review"

    def test_track_github_claude_review_custom_params(self):
        """Test tracking GitHub Claude review with custom parameters."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        tracker.track_github_claude_review(
            pr_number=456,
            model="claude-3-haiku",
            estimated_tokens=10000,
        )

        # Verify metric was stored
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]

        assert item["model"]["S"] == "claude-3-haiku"
        assert item["inputTokens"]["N"] == "7000"  # 70% of 10000
        assert item["outputTokens"]["N"] == "3000"  # 30% of 10000
        assert item["totalTokens"]["N"] == "10000"
        assert item["githubPR"]["N"] == "456"


@pytest.mark.unit
class TestWrappedOpenAIClient:
    """Test wrapped OpenAI client functionality."""

    def test_create_wrapped_openai_client_chat_completion(self):
        """Test wrapped OpenAI client for chat completions."""
        # Create mock OpenAI client
        mock_client = Mock(spec=OpenAI)
        mock_chat = Mock()
        mock_completions = Mock()
        mock_client.chat = mock_chat
        mock_chat.completions = mock_completions

        # Create mock response
        mock_response = create_mock_openai_response(
            prompt_tokens=100,
            completion_tokens=50,
            model="gpt-3.5-turbo",
        )
        mock_completions.create.return_value = mock_response

        # Create tracker
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        # Create wrapped client
        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        # Make a call
        response = wrapped_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello"}],
        )

        assert response == mock_response

        # Verify original client was called
        mock_completions.create.assert_called_once_with(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello"}],
        )

        # Verify tracking occurred
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        assert item["service"]["S"] == "openai"
        assert item["operation"]["S"] == "completion"

    def test_create_wrapped_openai_client_embeddings(self):
        """Test wrapped OpenAI client for embeddings."""
        # Create mock OpenAI client
        mock_client = Mock(spec=OpenAI)
        mock_embeddings = Mock()
        mock_client.embeddings = mock_embeddings

        # Create mock response
        mock_response = Mock()
        mock_response.model = "text-embedding-3-small"
        mock_response.usage = Mock(total_tokens=500)
        mock_embeddings.create.return_value = mock_response

        # Create tracker
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        # Create wrapped client
        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        # Make a call
        response = wrapped_client.embeddings.create(
            model="text-embedding-3-small",
            input=["test text"],
        )

        assert response == mock_response

        # Verify original client was called
        mock_embeddings.create.assert_called_once_with(
            model="text-embedding-3-small",
            input=["test text"],
        )

        # Verify tracking occurred
        mock_dynamo.put_item.assert_called_once()
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        assert item["service"]["S"] == "openai"
        assert item["operation"]["S"] == "embedding"
        assert item["totalTokens"]["N"] == "500"

    def test_wrapped_client_preserves_other_attributes(self):
        """Test that wrapped client preserves other attributes."""
        mock_client = Mock(spec=OpenAI)
        mock_client.api_key = "test-key"
        mock_client.base_url = "https://api.openai.com"

        tracker = AIUsageTracker()
        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        # Other attributes should be accessible
        assert wrapped_client.api_key == "test-key"
        assert wrapped_client.base_url == "https://api.openai.com"

    def test_wrapped_client_with_context(self):
        """Test wrapped client respects tracker context."""
        mock_client = Mock(spec=OpenAI)
        mock_chat = Mock()
        mock_completions = Mock()
        mock_client.chat = mock_chat
        mock_chat.completions = mock_completions

        mock_response = create_mock_openai_response()
        mock_completions.create.return_value = mock_response

        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        # Set context
        tracker.set_tracking_context(
            job_id="job-123",
            batch_id="batch-456",
            user_id="context-user",
        )

        wrapped_client = AIUsageTracker.create_wrapped_openai_client(
            mock_client, tracker
        )

        wrapped_client.chat.completions.create(model="gpt-3.5-turbo")

        # Verify context was included in metric
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        assert item["jobId"]["S"] == "job-123"
        assert item["batchId"]["S"] == "batch-456"
        assert item["userId"]["S"] == "context-user"


@pytest.mark.unit
class TestConcurrentTracking:
    """Test concurrent tracking scenarios."""

    def test_multiple_trackers_independent(self):
        """Test that multiple tracker instances are independent."""
        mock_dynamo1 = Mock()
        mock_dynamo2 = Mock()

        tracker1 = AIUsageTracker(
            dynamo_client=mock_dynamo1,
            table_name="table1",
            track_to_dynamo=True,
            user_id="user1",
            validate_table_environment=False,  # Disable validation for test table
        )

        tracker2 = AIUsageTracker(
            dynamo_client=mock_dynamo2,
            table_name="table2",
            track_to_dynamo=True,
            user_id="user2",
            validate_table_environment=False,  # Disable validation for test table
        )

        # Set different contexts
        tracker1.set_tracking_context(job_id="job1")
        tracker2.set_tracking_context(job_id="job2")

        # Track metrics with each tracker
        metric1 = AIUsageMetric(
            service="openai",
            model="gpt-3.5-turbo",
            operation="completion",
            timestamp=datetime.now(timezone.utc),
            user_id=tracker1.user_id,
            job_id=tracker1.current_job_id,
        )

        metric2 = AIUsageMetric(
            service="anthropic",
            model="claude-3-opus",
            operation="completion",
            timestamp=datetime.now(timezone.utc),
            user_id=tracker2.user_id,
            job_id=tracker2.current_job_id,
        )

        tracker1._store_metric(metric1)
        tracker2._store_metric(metric2)

        # Verify each tracker used its own configuration
        call_args1 = mock_dynamo1.put_item.call_args
        assert call_args1.kwargs["TableName"] == "table1"
        item1 = call_args1.kwargs["Item"]
        assert item1["userId"]["S"] == "user1"
        assert item1["jobId"]["S"] == "job1"

        call_args2 = mock_dynamo2.put_item.call_args
        assert call_args2.kwargs["TableName"] == "table2"
        item2 = call_args2.kwargs["Item"]
        assert item2["userId"]["S"] == "user2"
        assert item2["jobId"]["S"] == "job2"

    def test_tracking_with_threading_simulation(self):
        """Test tracking behavior in simulated concurrent scenarios."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        # Simulate multiple rapid calls
        @tracker.track_openai_completion
        def call_openai(request_id):
            response = Mock()
            response.model = "gpt-3.5-turbo"
            response.usage = Mock(
                prompt_tokens=100, completion_tokens=50, total_tokens=150
            )
            return response

        # Make multiple calls
        for i in range(5):
            call_openai(request_id=f"req-{i}")

        # All calls should be tracked
        assert mock_dynamo.put_item.call_count == 5


@pytest.mark.unit
class TestLatencyTracking:
    """Test latency measurement functionality."""

    def test_latency_measurement_accuracy(self):
        """Test that latency is measured accurately."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        # Create a function with known delay
        @tracker.track_openai_completion
        def slow_call(**kwargs):
            time.sleep(0.1)  # 100ms delay
            return create_mock_openai_response()

        start = time.time()
        slow_call(model="gpt-3.5-turbo")
        end = time.time()

        # Verify latency was tracked
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        latency_ms = int(item["latencyMs"]["N"])

        # Latency should be at least 100ms but not more than total execution time
        assert 100 <= latency_ms <= (end - start) * 1000 + 50  # 50ms tolerance

    def test_latency_tracking_on_error(self):
        """Test that latency is tracked even when function raises an error."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        @tracker.track_openai_completion
        def failing_call(**kwargs):
            time.sleep(0.05)  # 50ms delay
            raise ValueError("API Error")

        with pytest.raises(ValueError):
            failing_call(model="gpt-3.5-turbo")

        # Verify latency was tracked
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        latency_ms = int(item["latencyMs"]["N"])

        # Latency should be at least 50ms
        assert latency_ms >= 50


@pytest.mark.unit
class TestEdgeCases:
    """Test edge cases and error scenarios."""

    def test_empty_response_handling(self):
        """Test handling of empty or None responses."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        @tracker.track_openai_completion
        def return_none(**kwargs):
            return None

        result = return_none(model="gpt-3.5-turbo")
        assert result is None

        # Metric should still be stored
        mock_dynamo.put_item.assert_called_once()

    def test_malformed_response_handling(self):
        """Test handling of malformed responses."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        # Response with missing attributes
        @tracker.track_openai_completion
        def return_malformed(**kwargs):
            return {"data": "malformed"}

        result = return_malformed(model="gpt-3.5-turbo")
        assert result == {"data": "malformed"}

        # Metric should still be stored
        mock_dynamo.put_item.assert_called_once()

    def test_very_long_metadata(self):
        """Test handling of very long metadata values."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        @tracker.track_openai_completion
        def call_with_long_metadata(**kwargs):
            return create_mock_openai_response()

        # Call with very long parameter
        very_long_string = "x" * 10000
        call_with_long_metadata(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": very_long_string}],
        )

        # Should not crash and should store metric
        mock_dynamo.put_item.assert_called_once()

    def test_unicode_handling(self):
        """Test handling of unicode in tracked data."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        @tracker.track_anthropic_completion
        def call_with_unicode(**kwargs):
            response = Mock()
            response.usage = Mock(input_tokens=100, output_tokens=50)
            return response

        # Call with unicode parameters
        call_with_unicode(
            model="claude-3-opus",
            messages=[{"content": "Hello 世界 🌍 مرحبا"}],
        )

        # Should handle unicode properly
        mock_dynamo.put_item.assert_called_once()

    def test_zero_token_handling(self):
        """Test handling of zero token responses."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        @tracker.track_openai_completion
        def zero_tokens(**kwargs):
            response = Mock()
            response.model = "gpt-3.5-turbo"
            response.usage = Mock(
                prompt_tokens=0, completion_tokens=0, total_tokens=0
            )
            return response

        zero_tokens(model="gpt-3.5-turbo")

        # Verify zero cost is calculated
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        # Zero values might be stored or omitted depending on implementation
        if "inputTokens" in item:
            assert item["inputTokens"]["N"] == "0"
        if "outputTokens" in item:
            assert item["outputTokens"]["N"] == "0"
        if "totalTokens" in item:
            assert item["totalTokens"]["N"] == "0"
        if "costUSD" in item:
            assert float(item["costUSD"]["N"]) == 0.0


@pytest.mark.unit
class TestIntegrationWithCostCalculator:
    """Test integration with AICostCalculator."""

    @patch("receipt_label.utils.ai_usage_tracker.AICostCalculator")
    def test_cost_calculator_integration_openai(self, mock_calculator_class):
        """Test that cost calculator is called correctly for OpenAI."""
        mock_calculator_class.calculate_openai_cost.return_value = 0.005

        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        @tracker.track_openai_completion
        def call_openai(**kwargs):
            return create_mock_openai_response(
                prompt_tokens=1000,
                completion_tokens=500,
                model="gpt-4",
            )

        call_openai(model="gpt-4", is_batch=True)

        # Verify cost calculator was called with correct parameters
        mock_calculator_class.calculate_openai_cost.assert_called_once_with(
            model="gpt-4",
            input_tokens=1000,
            output_tokens=500,
            is_batch=True,
        )

        # Verify calculated cost was stored
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        assert float(item["costUSD"]["N"]) == 0.005

    @patch("receipt_label.utils.ai_usage_tracker.AICostCalculator")
    def test_cost_calculator_integration_anthropic(
        self, mock_calculator_class
    ):
        """Test that cost calculator is called correctly for Anthropic."""
        mock_calculator_class.calculate_anthropic_cost.return_value = 0.015

        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        @tracker.track_anthropic_completion
        def call_claude(**kwargs):
            return create_mock_anthropic_response(
                input_tokens=2000,
                output_tokens=1000,
                model="claude-3.5-sonnet-20240620",
            )

        call_claude(model="claude-3.5-sonnet-20240620")

        # Verify cost calculator was called
        mock_calculator_class.calculate_anthropic_cost.assert_called_once_with(
            model="claude-3.5-sonnet-20240620",
            input_tokens=2000,
            output_tokens=1000,
        )

        # Verify calculated cost was stored
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        assert float(item["costUSD"]["N"]) == 0.015

    @patch("receipt_label.utils.ai_usage_tracker.AICostCalculator")
    def test_cost_calculator_integration_google_places(
        self, mock_calculator_class
    ):
        """Test that cost calculator is called correctly for Google Places."""
        mock_calculator_class.calculate_google_places_cost.return_value = 0.017

        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        @tracker.track_google_places("Place Details")
        def get_place(**kwargs):
            return {"result": {}}

        get_place(place_id="test123")

        # Verify cost calculator was called
        mock_calculator_class.calculate_google_places_cost.assert_called_once_with(
            operation="Place Details",
            api_calls=1,
        )

        # Verify calculated cost was stored
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        assert float(item["costUSD"]["N"]) == 0.017


@pytest.mark.unit
class TestBackendFallback:
    """Test backend switching and fallback behavior."""

    def test_dynamo_failure_continues_to_file(self, capsys):
        """Test that file logging continues when DynamoDB fails."""
        mock_dynamo = Mock()
        mock_dynamo.put_item.side_effect = Exception("DynamoDB unavailable")

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            temp_file = f.name

        try:
            tracker = AIUsageTracker(
                dynamo_client=mock_dynamo,
                table_name="test-table",
                validate_table_environment=False,  # Disable validation for test table
                track_to_dynamo=True,
                track_to_file=True,
                log_file=temp_file,
            )

            metric = AIUsageMetric(
                service="openai",
                model="gpt-3.5-turbo",
                operation="completion",
                timestamp=datetime.now(timezone.utc),
                cost_usd=0.001,
                user_id="test-user",
            )

            tracker._store_metric(metric)

            # DynamoDB should fail
            captured = capsys.readouterr()
            assert "Failed to store metric in DynamoDB" in captured.out

            # But file should still be written
            with open(temp_file, "r") as f:
                line = f.readline()
                data = json.loads(line)
            assert data["service"] == "openai"

        finally:
            os.unlink(temp_file)

    def test_both_backends_failure(self, capsys):
        """Test graceful handling when both backends fail."""
        mock_dynamo = Mock()
        mock_dynamo.put_item.side_effect = Exception("DynamoDB error")

        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
            track_to_file=True,
            log_file="/invalid/path/file.jsonl",
        )

        metric = AIUsageMetric(
            service="openai",
            model="gpt-3.5-turbo",
            operation="completion",
            timestamp=datetime.now(timezone.utc),
            user_id="test-user",
        )

        # Should not raise exception
        tracker._store_metric(metric)

        # Both backends should report failure
        captured = capsys.readouterr()
        assert "Failed to store metric in DynamoDB" in captured.out
        assert "Failed to log metric to file" in captured.out

    def test_no_tracking_backends_enabled(self):
        """Test behavior when no tracking backends are enabled."""
        tracker = AIUsageTracker(
            track_to_dynamo=False,
            track_to_file=False,
        )

        @tracker.track_openai_completion
        def call_openai(**kwargs):
            return create_mock_openai_response()

        # Should work normally without tracking
        response = call_openai(model="gpt-3.5-turbo")
        assert response is not None


@pytest.mark.unit
class TestMetadataHandling:
    """Test custom metadata handling."""

    def test_metadata_preservation_in_decorators(self):
        """Test that decorator metadata is preserved."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        @tracker.track_openai_completion
        def my_custom_function(**kwargs):
            """Custom function docstring."""
            return create_mock_openai_response()

        # Function metadata should be preserved
        assert my_custom_function.__name__ == "my_custom_function"
        assert "Custom function docstring" in my_custom_function.__doc__

    def test_complex_metadata_serialization(self):
        """Test that complex metadata is properly serialized."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        @tracker.track_openai_completion
        def call_with_complex_params(**kwargs):
            return create_mock_openai_response()

        # Call with various parameter types
        call_with_complex_params(
            model="gpt-3.5-turbo",
            temperature=0.7,
            max_tokens=None,
            stop=["\n", "END"],
            presence_penalty=0.5,
            custom_param={"nested": {"value": 123}},
        )

        # Verify metadata was serialized
        call_args = mock_dynamo.put_item.call_args
        item = call_args.kwargs["Item"]
        metadata_map = item["metadata"]["M"]

        assert float(metadata_map["temperature"]["N"]) == 0.7
        # None values are stored as {"NULL": True} in DynamoDB
        max_tokens_value = metadata_map.get("max_tokens")
        assert max_tokens_value is None or max_tokens_value == {"NULL": True}
        assert "function" in metadata_map

    def test_metadata_with_non_serializable_values(self):
        """Test handling of non-JSON-serializable metadata values."""
        mock_dynamo = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo,
            table_name="test-table",
            validate_table_environment=False,  # Disable validation for test table
            track_to_dynamo=True,
        )

        @tracker.track_openai_completion
        def call_with_object(**kwargs):
            return create_mock_openai_response()

        # Create a non-serializable object
        class CustomObject:
            pass

        # This should not crash
        call_with_object(
            model="gpt-3.5-turbo",
            custom_object=CustomObject(),
        )

        # Metric should still be stored
        mock_dynamo.put_item.assert_called_once()
