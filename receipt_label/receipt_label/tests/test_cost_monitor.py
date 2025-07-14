"""
Unit tests for the CostMonitor class.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from receipt_label.utils.cost_monitoring import CostMonitor, ThresholdAlert
from receipt_label.utils.cost_monitoring.cost_monitor import ThresholdLevel

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric


class TestCostMonitor:
    """Tests for CostMonitor functionality."""

    @pytest.fixture
    def mock_dynamo_client(self, dynamodb_table_and_s3_bucket):
        """Create a DynamoDB client with mocked AWS resources."""
        table_name, _ = dynamodb_table_and_s3_bucket
        return DynamoClient(table_name=table_name)

    @pytest.fixture
    def cost_monitor(self, mock_dynamo_client):
        """Create a CostMonitor instance."""
        return CostMonitor(
            dynamo_client=mock_dynamo_client,
            alert_cooldown_minutes=60,
        )

    @pytest.fixture
    def sample_usage_metric(self):
        """Create a sample AIUsageMetric."""
        return AIUsageMetric(
            service="openai",
            model="gpt-3.5-turbo",
            operation="completion",
            timestamp=datetime.now(timezone.utc),
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            cost_usd=Decimal("0.50"),
            user_id="test-user",
        )

    def test_check_budget_threshold_no_alert(
        self, cost_monitor, sample_usage_metric
    ):
        """Test budget check when no threshold is crossed."""
        # Mock current spend at 30%
        with patch.object(
            cost_monitor, "_get_period_spend", return_value=Decimal("30.00")
        ):
            alert = cost_monitor.check_budget_threshold(
                current_usage=sample_usage_metric,
                budget_limit=Decimal("100.00"),
                scope="user:test-user",
                period="daily",
            )

            assert alert is None

    def test_check_budget_threshold_warning(
        self, cost_monitor, sample_usage_metric
    ):
        """Test budget check when warning threshold is crossed."""
        # Mock current spend at 79.5% (will be 80% with new usage)
        with patch.object(
            cost_monitor, "_get_period_spend", return_value=Decimal("79.50")
        ):
            alert = cost_monitor.check_budget_threshold(
                current_usage=sample_usage_metric,
                budget_limit=Decimal("100.00"),
                scope="user:test-user",
                period="daily",
            )

            assert alert is not None
            assert alert.level == ThresholdLevel.WARNING
            assert alert.threshold_percent == 80
            assert alert.current_spend == Decimal("80.00")
            assert alert.budget_limit == Decimal("100.00")
            assert "WARNING" in alert.message

    def test_check_budget_threshold_exceeded(
        self, cost_monitor, sample_usage_metric
    ):
        """Test budget check when budget is exceeded."""
        # Mock current spend at 99.5% (will be 100% with new usage)
        with patch.object(
            cost_monitor, "_get_period_spend", return_value=Decimal("99.50")
        ):
            alert = cost_monitor.check_budget_threshold(
                current_usage=sample_usage_metric,
                budget_limit=Decimal("100.00"),
                scope="service:openai",
                period="monthly",
            )

            assert alert is not None
            assert alert.level == ThresholdLevel.EXCEEDED
            assert alert.threshold_percent == 100
            assert alert.current_spend == Decimal("100.00")
            assert "BUDGET EXCEEDED" in alert.message

    def test_alert_cooldown(self, cost_monitor, sample_usage_metric):
        """Test that alerts respect cooldown period."""
        with patch.object(
            cost_monitor, "_get_period_spend", return_value=Decimal("80.00")
        ):
            # First alert should be sent
            alert1 = cost_monitor.check_budget_threshold(
                current_usage=sample_usage_metric,
                budget_limit=Decimal("100.00"),
                scope="user:test-user",
                period="daily",
            )
            assert alert1 is not None

            # Second alert within cooldown should be suppressed
            alert2 = cost_monitor.check_budget_threshold(
                current_usage=sample_usage_metric,
                budget_limit=Decimal("100.00"),
                scope="user:test-user",
                period="daily",
            )
            assert alert2 is None

    def test_get_cost_breakdown(self, cost_monitor, mock_dynamo_client):
        """Test cost breakdown by service."""
        # Mock query results
        mock_metrics = [
            MagicMock(service="openai", cost_usd=Decimal("10.00")),
            MagicMock(service="openai", cost_usd=Decimal("15.00")),
            MagicMock(service="anthropic", cost_usd=Decimal("20.00")),
            MagicMock(service="google_places", cost_usd=Decimal("5.00")),
        ]

        with patch.object(
            cost_monitor, "_query_metrics", return_value=mock_metrics
        ):
            breakdown = cost_monitor.get_cost_breakdown(
                scope="global:all",
                period="daily",
            )

            assert breakdown["openai"] == Decimal("25.00")
            assert breakdown["anthropic"] == Decimal("20.00")
            assert breakdown["google_places"] == Decimal("5.00")

    def test_parse_scope_valid(self, cost_monitor):
        """Test parsing valid scope strings."""
        assert cost_monitor._parse_scope("user:123") == ("user", "123")
        assert cost_monitor._parse_scope("service:openai") == (
            "service",
            "openai",
        )
        assert cost_monitor._parse_scope("global:all") == ("global", "all")

    def test_parse_scope_invalid(self, cost_monitor):
        """Test parsing invalid scope strings."""
        with pytest.raises(ValueError, match="Invalid scope format"):
            cost_monitor._parse_scope("invalid")

        with pytest.raises(ValueError, match="Invalid scope type"):
            cost_monitor._parse_scope("invalid:type")

    def test_get_period_dates_daily(self, cost_monitor):
        """Test period date calculation for daily."""
        with patch(
            "receipt_label.utils.cost_monitoring.cost_monitor.datetime"
        ) as mock_dt:
            mock_now = datetime(2024, 1, 15, 14, 30, 0, tzinfo=timezone.utc)
            mock_dt.now.return_value = mock_now

            start, end = cost_monitor._get_period_dates("daily")

            assert start == "2024-01-15"
            assert end == "2024-01-15"

    def test_get_period_dates_weekly(self, cost_monitor):
        """Test period date calculation for weekly."""
        with patch(
            "receipt_label.utils.cost_monitoring.cost_monitor.datetime"
        ) as mock_dt:
            # Wednesday
            mock_now = datetime(2024, 1, 17, 14, 30, 0, tzinfo=timezone.utc)
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *args, **kwargs: datetime(
                *args, **kwargs
            )

            start, end = cost_monitor._get_period_dates("weekly")

            # Should start on Monday
            assert start == "2024-01-15"
            assert end == "2024-01-17"

    def test_get_period_dates_monthly(self, cost_monitor):
        """Test period date calculation for monthly."""
        with patch(
            "receipt_label.utils.cost_monitoring.cost_monitor.datetime"
        ) as mock_dt:
            mock_now = datetime(2024, 1, 15, 14, 30, 0, tzinfo=timezone.utc)
            mock_dt.now.return_value = mock_now

            start, end = cost_monitor._get_period_dates("monthly")

            assert start == "2024-01-01"
            assert end == "2024-01-15"

    def test_query_metrics_by_service(self, cost_monitor, mock_dynamo_client):
        """Test querying metrics by service."""
        expected_metrics = [MagicMock(), MagicMock()]
        AIUsageMetric.query_by_service_date = MagicMock(
            return_value=expected_metrics
        )

        metrics = cost_monitor._query_metrics(
            "service",
            "openai",
            "2024-01-01",
            "2024-01-31",
        )

        assert metrics == expected_metrics
        AIUsageMetric.query_by_service_date.assert_called_once_with(
            mock_dynamo_client,
            service="openai",
            start_date="2024-01-01",
            end_date="2024-01-31",
        )

    def test_custom_thresholds(self, mock_dynamo_client):
        """Test custom alert thresholds."""
        custom_thresholds = [
            (25, ThresholdLevel.INFO),
            (75, ThresholdLevel.WARNING),
            (90, ThresholdLevel.CRITICAL),
            (100, ThresholdLevel.EXCEEDED),
        ]

        monitor = CostMonitor(
            dynamo_client=mock_dynamo_client,
            alert_thresholds=custom_thresholds,
        )

        assert monitor.alert_thresholds == custom_thresholds

    def test_threshold_alert_to_dict(self):
        """Test ThresholdAlert serialization."""
        alert = ThresholdAlert(
            level=ThresholdLevel.WARNING,
            threshold_percent=80,
            current_spend=Decimal("80.00"),
            budget_limit=Decimal("100.00"),
            scope="user:test",
            period="daily",
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            message="Test alert",
            metadata={"test": "data"},
        )

        alert_dict = alert.to_dict()

        assert alert_dict["level"] == "warning"
        assert alert_dict["threshold_percent"] == 80
        assert alert_dict["current_spend"] == "80.00"
        assert alert_dict["budget_limit"] == "100.00"
        assert alert_dict["scope"] == "user:test"
        assert alert_dict["period"] == "daily"
        assert alert_dict["timestamp"] == "2024-01-01T12:00:00+00:00"
        assert alert_dict["message"] == "Test alert"
        assert alert_dict["metadata"] == {"test": "data"}

    def test_check_budget_threshold_zero_budget(self, mock_dynamo_client):
        """Test budget check with zero budget limit."""
        monitor = CostMonitor(mock_dynamo_client)

        # Mock current spend
        with patch.object(
            monitor, "_get_period_spend", return_value=Decimal("0")
        ):
            # Create usage metric
            usage = AIUsageMetric(
                service="openai",
                model="gpt-3.5-turbo",
                operation="completion",
                timestamp=datetime.now(timezone.utc),
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                cost_usd=Decimal("0.50"),
            )

            # Test with zero budget - any spend should trigger exceeded
            alert = monitor.check_budget_threshold(
                current_usage=usage,
                budget_limit=Decimal("0"),
                scope="user:test",
                period="daily",
            )

            assert alert is not None
            assert alert.level == ThresholdLevel.EXCEEDED
            assert alert.threshold_percent == 100
            assert alert.current_spend == Decimal("0.50")

    def test_check_budget_threshold_zero_budget_no_spend(
        self, mock_dynamo_client
    ):
        """Test budget check with zero budget and no spend."""
        monitor = CostMonitor(mock_dynamo_client)

        # Mock current spend
        with patch.object(
            monitor, "_get_period_spend", return_value=Decimal("0")
        ):
            # Create usage metric with no cost
            usage = AIUsageMetric(
                service="openai",
                model="gpt-3.5-turbo",
                operation="completion",
                timestamp=datetime.now(timezone.utc),
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                cost_usd=Decimal("0"),
            )

            # Test with zero budget and zero spend - should not trigger alert
            alert = monitor.check_budget_threshold(
                current_usage=usage,
                budget_limit=Decimal("0"),
                scope="user:test",
                period="daily",
            )

            assert alert is None

    def test_query_metrics_job_scope(self, mock_dynamo_client):
        """Test querying metrics for job scope using GSI3."""
        from datetime import datetime

        # Create and insert a test metric
        test_metric = AIUsageMetric(
            service="openai",
            model="gpt-3.5-turbo",
            operation="completion",
            timestamp=datetime.fromisoformat("2024-01-01T12:00:00+00:00"),
            job_id="test-job-123",
            cost_usd=1.50,
            api_calls=1,
        )

        # Insert the metric into the mocked DynamoDB
        mock_dynamo_client.put_ai_usage_metric(test_metric)

        # Create monitor and query
        monitor = CostMonitor(mock_dynamo_client)

        # Test job scope query
        metrics = monitor._query_metrics(
            scope_type="job",
            scope_value="test-job-123",
            start_date="2024-01-01",
            end_date="2024-01-01",
        )

        assert len(metrics) == 1
        assert metrics[0].job_id == "test-job-123"
        assert metrics[0].service == "openai"
        assert metrics[0].cost_usd == Decimal("1.50")

    def test_query_metrics_environment_scope(self, mock_dynamo_client):
        """Test querying metrics for environment scope using scan."""
        from datetime import datetime

        # Create and insert a test metric
        test_metric = AIUsageMetric(
            service="anthropic",
            model="claude-3-opus",
            operation="completion",
            timestamp=datetime.fromisoformat("2024-01-01T12:00:00+00:00"),
            environment="production",
            cost_usd=2.25,
            api_calls=1,
        )

        # Insert the metric into the mocked DynamoDB
        mock_dynamo_client.put_ai_usage_metric(test_metric)

        # Create monitor and query
        monitor = CostMonitor(mock_dynamo_client)

        # Test environment scope query
        metrics = monitor._query_metrics(
            scope_type="environment",
            scope_value="production",
            start_date="2024-01-01",
            end_date="2024-01-01",
        )

        assert len(metrics) == 1
        assert metrics[0].environment == "production"
        assert metrics[0].service == "anthropic"
        assert metrics[0].cost_usd == Decimal("2.25")

    def test_get_period_spend_with_zero_costs(
        self, cost_monitor, mock_dynamo_client
    ):
        """Test that zero-cost metrics are correctly included in spend calculation."""
        from datetime import datetime

        # Create metrics with various cost values including zeros
        metrics = [
            AIUsageMetric(
                service="openai",
                model="gpt-3.5-turbo",
                operation="completion",
                timestamp=datetime.now(timezone.utc),
                cost_usd=1.50,  # Normal cost
            ),
            AIUsageMetric(
                service="openai",
                model="gpt-3.5-turbo",
                operation="cached",
                timestamp=datetime.now(timezone.utc),
                cost_usd=0.0,  # Zero cost (cached response)
            ),
            AIUsageMetric(
                service="anthropic",
                model="claude-3",
                operation="completion",
                timestamp=datetime.now(timezone.utc),
                cost_usd=2.25,  # Normal cost
            ),
            AIUsageMetric(
                service="openai",
                model="gpt-3.5-turbo",
                operation="cached",
                timestamp=datetime.now(timezone.utc),
                cost_usd=0.0,  # Another zero cost
            ),
            AIUsageMetric(
                service="openai",
                model="gpt-3.5-turbo",
                operation="error",
                timestamp=datetime.now(timezone.utc),
                cost_usd=None,  # No cost (error case)
            ),
        ]

        # Test internal _get_period_spend method
        with patch.object(
            cost_monitor, "_query_metrics", return_value=metrics
        ):
            total_spend = cost_monitor._get_period_spend(
                scope="global:all", period="daily"
            )

            # Should include: 1.50 + 0.0 + 2.25 + 0.0 = 3.75
            # Should NOT include the None value
            assert total_spend == Decimal("3.75")

        # Test that it correctly handles all None values
        none_metrics = [
            AIUsageMetric(
                service="test",
                model="test",
                operation="test",
                timestamp=datetime.now(timezone.utc),
                cost_usd=None,
            )
            for _ in range(3)
        ]

        with patch.object(
            cost_monitor, "_query_metrics", return_value=none_metrics
        ):
            total_spend = cost_monitor._get_period_spend(
                scope="global:all", period="daily"
            )
            assert total_spend == Decimal("0")
