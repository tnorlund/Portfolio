"""
Tests for AI Usage Tracker environment-based functionality.
"""

import os
import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock
from decimal import Decimal

from receipt_label.utils.ai_usage_tracker import AIUsageTracker
from receipt_label.utils.environment_config import Environment, EnvironmentConfig
from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric


class TestAIUsageTrackerEnvironmentIntegration:
    """Test AIUsageTracker integration with environment configuration."""

    def test_tracker_creation_with_environment_detection(self):
        """Test tracker creation with automatic environment detection."""
        with patch.dict(os.environ, {"ENVIRONMENT": "staging"}, clear=False):
            # When table_name is provided, it's used as-is
            tracker = AIUsageTracker.create_for_environment(
                table_name="AIUsageMetrics"
            )
            
            assert tracker.environment_config.environment == Environment.STAGING
            assert tracker.table_name == "AIUsageMetrics"  # Used as-is
            assert not tracker.environment_config.require_context

    def test_tracker_creation_with_auto_table_naming(self):
        """Test tracker creation with automatic table naming (no table_name provided)."""
        with patch.dict(os.environ, {"ENVIRONMENT": "staging"}, clear=False):
            # When table_name is None, auto-generate with environment suffix
            tracker = AIUsageTracker.create_for_environment()
            
            assert tracker.environment_config.environment == Environment.STAGING
            assert tracker.table_name == "AIUsageMetrics-staging"  # Auto-generated
            assert not tracker.environment_config.require_context

    def test_tracker_creation_with_explicit_environment(self):
        """Test tracker creation with explicitly specified environment."""
        tracker = AIUsageTracker.create_for_environment(
            table_name="AIUsageMetrics",
            environment=Environment.PRODUCTION
        )
        
        assert tracker.environment_config.environment == Environment.PRODUCTION
        assert tracker.table_name == "AIUsageMetrics"  # Used as-is
        assert tracker.environment_config.require_context

    def test_tracker_environment_isolation_validation(self):
        """Test that tracker validates environment isolation on creation."""
        # This should work - staging environment with staging table
        tracker = AIUsageTracker(
            table_name="AIUsageMetrics-staging",
            environment=Environment.STAGING
        )
        assert tracker.table_name == "AIUsageMetrics-staging"

        # This should fail - production environment with staging table
        with pytest.raises(ValueError, match="does not match environment"):
            AIUsageTracker(
                table_name="AIUsageMetrics-staging",
                environment=Environment.PRODUCTION
            )

    def test_production_table_name_no_suffix(self):
        """Test that production environment doesn't add table suffix."""
        tracker = AIUsageTracker(
            environment=Environment.PRODUCTION,
            table_name="AIUsageMetrics"
        )
        assert tracker.table_name == "AIUsageMetrics"

    def test_non_production_table_name_with_auto_generation(self):
        """Test that non-production environments add table suffix when auto-generating."""
        tracker = AIUsageTracker(
            environment=Environment.DEVELOPMENT,
            table_name=None  # Will auto-generate
        )
        assert tracker.table_name == "AIUsageMetrics-development"

    def test_environment_auto_tags_included_in_metadata(self):
        """Test that environment auto-tags are included in metric metadata."""
        with patch.dict(
            os.environ,
            {
                "GITHUB_RUN_ID": "12345",
                "GITHUB_WORKFLOW": "test-workflow",
                "APP_VERSION": "1.0.0",
            },
            clear=False,
        ):
            tracker = AIUsageTracker(
                environment=Environment.CICD,
                table_name="AIUsageMetrics",
                track_to_dynamo=False,
                validate_table_environment=False  # Disable validation for test
            )

            # Mock OpenAI response
            mock_response = Mock()
            mock_response.model = "gpt-3.5-turbo"
            mock_response.usage = Mock()
            mock_response.usage.prompt_tokens = 100
            mock_response.usage.completion_tokens = 50
            mock_response.usage.total_tokens = 150

            @tracker.track_openai_completion
            def mock_openai_call(**kwargs):
                return mock_response

            # Call the tracked function
            result = mock_openai_call(model="gpt-3.5-turbo")

            # Check that environment tags are included
            # We can verify this by checking the _create_base_metadata method
            metadata = tracker._create_base_metadata()
            assert metadata["environment"] == "cicd"
            assert metadata["service"] == "receipt-processing"
            assert metadata["version"] == "1.0.0"
            assert metadata["ci_run_id"] == "12345"
            assert metadata["ci_workflow"] == "test-workflow"

    def test_metric_includes_environment_field(self):
        """Test that created metrics include the environment field."""
        mock_dynamo_client = Mock()
        tracker = AIUsageTracker(
            dynamo_client=mock_dynamo_client,
            environment=Environment.STAGING,
            table_name="AIUsageMetrics",
            validate_table_environment=False  # Disable validation for test
        )

        # Mock OpenAI response
        mock_response = Mock()
        mock_response.model = "gpt-3.5-turbo"
        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 50
        mock_response.usage.total_tokens = 150

        @tracker.track_openai_completion
        def mock_openai_call(**kwargs):
            return mock_response

        # Call the tracked function
        result = mock_openai_call(model="gpt-3.5-turbo")

        # Verify that put_item was called
        mock_dynamo_client.put_item.assert_called_once()
        
        # Get the item that was stored
        call_args = mock_dynamo_client.put_item.call_args
        item = call_args.kwargs["Item"]
        
        # Verify environment field is present
        assert "environment" in item
        assert item["environment"]["S"] == "staging"

    def test_different_environments_use_different_tables(self):
        """Test that different environments use different table names when auto-generating."""
        # Production tracker (auto-generate table name)
        prod_tracker = AIUsageTracker(
            environment=Environment.PRODUCTION,
            table_name=None
        )
        
        # Staging tracker (auto-generate table name)
        staging_tracker = AIUsageTracker(
            environment=Environment.STAGING,
            table_name=None
        )
        
        # Development tracker (auto-generate table name)
        dev_tracker = AIUsageTracker(
            environment=Environment.DEVELOPMENT,
            table_name=None
        )

        # Verify different table names
        assert prod_tracker.table_name == "AIUsageMetrics"
        assert staging_tracker.table_name == "AIUsageMetrics-staging"
        assert dev_tracker.table_name == "AIUsageMetrics-development"

        # Verify they're all different
        table_names = {
            prod_tracker.table_name,
            staging_tracker.table_name,
            dev_tracker.table_name
        }
        assert len(table_names) == 3

    def test_file_logging_includes_environment(self):
        """Test that file logging includes environment information."""
        import tempfile
        import json
        
        with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.jsonl') as f:
            log_file = f.name

        try:
            tracker = AIUsageTracker(
                environment=Environment.DEVELOPMENT,
                table_name="AIUsageMetrics",
                track_to_dynamo=False,
                track_to_file=True,
                log_file=log_file,
                validate_table_environment=False  # Disable validation for test
            )

            # Mock OpenAI response
            mock_response = Mock()
            mock_response.model = "gpt-3.5-turbo"
            mock_response.usage = Mock()
            mock_response.usage.prompt_tokens = 100
            mock_response.usage.completion_tokens = 50
            mock_response.usage.total_tokens = 150

            @tracker.track_openai_completion
            def mock_openai_call(**kwargs):
                return mock_response

            # Call the tracked function
            result = mock_openai_call(model="gpt-3.5-turbo")

            # Read the log file
            with open(log_file, 'r') as f:
                log_entry = json.loads(f.read().strip())

            # Verify environment is logged
            assert log_entry["environment"] == "development"
            assert log_entry["service"] == "openai"
            assert log_entry["model"] == "gpt-3.5-turbo"

        finally:
            # Clean up
            os.unlink(log_file)


class TestAIUsageTrackerEnvironmentErrorHandling:
    """Test error handling in environment-based tracking."""

    def test_invalid_table_environment_combination_raises_error(self):
        """Test that invalid table/environment combinations raise errors."""
        # Production environment should not accept staging table
        with pytest.raises(ValueError, match="does not match environment"):
            AIUsageTracker(
                table_name="AIUsageMetrics-staging",
                environment=Environment.PRODUCTION
            )

        # Staging environment should not accept development table
        with pytest.raises(ValueError, match="does not match environment"):
            AIUsageTracker(
                table_name="AIUsageMetrics-development", 
                environment=Environment.STAGING
            )

    def test_production_require_context_behavior(self):
        """Test that production environment has stricter context requirements."""
        prod_config = EnvironmentConfig(
            environment=Environment.PRODUCTION,
            table_suffix="",
            require_context=True,
            auto_tag={}
        )
        
        dev_config = EnvironmentConfig(
            environment=Environment.DEVELOPMENT,
            table_suffix="-development",
            require_context=False,
            auto_tag={}
        )
        
        assert prod_config.require_context is True
        assert dev_config.require_context is False


class TestMetricEnvironmentIntegration:
    """Test that AIUsageMetric properly handles environment field."""

    def test_metric_creation_with_environment(self):
        """Test creating metrics with environment field."""
        metric = AIUsageMetric(
            service="openai",
            model="gpt-3.5-turbo",
            operation="completion",
            timestamp=datetime.now(timezone.utc),
            environment="production",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001
        )
        
        assert metric.environment == "production"

    def test_metric_dynamodb_serialization_with_environment(self):
        """Test that environment field is properly serialized to DynamoDB."""
        metric = AIUsageMetric(
            service="openai",
            model="gpt-3.5-turbo", 
            operation="completion",
            timestamp=datetime.now(timezone.utc),
            environment="staging",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001
        )
        
        item = metric.to_dynamodb_item()
        assert "environment" in item
        assert item["environment"]["S"] == "staging"

    def test_metric_dynamodb_deserialization_with_environment(self):
        """Test that environment field is properly deserialized from DynamoDB."""
        # Create a DynamoDB item with environment field
        item = {
            "PK": {"S": "AI_USAGE#openai#gpt-3.5-turbo"},
            "SK": {"S": "USAGE#2023-01-01T00:00:00#123"},
            "GSI1PK": {"S": "AI_USAGE#openai"},
            "GSI1SK": {"S": "DATE#2023-01-01"},
            "GSI2PK": {"S": "AI_USAGE_COST"},
            "GSI2SK": {"S": "COST#2023-01-01#openai"},
            "TYPE": {"S": "AIUsageMetric"},
            "service": {"S": "openai"},
            "model": {"S": "gpt-3.5-turbo"},
            "operation": {"S": "completion"},
            "timestamp": {"S": "2023-01-01T00:00:00+00:00"},
            "requestId": {"S": "123"},
            "apiCalls": {"N": "1"},
            "date": {"S": "2023-01-01"},
            "month": {"S": "2023-01"},
            "hour": {"S": "2023-01-01-00"},
            "environment": {"S": "staging"},
            "inputTokens": {"N": "100"},
            "outputTokens": {"N": "50"},
            "costUSD": {"N": "0.001"}
        }
        
        metric = AIUsageMetric.from_dynamodb_item(item)
        assert metric.environment == "staging"

    def test_metric_without_environment_field(self):
        """Test that metrics without environment field work (backward compatibility)."""
        metric = AIUsageMetric(
            service="openai",
            model="gpt-3.5-turbo",
            operation="completion", 
            timestamp=datetime.now(timezone.utc),
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001
        )
        
        assert metric.environment is None
        
        # Should serialize without environment field
        item = metric.to_dynamodb_item()
        assert "environment" not in item


class TestTrackerFactoryMethods:
    """Test factory methods for creating trackers."""

    def test_create_for_environment_with_auto_detection(self):
        """Test create_for_environment with auto-detection."""
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=False):
            tracker = AIUsageTracker.create_for_environment(
                table_name="AIUsageMetrics"
            )
            
            assert tracker.environment_config.environment == Environment.PRODUCTION
            assert tracker.table_name == "AIUsageMetrics"

    def test_create_for_environment_with_explicit_environment(self):
        """Test create_for_environment with explicit environment."""
        tracker = AIUsageTracker.create_for_environment(
            table_name=None,  # Auto-generate
            environment=Environment.CICD
        )
        
        assert tracker.environment_config.environment == Environment.CICD
        assert tracker.table_name == "AIUsageMetrics-cicd"

    def test_create_for_environment_preserves_other_options(self):
        """Test that create_for_environment preserves other configuration options."""
        mock_client = Mock()
        
        tracker = AIUsageTracker.create_for_environment(
            dynamo_client=mock_client,
            table_name="CustomTable",  # Will be used as-is
            user_id="test-user",
            track_to_dynamo=True,
            track_to_file=True,
            environment=Environment.STAGING
        )
        
        assert tracker.dynamo_client == mock_client
        assert tracker.table_name == "CustomTable"  # Used as-is, no suffix
        assert tracker.user_id == "test-user"
        assert tracker.track_to_dynamo is True
        assert tracker.track_to_file is True