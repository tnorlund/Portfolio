"""
Alert management system for cost monitoring notifications.

This module provides multi-channel alert delivery including
email, Slack, webhooks, and SMS for budget threshold alerts.
"""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import boto3
from botocore.exceptions import ClientError

from .cost_monitor import ThresholdAlert, ThresholdLevel

logger = logging.getLogger(__name__)


@dataclass
class AlertChannel:
    """Configuration for an alert channel."""

    channel_type: str  # email, slack, webhook, sns
    destination: str  # email address, webhook URL, SNS topic ARN
    enabled: bool = True
    min_level: ThresholdLevel = ThresholdLevel.INFO
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        """Convert string min_level to ThresholdLevel enum if needed."""
        if isinstance(self.min_level, str):
            try:
                self.min_level = ThresholdLevel[self.min_level.upper()]
            except KeyError:
                logger.warning(
                    f"Invalid threshold level '{self.min_level}', defaulting to INFO"
                )
                self.min_level = ThresholdLevel.INFO

    def should_send(self, alert: ThresholdAlert) -> bool:
        """Check if this channel should receive the alert."""
        if not self.enabled:
            return False

        # Check minimum level
        level_order = [
            ThresholdLevel.INFO,
            ThresholdLevel.WARNING,
            ThresholdLevel.CRITICAL,
            ThresholdLevel.EXCEEDED,
        ]

        alert_idx = level_order.index(alert.level)
        min_idx = level_order.index(self.min_level)

        return alert_idx >= min_idx


class AlertSender(ABC):
    """Abstract base class for alert senders."""

    @abstractmethod
    async def send(self, alert: ThresholdAlert, destination: str) -> bool:
        """Send an alert to a destination."""
        pass


class EmailAlertSender(AlertSender):
    """Sends alerts via email using AWS SES."""

    def __init__(self, ses_client=None, from_email: str = None):
        """Initialize email sender."""
        self.ses_client = ses_client or boto3.client("ses")
        self.from_email = from_email or "noreply@example.com"

    async def send(self, alert: ThresholdAlert, destination: str) -> bool:
        """Send alert via email."""
        try:
            subject = f"[{alert.level.value.upper()}] AI Usage Alert: {alert.scope}"

            body_html = self._format_html_body(alert)
            body_text = self._format_text_body(alert)

            response = self.ses_client.send_email(
                Source=self.from_email,
                Destination={"ToAddresses": [destination]},
                Message={
                    "Subject": {"Data": subject},
                    "Body": {
                        "Text": {"Data": body_text},
                        "Html": {"Data": body_html},
                    },
                },
            )

            logger.info(f"Sent email alert to {destination}: {response['MessageId']}")
            return True

        except ClientError as e:
            logger.error(f"Failed to send email alert: {e}")
            return False

    def _format_html_body(self, alert: ThresholdAlert) -> str:
        """Format HTML email body."""
        color_map = {
            ThresholdLevel.INFO: "#17a2b8",
            ThresholdLevel.WARNING: "#ffc107",
            ThresholdLevel.CRITICAL: "#dc3545",
            ThresholdLevel.EXCEEDED: "#dc3545",
        }

        color = color_map.get(alert.level, "#6c757d")

        return f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; color: #333; }}
                .alert-box {{
                    border: 2px solid {color};
                    border-radius: 8px;
                    padding: 20px;
                    margin: 20px 0;
                    background-color: #f8f9fa;
                }}
                .alert-header {{
                    color: {color};
                    font-size: 24px;
                    font-weight: bold;
                    margin-bottom: 10px;
                }}
                .metric {{
                    margin: 10px 0;
                    font-size: 16px;
                }}
                .metric-label {{
                    font-weight: bold;
                    display: inline-block;
                    width: 150px;
                }}
            </style>
        </head>
        <body>
            <div class="alert-box">
                <div class="alert-header">AI Usage Cost Alert</div>
                <div class="metric">
                    <span class="metric-label">Level:</span> {alert.level.value.upper()}
                </div>
                <div class="metric">
                    <span class="metric-label">Scope:</span> {alert.scope}
                </div>
                <div class="metric">
                    <span class="metric-label">Period:</span> {alert.period}
                </div>
                <div class="metric">
                    <span class="metric-label">Current Spend:</span> ${alert.current_spend:.2f}
                </div>
                <div class="metric">
                    <span class="metric-label">Budget Limit:</span> ${alert.budget_limit:.2f}
                </div>
                <div class="metric">
                    <span class="metric-label">Usage:</span> {alert.threshold_percent}%
                </div>
                <div class="metric">
                    <span class="metric-label">Time:</span> {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}
                </div>
            </div>
            <p>{alert.message}</p>
        </body>
        </html>
        """

    def _format_text_body(self, alert: ThresholdAlert) -> str:
        """Format plain text email body."""
        return f"""
AI Usage Cost Alert

Level: {alert.level.value.upper()}
Scope: {alert.scope}
Period: {alert.period}
Current Spend: ${alert.current_spend:.2f}
Budget Limit: ${alert.budget_limit:.2f}
Usage: {alert.threshold_percent}%
Time: {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}

{alert.message}
"""


class SlackAlertSender(AlertSender):
    """Sends alerts to Slack via webhook."""

    def __init__(self):
        """Initialize Slack sender."""
        import httpx

        self.client = httpx.AsyncClient()

    async def send(self, alert: ThresholdAlert, destination: str) -> bool:
        """Send alert to Slack webhook."""
        try:
            color_map = {
                ThresholdLevel.INFO: "#36a64f",
                ThresholdLevel.WARNING: "#ff9900",
                ThresholdLevel.CRITICAL: "#ff0000",
                ThresholdLevel.EXCEEDED: "#ff0000",
            }

            color = color_map.get(alert.level, "#808080")

            payload = {
                "attachments": [
                    {
                        "color": color,
                        "title": f"AI Usage Cost Alert: {alert.level.value.upper()}",
                        "fields": [
                            {
                                "title": "Scope",
                                "value": alert.scope,
                                "short": True,
                            },
                            {
                                "title": "Period",
                                "value": alert.period,
                                "short": True,
                            },
                            {
                                "title": "Current Spend",
                                "value": f"${alert.current_spend:.2f}",
                                "short": True,
                            },
                            {
                                "title": "Budget Limit",
                                "value": f"${alert.budget_limit:.2f}",
                                "short": True,
                            },
                            {
                                "title": "Usage",
                                "value": f"{alert.threshold_percent}%",
                                "short": True,
                            },
                            {
                                "title": "Remaining",
                                "value": f"${alert.budget_limit - alert.current_spend:.2f}",
                                "short": True,
                            },
                        ],
                        "text": alert.message,
                        "footer": "AI Usage Tracker",
                        "ts": int(alert.timestamp.timestamp()),
                    }
                ]
            }

            response = await self.client.post(destination, json=payload)

            if response.status_code == 200:
                logger.info(f"Sent Slack alert to webhook")
                return True
            else:
                logger.error(f"Slack webhook failed: {response.status_code}")
                return False

        except Exception as e:
            logger.error(f"Failed to send Slack alert: {e}")
            return False


class AlertManager:
    """
    Manages alert delivery across multiple channels.

    This class provides:
    - Multi-channel alert routing
    - Alert deduplication and rate limiting
    - Delivery status tracking
    - Escalation policies
    """

    def __init__(
        self,
        channels: List[AlertChannel],
        rate_limit_minutes: int = 60,
        ses_client=None,
        sns_client=None,
    ):
        """
        Initialize alert manager.

        Args:
            channels: List of configured alert channels
            rate_limit_minutes: Minutes between duplicate alerts
            ses_client: Optional SES client for email
            sns_client: Optional SNS client for SMS/topics
        """
        self.channels = channels
        self.rate_limit = timedelta(minutes=rate_limit_minutes)

        # Initialize senders
        self.senders = {
            "email": EmailAlertSender(ses_client),
            "slack": SlackAlertSender(),
        }

        # Track sent alerts for rate limiting
        self._sent_alerts: Dict[str, datetime] = {}

    async def send_alert(
        self,
        alert: ThresholdAlert,
        force: bool = False,
    ) -> Dict[str, bool]:
        """
        Send alert to all configured channels.

        Args:
            alert: The alert to send
            force: Force send even if rate limited

        Returns:
            Dictionary of channel -> success status
        """
        # Check rate limiting
        if not force and self._is_rate_limited(alert):
            logger.info(f"Alert rate limited: {alert.scope}:{alert.threshold_percent}")
            return {}

        results = {}

        for channel in self.channels:
            if not channel.should_send(alert):
                continue

            sender = self.senders.get(channel.channel_type)
            if not sender:
                logger.warning(f"No sender for channel type: {channel.channel_type}")
                continue

            try:
                success = await sender.send(alert, channel.destination)
                results[f"{channel.channel_type}:{channel.destination}"] = success

                if success:
                    # Update rate limiting
                    alert_key = self._get_alert_key(alert)
                    self._sent_alerts[alert_key] = datetime.now(timezone.utc)

            except Exception as e:
                logger.error(f"Failed to send alert to {channel.channel_type}: {e}")
                results[f"{channel.channel_type}:{channel.destination}"] = False

        return results

    def add_channel(self, channel: AlertChannel) -> None:
        """Add a new alert channel."""
        self.channels.append(channel)

    def remove_channel(self, channel_type: str, destination: str) -> bool:
        """Remove an alert channel."""
        original_count = len(self.channels)
        self.channels = [
            c
            for c in self.channels
            if not (c.channel_type == channel_type and c.destination == destination)
        ]
        return len(self.channels) < original_count

    def _is_rate_limited(self, alert: ThresholdAlert) -> bool:
        """Check if alert is rate limited."""
        alert_key = self._get_alert_key(alert)
        last_sent = self._sent_alerts.get(alert_key)

        if not last_sent:
            return False

        return datetime.now(timezone.utc) - last_sent < self.rate_limit

    def _get_alert_key(self, alert: ThresholdAlert) -> str:
        """Get unique key for alert deduplication."""
        return f"{alert.scope}:{alert.period}:{alert.threshold_percent}"
