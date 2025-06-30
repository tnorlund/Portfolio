"""
Integration between AIUsageTracker and cost monitoring system.

This module provides seamless integration to automatically check
budgets and send alerts when AI usage is tracked.
"""

import asyncio
import logging
from typing import Optional

from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric

from ..ai_usage_tracker import AIUsageTracker
from .alert_manager import AlertChannel, AlertManager
from .budget_manager import BudgetManager
from .cost_monitor import CostMonitor

logger = logging.getLogger(__name__)


class CostAwareAIUsageTracker(AIUsageTracker):
    """
    Extended AIUsageTracker with automatic cost monitoring.

    This class extends the base AIUsageTracker to automatically:
    - Check budget thresholds on each tracked usage
    - Send alerts when thresholds are crossed
    - Update budget tracking in real-time
    """

    def __init__(
        self,
        *args,
        cost_monitor: Optional[CostMonitor] = None,
        budget_manager: Optional[BudgetManager] = None,
        alert_manager: Optional[AlertManager] = None,
        enable_cost_monitoring: bool = True,
        **kwargs,
    ):
        """
        Initialize cost-aware tracker.

        Args:
            *args: Arguments for parent AIUsageTracker
            cost_monitor: Optional CostMonitor instance
            budget_manager: Optional BudgetManager instance
            alert_manager: Optional AlertManager instance
            enable_cost_monitoring: Whether to enable cost monitoring
            **kwargs: Additional arguments for parent
        """
        super().__init__(*args, **kwargs)

        self.cost_monitor = cost_monitor
        self.budget_manager = budget_manager
        self.alert_manager = alert_manager
        self.enable_cost_monitoring = enable_cost_monitoring and bool(
            cost_monitor
        )

    def _store_metric(self, metric: AIUsageMetric) -> None:
        """Override to add cost monitoring checks."""
        # Store metric as usual
        super()._store_metric(metric)

        # Check budgets if enabled
        if self.enable_cost_monitoring and self.cost_monitor:
            try:
                self._check_budgets(metric)
            except Exception as e:
                logger.error(f"Error checking budgets: {e}")
                # Don't fail the tracking if budget check fails

    def _check_budgets(self, metric: AIUsageMetric) -> None:
        """Check relevant budgets for the metric."""
        # Check user budget
        if metric.user_id:
            self._check_budget_for_scope(
                metric, f"user:{metric.user_id}", "User budget"
            )

        # Check service budget
        self._check_budget_for_scope(
            metric, f"service:{metric.service}", "Service budget"
        )

        # Check global budget
        self._check_budget_for_scope(metric, "global:all", "Global budget")

        # Check job budget if applicable
        if metric.job_id:
            self._check_budget_for_scope(
                metric, f"job:{metric.job_id}", "Job budget"
            )

    def _check_budget_for_scope(
        self,
        metric: AIUsageMetric,
        scope: str,
        budget_name: str,
    ) -> None:
        """Check budget for a specific scope."""
        if not self.budget_manager:
            return

        # Get active budget
        budget = self.budget_manager.get_active_budget(scope)
        if not budget:
            return

        # Check threshold
        alert = self.cost_monitor.check_budget_threshold(
            current_usage=metric,
            budget_limit=budget.amount,
            scope=scope,
            period=budget.period.value,
        )

        if alert and self.alert_manager:
            # Send alert asynchronously
            asyncio.create_task(self._send_alert_async(alert, budget_name))

    async def _send_alert_async(self, alert, budget_name: str) -> None:
        """Send alert asynchronously to avoid blocking."""
        try:
            logger.info(f"Sending alert for {budget_name}: {alert.message}")
            await self.alert_manager.send_alert(alert)
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")


def create_cost_monitored_tracker(
    dynamo_client,
    alert_channels: Optional[list] = None,
    enable_email_alerts: bool = False,
    enable_slack_alerts: bool = False,
    email_addresses: Optional[list] = None,
    slack_webhook_url: Optional[str] = None,
    **tracker_kwargs,
) -> CostAwareAIUsageTracker:
    """
    Factory function to create a cost-monitored AI usage tracker.

    Args:
        dynamo_client: DynamoDB client
        alert_channels: List of AlertChannel configurations
        enable_email_alerts: Enable email alerts
        enable_slack_alerts: Enable Slack alerts
        email_addresses: Email addresses for alerts
        slack_webhook_url: Slack webhook URL
        **tracker_kwargs: Additional arguments for AIUsageTracker

    Returns:
        Configured CostAwareAIUsageTracker instance
    """
    # Create monitoring components
    cost_monitor = CostMonitor(dynamo_client)
    budget_manager = BudgetManager(dynamo_client)

    # Configure alert channels
    channels = alert_channels or []

    if enable_email_alerts and email_addresses:
        for email in email_addresses:
            channels.append(
                AlertChannel(
                    channel_type="email",
                    destination=email,
                    enabled=True,
                )
            )

    if enable_slack_alerts and slack_webhook_url:
        channels.append(
            AlertChannel(
                channel_type="slack",
                destination=slack_webhook_url,
                enabled=True,
            )
        )

    alert_manager = AlertManager(channels) if channels else None

    # Create tracker
    return CostAwareAIUsageTracker(
        dynamo_client=dynamo_client,
        cost_monitor=cost_monitor,
        budget_manager=budget_manager,
        alert_manager=alert_manager,
        enable_cost_monitoring=True,
        **tracker_kwargs,
    )
