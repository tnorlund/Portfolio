"""
Configuration for cost monitoring system.

This module provides configuration classes and utilities for setting up
the cost monitoring system with environment variables and defaults.
"""

import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

from .alert_manager import AlertChannel
from .budget_manager import BudgetPeriod
from .cost_monitor import ThresholdLevel


@dataclass
class CostMonitoringConfig:
    """Configuration for the cost monitoring system."""

    # Budget defaults
    default_daily_budget: Decimal = Decimal("100.00")
    default_weekly_budget: Decimal = Decimal("500.00")
    default_monthly_budget: Decimal = Decimal("2000.00")

    # Alert thresholds (percentages)
    alert_thresholds: List[tuple] = field(
        default_factory=lambda: [
            (50, ThresholdLevel.INFO),
            (80, ThresholdLevel.WARNING),
            (95, ThresholdLevel.CRITICAL),
            (100, ThresholdLevel.EXCEEDED),
        ]
    )

    # Alert configuration
    alert_cooldown_minutes: int = 60
    alert_rate_limit_minutes: int = 60

    # Analytics configuration
    anomaly_sensitivity: float = 2.0
    trend_lookback_days: int = 30
    forecast_days: int = 7

    # Channel configuration
    enable_email_alerts: bool = False
    email_recipients: List[str] = field(default_factory=list)
    email_from_address: str = "noreply@example.com"

    enable_slack_alerts: bool = False
    slack_webhook_url: Optional[str] = None

    enable_sns_alerts: bool = False
    sns_topic_arn: Optional[str] = None

    # Storage configuration
    dynamo_table_name: Optional[str] = None
    enable_cost_monitoring: bool = True

    @classmethod
    def from_env(cls) -> "CostMonitoringConfig":
        """Create configuration from environment variables."""
        config = cls()

        # Budget defaults
        if daily := os.environ.get("DEFAULT_DAILY_BUDGET"):
            config.default_daily_budget = Decimal(daily)
        if weekly := os.environ.get("DEFAULT_WEEKLY_BUDGET"):
            config.default_weekly_budget = Decimal(weekly)
        if monthly := os.environ.get("DEFAULT_MONTHLY_BUDGET"):
            config.default_monthly_budget = Decimal(monthly)

        # Alert configuration
        if cooldown := os.environ.get("ALERT_COOLDOWN_MINUTES"):
            config.alert_cooldown_minutes = int(cooldown)
        if rate_limit := os.environ.get("ALERT_RATE_LIMIT_MINUTES"):
            config.alert_rate_limit_minutes = int(rate_limit)

        # Analytics
        if sensitivity := os.environ.get("ANOMALY_SENSITIVITY"):
            config.anomaly_sensitivity = float(sensitivity)
        if lookback := os.environ.get("TREND_LOOKBACK_DAYS"):
            config.trend_lookback_days = int(lookback)

        # Email alerts
        config.enable_email_alerts = (
            os.environ.get("ENABLE_EMAIL_ALERTS", "false").lower() == "true"
        )
        if recipients := os.environ.get("EMAIL_ALERT_RECIPIENTS"):
            config.email_recipients = [r.strip() for r in recipients.split(",")]
        if from_addr := os.environ.get("EMAIL_FROM_ADDRESS"):
            config.email_from_address = from_addr

        # Slack alerts
        config.enable_slack_alerts = (
            os.environ.get("ENABLE_SLACK_ALERTS", "false").lower() == "true"
        )
        config.slack_webhook_url = os.environ.get("SLACK_WEBHOOK_URL")

        # SNS alerts
        config.enable_sns_alerts = (
            os.environ.get("ENABLE_SNS_ALERTS", "false").lower() == "true"
        )
        config.sns_topic_arn = os.environ.get("SNS_TOPIC_ARN")

        # Storage
        config.dynamo_table_name = os.environ.get("DYNAMODB_TABLE_NAME")
        config.enable_cost_monitoring = (
            os.environ.get("ENABLE_COST_MONITORING", "true").lower() == "true"
        )

        return config

    def get_alert_channels(self) -> List[AlertChannel]:
        """Get configured alert channels."""
        channels = []

        if self.enable_email_alerts and self.email_recipients:
            for email in self.email_recipients:
                channels.append(
                    AlertChannel(
                        channel_type="email",
                        destination=email,
                        enabled=True,
                    )
                )

        if self.enable_slack_alerts and self.slack_webhook_url:
            channels.append(
                AlertChannel(
                    channel_type="slack",
                    destination=self.slack_webhook_url,
                    enabled=True,
                )
            )

        if self.enable_sns_alerts and self.sns_topic_arn:
            channels.append(
                AlertChannel(
                    channel_type="sns",
                    destination=self.sns_topic_arn,
                    enabled=True,
                )
            )

        return channels


@dataclass
class BudgetTemplate:
    """Template for creating budgets."""

    scope_pattern: str  # e.g., "user:*", "service:openai"
    amount: Decimal
    period: BudgetPeriod
    alert_thresholds: List[float] = field(default_factory=lambda: [50, 80, 95, 100])
    rollover_enabled: bool = False
    metadata: Dict[str, str] = field(default_factory=dict)

    def matches_scope(self, scope: str) -> bool:
        """Check if a scope matches this template."""
        if self.scope_pattern.endswith("*"):
            prefix = self.scope_pattern[:-1]
            return scope.startswith(prefix)
        return scope == self.scope_pattern

    def create_budget_for_scope(self, scope: str) -> Dict:
        """Create budget parameters for a specific scope."""
        return {
            "scope": scope,
            "amount": self.amount,
            "period": self.period,
            "alert_thresholds": self.alert_thresholds,
            "rollover_enabled": self.rollover_enabled,
            "metadata": self.metadata.copy(),
        }


class BudgetTemplateManager:
    """Manages budget templates for automatic budget creation."""

    def __init__(self):
        self.templates: List[BudgetTemplate] = []

    def add_template(self, template: BudgetTemplate) -> None:
        """Add a budget template."""
        self.templates.append(template)

    def add_default_templates(self, config: CostMonitoringConfig) -> None:
        """Add default budget templates based on configuration."""
        # User daily budgets
        self.add_template(
            BudgetTemplate(
                scope_pattern="user:*",
                amount=config.default_daily_budget,
                period=BudgetPeriod.DAILY,
                metadata={"type": "user_daily"},
            )
        )

        # Service monthly budgets
        for service in ["openai", "anthropic", "google_places"]:
            self.add_template(
                BudgetTemplate(
                    scope_pattern=f"service:{service}",
                    amount=config.default_monthly_budget,
                    period=BudgetPeriod.MONTHLY,
                    rollover_enabled=True,
                    metadata={"type": "service_monthly"},
                )
            )

        # Global daily budget
        self.add_template(
            BudgetTemplate(
                scope_pattern="global:all",
                amount=config.default_daily_budget * 5,  # 5x individual budget
                period=BudgetPeriod.DAILY,
                alert_thresholds=[60, 80, 90, 100],
                metadata={"type": "global_daily"},
            )
        )

    def get_template_for_scope(self, scope: str) -> Optional[BudgetTemplate]:
        """Get the first matching template for a scope."""
        for template in self.templates:
            if template.matches_scope(scope):
                return template
        return None

    def create_budget_if_needed(
        self,
        budget_manager,
        scope: str,
    ) -> Optional[object]:
        """Create a budget using template if one doesn't exist."""
        # Check if budget already exists
        existing = budget_manager.get_active_budget(scope)
        if existing:
            return None

        # Find matching template
        template = self.get_template_for_scope(scope)
        if not template:
            return None

        # Create budget from template
        params = template.create_budget_for_scope(scope)
        return budget_manager.create_budget(**params)
