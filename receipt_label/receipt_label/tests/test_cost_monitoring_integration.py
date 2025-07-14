"""
Integration tests for cost monitoring system.
"""

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from receipt_label.utils.cost_monitoring import (
    AlertChannel,
    AlertManager,
    Budget,
    BudgetManager,
    BudgetPeriod,
    CostAnalytics,
    CostAwareAIUsageTracker,
    CostMonitor,
    ThresholdLevel,
    TrendDirection,
    create_cost_monitored_tracker,
)

from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric


@pytest.mark.asyncio
class TestCostMonitoringIntegration:
    """Integration tests for cost monitoring components."""

    @pytest.fixture
    def mock_dynamo_client(self):
        """Create mock DynamoDB client."""
        client = MagicMock()
        client.table_name = "test-table"
        # Mock the _client attribute that BudgetManager uses
        client._client = MagicMock()
        return client

    @pytest.fixture
    def sample_metric(self):
        """Create sample usage metric."""
        return AIUsageMetric(
            service="openai",
            model="gpt-3.5-turbo",
            operation="completion",
            timestamp=datetime.now(timezone.utc),
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            cost_usd=Decimal("1.50"),
            user_id="test-user",
            job_id="test-job",
        )

    async def test_cost_aware_tracker_integration(
        self,
        mock_dynamo_client,
        sample_metric,
    ):
        """Test CostAwareAIUsageTracker with all components."""
        # Create components
        cost_monitor = CostMonitor(mock_dynamo_client)
        budget_manager = BudgetManager(mock_dynamo_client)

        # Create budget
        budget = Budget(
            budget_id="BUDGET#user:test-user#daily#123",
            scope="user:test-user",
            amount=Decimal("10.00"),
            period=BudgetPeriod.DAILY,
            created_at=datetime.now(timezone.utc),
            effective_from=datetime.now(timezone.utc),
            alert_thresholds=[50, 80, 95, 100],
            is_active=True,
        )

        # Mock budget manager to return our budget
        budget_manager.get_active_budget = MagicMock(return_value=budget)

        # Mock current spend at 80% (8.00)
        with patch.object(
            cost_monitor, "_get_period_spend", return_value=Decimal("8.00")
        ):
            # Create alert manager with mock channel
            alert_channel = AlertChannel(
                channel_type="email",
                destination="test@example.com",
                enabled=True,
            )
            alert_manager = AlertManager([alert_channel])

            # Mock alert sending
            alert_manager.send_alert = AsyncMock(
                return_value={"email:test@example.com": True}
            )

            # Create cost-aware tracker
            tracker = CostAwareAIUsageTracker(
                dynamo_client=mock_dynamo_client,
                cost_monitor=cost_monitor,
                budget_manager=budget_manager,
                alert_manager=alert_manager,
                enable_cost_monitoring=True,
                track_to_dynamo=True,
            )

            # Track the metric (this should trigger budget check)
            tracker._store_metric(sample_metric)

            # Allow async alert to process
            await asyncio.sleep(0.1)

            # Verify alerts were sent (95% threshold crossed for all scopes)
            assert (
                alert_manager.send_alert.call_count == 4
            )  # user, service, global, job

            # Check first alert (user budget)
            alert = alert_manager.send_alert.call_args_list[0][0][0]
            assert alert.level == ThresholdLevel.CRITICAL  # 95% threshold
            assert alert.threshold_percent == 95
            assert alert.current_spend == Decimal("9.50")  # 8.00 + 1.50
            assert alert.scope == "user:test-user"

    async def test_create_cost_monitored_tracker(self, mock_dynamo_client):
        """Test factory function for creating monitored tracker."""
        tracker = create_cost_monitored_tracker(
            dynamo_client=mock_dynamo_client,
            enable_email_alerts=True,
            email_addresses=["admin@example.com", "alerts@example.com"],
            enable_slack_alerts=True,
            slack_webhook_url="https://hooks.slack.com/test",
            table_name="test-table",
            user_id="test-user",
            validate_table_environment=False,  # Disable validation for test table
        )

        assert isinstance(tracker, CostAwareAIUsageTracker)
        assert tracker.cost_monitor is not None
        assert tracker.budget_manager is not None
        assert tracker.alert_manager is not None

        # Check alert channels
        channels = tracker.alert_manager.channels
        assert len(channels) == 3  # 2 email + 1 slack

        email_channels = [c for c in channels if c.channel_type == "email"]
        assert len(email_channels) == 2
        assert set(c.destination for c in email_channels) == {
            "admin@example.com",
            "alerts@example.com",
        }

        slack_channels = [c for c in channels if c.channel_type == "slack"]
        assert len(slack_channels) == 1
        assert slack_channels[0].destination == "https://hooks.slack.com/test"

    def test_cost_analytics_integration(self, mock_dynamo_client):
        """Test cost analytics with real data flow."""
        analytics = CostAnalytics(mock_dynamo_client)

        # Mock metrics for trend analysis
        mock_metrics = []
        base_cost = Decimal("10.00")

        for day in range(30):
            # Create increasing trend
            daily_cost = base_cost + (Decimal(str(day)) * Decimal("0.5"))

            # Create multiple metrics per day
            for i in range(5):
                metric = MagicMock()
                metric.service = "openai"
                metric.cost_usd = daily_cost / 5
                metric.timestamp = datetime.now(timezone.utc).replace(
                    day=day + 1
                )
                mock_metrics.append(metric)

        # Mock query to return our metrics
        with patch.object(
            analytics, "_query_metrics_by_scope", return_value=mock_metrics
        ):
            # Analyze trends
            trend = analytics.analyze_trends(
                scope="service:openai",
                period="daily",
                lookback_days=30,
                forecast_days=7,
            )

            assert (
                trend.direction == TrendDirection.INCREASING
            )  # Should detect increasing trend
            assert trend.change_percent > 0
            assert trend.forecast_value is not None
            # Note: forecast_value might be lower if analyzing daily averages
            # The important part is that we detected an increasing trend

    def test_budget_lifecycle_integration(self, mock_dynamo_client):
        """Test complete budget lifecycle."""
        budget_manager = BudgetManager(mock_dynamo_client)

        # Create initial budget
        budget = budget_manager.create_budget(
            scope="service:openai",
            amount=Decimal("1000.00"),
            period=BudgetPeriod.MONTHLY,
            rollover_enabled=True,
            metadata={"team": "engineering"},
        )

        assert budget.scope == "service:openai"
        assert budget.amount == Decimal("1000.00")

        # Update budget
        mock_dynamo_client._client.get_item.return_value = {
            "Item": {"budget_data": {"S": json.dumps(budget.to_dict())}}
        }

        updated_budget = budget_manager.update_budget(
            budget_id=budget.budget_id,
            amount=Decimal("1500.00"),
            metadata_updates={"approved_by": "finance"},
        )

        assert updated_budget.amount == Decimal("1500.00")
        assert updated_budget.metadata["team"] == "engineering"
        assert updated_budget.metadata["approved_by"] == "finance"

        # Process rollover
        new_budget = budget_manager.process_period_rollover(updated_budget)

        assert new_budget is not None
        assert new_budget.scope == updated_budget.scope
        assert (
            new_budget.metadata["rolled_over_from"] == updated_budget.budget_id
        )

    def test_anomaly_detection_integration(self, mock_dynamo_client):
        """Test anomaly detection with realistic data."""
        analytics = CostAnalytics(mock_dynamo_client)

        # Create normal pattern with anomalies
        mock_metrics = []

        for day in range(30):
            # Normal cost around $50/day
            if day in [10, 20]:  # Anomaly days
                daily_metrics = 50  # 10x normal
                cost_per_metric = Decimal("10.00")
            else:
                daily_metrics = 5
                cost_per_metric = Decimal("10.00")

            for i in range(daily_metrics):
                metric = MagicMock()
                metric.service = "openai"
                metric.cost_usd = cost_per_metric
                metric.timestamp = datetime.now(timezone.utc).replace(
                    day=day + 1
                )
                mock_metrics.append(metric)

        with patch.object(analytics, "_get_service_daily_costs") as mock_costs:
            # Prepare daily costs data
            daily_costs = {}
            for day in range(30):
                date = datetime.now(timezone.utc).replace(day=day + 1)
                if day in [10, 20]:
                    daily_costs[date] = Decimal("500.00")
                else:
                    daily_costs[date] = Decimal("50.00")

            mock_costs.return_value = {"openai": sorted(daily_costs.items())}

            # Detect anomalies
            anomalies = analytics.detect_anomalies(
                scope="global:all",
                sensitivity=2.0,
                lookback_days=30,
            )

            assert len(anomalies) >= 2  # Should detect at least 2 anomalies

            for anomaly in anomalies:
                assert anomaly.deviation_percent > 100  # Significant deviation
                assert anomaly.severity in ["medium", "high"]
                assert len(anomaly.possible_causes) > 0

    def test_optimization_recommendations(self, mock_dynamo_client):
        """Test optimization recommendation generation."""
        analytics = CostAnalytics(mock_dynamo_client)

        # Create metrics showing optimization opportunities
        mock_metrics = []

        # Heavy GPT-4 usage
        for i in range(100):
            metric = MagicMock()
            metric.service = "openai"
            metric.model = "gpt-4"
            metric.cost_usd = Decimal("5.00")
            metric.timestamp = datetime.now(timezone.utc)
            metric.total_tokens = 1000
            mock_metrics.append(metric)

        # Light GPT-3.5 usage
        for i in range(20):
            metric = MagicMock()
            metric.service = "openai"
            metric.model = "gpt-3.5-turbo"
            metric.cost_usd = Decimal("0.50")
            metric.timestamp = datetime.now(timezone.utc)
            metric.total_tokens = 1000
            mock_metrics.append(metric)

        with patch.object(
            analytics, "_get_detailed_metrics", return_value=mock_metrics
        ):
            recommendations = analytics.generate_optimization_recommendations(
                scope="global:all",
                lookback_days=30,
            )

            assert len(recommendations) > 0

            # Should recommend model optimization
            model_rec = next(
                (
                    r
                    for r in recommendations
                    if r.category == "model_selection"
                ),
                None,
            )
            assert model_rec is not None
            assert model_rec.potential_savings > Decimal("0")
            assert "GPT-3.5" in " ".join(model_rec.specific_actions)

    async def test_alert_delivery_integration(self):
        """Test alert delivery through multiple channels."""
        # Create alert
        alert = MagicMock()
        alert.level = ThresholdLevel.WARNING
        alert.threshold_percent = 80
        alert.current_spend = Decimal("80.00")
        alert.budget_limit = Decimal("100.00")
        alert.scope = "user:test"
        alert.period = "daily"
        alert.timestamp = datetime.now(timezone.utc)
        alert.message = "Budget warning"
        alert.to_dict.return_value = {
            "level": "warning",
            "threshold_percent": 80,
            "current_spend": "80.00",
            "budget_limit": "100.00",
            "scope": "user:test",
            "period": "daily",
            "timestamp": alert.timestamp.isoformat(),
            "message": "Budget warning",
        }

        # Create channels
        channels = [
            AlertChannel("email", "test@example.com", enabled=True),
            AlertChannel(
                "slack", "https://hooks.slack.com/test", enabled=True
            ),
        ]

        # Mock SES client
        mock_ses = MagicMock()
        mock_ses.send_email.return_value = {"MessageId": "test-123"}

        # Create alert manager
        alert_manager = AlertManager(channels, ses_client=mock_ses)

        # Mock Slack sender
        with patch.object(
            alert_manager.senders["slack"], "send", new_callable=AsyncMock
        ) as mock_slack:
            mock_slack.return_value = True

            # Send alert
            results = await alert_manager.send_alert(alert)

            # Verify both channels were used
            assert len(results) == 2
            assert results.get("email:test@example.com") is True
            assert results.get("slack:https://hooks.slack.com/test") is True

            # Verify email was sent
            mock_ses.send_email.assert_called_once()

            # Verify Slack was called
            mock_slack.assert_called_once()

    def test_alert_channel_string_level_conversion(self):
        """Test that AlertChannel correctly converts string levels to enums."""
        # Test valid string levels
        channel1 = AlertChannel(
            channel_type="email",
            destination="test@example.com",
            min_level="WARNING",  # String instead of enum
        )
        assert channel1.min_level == ThresholdLevel.WARNING

        channel2 = AlertChannel(
            channel_type="slack",
            destination="webhook_url",
            min_level="critical",  # Lowercase string
        )
        assert channel2.min_level == ThresholdLevel.CRITICAL

        # Test invalid string level (should default to INFO)
        with patch(
            "receipt_label.utils.cost_monitoring.alert_manager.logger"
        ) as mock_logger:
            channel3 = AlertChannel(
                channel_type="email",
                destination="test@example.com",
                min_level="INVALID_LEVEL",
            )
            assert channel3.min_level == ThresholdLevel.INFO
            mock_logger.warning.assert_called_once()

        # Test that enum values still work
        channel4 = AlertChannel(
            channel_type="email",
            destination="test@example.com",
            min_level=ThresholdLevel.EXCEEDED,
        )
        assert channel4.min_level == ThresholdLevel.EXCEEDED
