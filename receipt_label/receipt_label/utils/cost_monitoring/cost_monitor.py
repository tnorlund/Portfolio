"""
Cost monitoring system for real-time tracking of AI service costs.

This module provides the core CostMonitor class that tracks spending,
checks budget thresholds, and triggers alerts when limits are approached.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric

logger = logging.getLogger(__name__)


class ThresholdLevel(Enum):
    """Alert threshold levels for budget monitoring."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EXCEEDED = "exceeded"


@dataclass
class ThresholdAlert:
    """Represents a cost threshold alert."""

    level: ThresholdLevel
    threshold_percent: float
    current_spend: Decimal
    budget_limit: Decimal
    scope: str
    period: str
    timestamp: datetime
    message: str
    metadata: Dict[str, Any] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert alert to dictionary for serialization."""
        return {
            "level": self.level.value,
            "threshold_percent": self.threshold_percent,
            "current_spend": str(self.current_spend),
            "budget_limit": str(self.budget_limit),
            "scope": self.scope,
            "period": self.period,
            "timestamp": self.timestamp.isoformat(),
            "message": self.message,
            "metadata": self.metadata or {},
        }


class CostMonitor:
    """
    Monitors AI usage costs in real-time and triggers alerts.

    This class provides:
    - Real-time cost accumulation tracking
    - Budget threshold checking
    - Alert generation when thresholds are crossed
    - Cost aggregation by service/user/period
    """

    # Default alert thresholds
    DEFAULT_THRESHOLDS = [
        (50, ThresholdLevel.INFO),
        (80, ThresholdLevel.WARNING),
        (95, ThresholdLevel.CRITICAL),
        (100, ThresholdLevel.EXCEEDED),
    ]

    def __init__(
        self,
        dynamo_client: DynamoClient,
        alert_thresholds: Optional[List[Tuple[float, ThresholdLevel]]] = None,
        alert_cooldown_minutes: int = 60,
    ):
        """
        Initialize the cost monitor.

        Args:
            dynamo_client: DynamoDB client for querying metrics
            alert_thresholds: List of (percentage, level) tuples for alerts
            alert_cooldown_minutes: Minutes to wait before re-alerting
        """
        self.dynamo_client = dynamo_client
        self.alert_thresholds = sorted(
            alert_thresholds or self.DEFAULT_THRESHOLDS, key=lambda x: x[0]
        )
        self.alert_cooldown = timedelta(minutes=alert_cooldown_minutes)

        # Track sent alerts to prevent spam
        self._sent_alerts: Dict[str, datetime] = {}

    def check_budget_threshold(
        self,
        current_usage: AIUsageMetric,
        budget_limit: Decimal,
        scope: str,
        period: str = "daily",
    ) -> Optional[ThresholdAlert]:
        """
        Check if a new usage metric crosses any budget thresholds.

        Args:
            current_usage: The new usage metric to check
            budget_limit: The budget limit for the scope
            scope: Budget scope (e.g., "user:123", "service:openai")
            period: Budget period (daily, weekly, monthly)

        Returns:
            ThresholdAlert if a threshold is crossed, None otherwise
        """
        # Get current period spend
        current_spend = self._get_period_spend(scope, period)

        # Add the new usage cost
        new_total = current_spend + (current_usage.cost_usd or Decimal("0"))

        # Calculate percentage of budget used
        if budget_limit == 0:
            # If budget is 0, consider any spend as exceeding the budget
            percent_used = 100.0 if new_total > 0 else 0.0
        else:
            percent_used = float(new_total / budget_limit * 100)

        # Check which threshold we've crossed
        alert = self._check_thresholds(
            percent_used,
            new_total,
            budget_limit,
            scope,
            period,
        )

        return alert

    def get_current_spend(
        self,
        scope: str,
        period: str = "daily",
        service: Optional[str] = None,
    ) -> Decimal:
        """
        Get current spending for a scope and period.

        Args:
            scope: Budget scope to check
            period: Time period (daily, weekly, monthly)
            service: Optional service filter

        Returns:
            Current spend amount
        """
        return self._get_period_spend(scope, period, service)

    def get_cost_breakdown(
        self,
        scope: str,
        period: str = "daily",
    ) -> Dict[str, Decimal]:
        """
        Get cost breakdown by service for a scope.

        Args:
            scope: Budget scope to analyze
            period: Time period for analysis

        Returns:
            Dictionary of service -> cost
        """
        start_date, end_date = self._get_period_dates(period)

        # Parse scope to determine query parameters
        scope_type, scope_value = self._parse_scope(scope)

        # Query metrics for the period
        metrics = self._query_metrics(
            scope_type,
            scope_value,
            start_date,
            end_date,
        )

        # Aggregate by service
        breakdown: Dict[str, Decimal] = {}
        for metric in metrics:
            service = metric.service
            cost = metric.cost_usd or Decimal("0")
            breakdown[service] = breakdown.get(service, Decimal("0")) + cost

        return breakdown

    def _get_period_spend(
        self,
        scope: str,
        period: str,
        service: Optional[str] = None,
    ) -> Decimal:
        """Calculate spending for a specific period."""
        start_date, end_date = self._get_period_dates(period)

        # Parse scope
        scope_type, scope_value = self._parse_scope(scope)

        # Query metrics
        metrics = self._query_metrics(
            scope_type,
            scope_value,
            start_date,
            end_date,
            service,
        )

        # Sum costs
        total = Decimal("0")
        for metric in metrics:
            if metric.cost_usd:
                total += metric.cost_usd

        return total

    def _check_thresholds(
        self,
        percent_used: float,
        current_spend: Decimal,
        budget_limit: Decimal,
        scope: str,
        period: str,
    ) -> Optional[ThresholdAlert]:
        """Check if any alert thresholds are crossed."""
        # Find the highest threshold that's been crossed
        crossed_threshold = None
        for threshold_percent, level in reversed(self.alert_thresholds):
            if percent_used >= threshold_percent:
                crossed_threshold = (threshold_percent, level)
                break

        if not crossed_threshold:
            return None

        threshold_percent, level = crossed_threshold

        # Check cooldown to prevent spam
        alert_key = f"{scope}:{period}:{threshold_percent}"
        last_alert = self._sent_alerts.get(alert_key)

        if (
            last_alert
            and datetime.now(timezone.utc) - last_alert < self.alert_cooldown
        ):
            return None

        # Create alert
        alert = ThresholdAlert(
            level=level,
            threshold_percent=threshold_percent,
            current_spend=current_spend,
            budget_limit=budget_limit,
            scope=scope,
            period=period,
            timestamp=datetime.now(timezone.utc),
            message=self._format_alert_message(
                level,
                threshold_percent,
                current_spend,
                budget_limit,
                scope,
                period,
            ),
            metadata={
                "percent_used": percent_used,
                "remaining_budget": str(budget_limit - current_spend),
            },
        )

        # Record alert sent time
        self._sent_alerts[alert_key] = alert.timestamp

        return alert

    def _format_alert_message(
        self,
        level: ThresholdLevel,
        threshold_percent: float,
        current_spend: Decimal,
        budget_limit: Decimal,
        scope: str,
        period: str,
    ) -> str:
        """Format a human-readable alert message."""
        if level == ThresholdLevel.EXCEEDED:
            return (
                f"BUDGET EXCEEDED: {scope} has spent ${current_spend:.2f} "
                f"exceeding the {period} budget of ${budget_limit:.2f}"
            )
        else:
            return (
                f"{level.value.upper()}: {scope} has used {threshold_percent}% "
                f"(${current_spend:.2f}) of the {period} budget (${budget_limit:.2f})"
            )

    def _get_period_dates(self, period: str) -> Tuple[str, str]:
        """Get start and end dates for a period."""
        now = datetime.now(timezone.utc)

        if period == "daily":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "weekly":
            # Start of week (Monday)
            days_since_monday = now.weekday()
            start = now - timedelta(days=days_since_monday)
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "monthly":
            start = now.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
        else:
            raise ValueError(f"Invalid period: {period}")

        end = now

        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    def _parse_scope(self, scope: str) -> Tuple[str, str]:
        """Parse scope string into type and value."""
        if ":" not in scope:
            raise ValueError(f"Invalid scope format: {scope}")

        scope_type, scope_value = scope.split(":", 1)

        if scope_type not in [
            "user",
            "service",
            "global",
            "job",
            "environment",
        ]:
            raise ValueError(f"Invalid scope type: {scope_type}")

        return scope_type, scope_value

    def _query_metrics(
        self,
        scope_type: str,
        scope_value: str,
        start_date: str,
        end_date: str,
        service: Optional[str] = None,
    ) -> List[AIUsageMetric]:
        """Query metrics from DynamoDB based on scope."""
        if scope_type == "service":
            return AIUsageMetric.query_by_service_date(
                self.dynamo_client,
                service=scope_value,
                start_date=start_date,
                end_date=end_date,
            )
        elif scope_type == "user":
            # Use enhanced GSI3 for user queries (eliminates scans)
            return self._query_by_user_gsi3(
                scope_value, start_date, end_date, service
            )
        elif scope_type == "global":
            # Query all services and aggregate
            all_metrics = []
            for svc in ["openai", "anthropic", "google_places"]:
                if not service or service == svc:
                    metrics = AIUsageMetric.query_by_service_date(
                        self.dynamo_client,
                        service=svc,
                        start_date=start_date,
                        end_date=end_date,
                    )
                    all_metrics.extend(metrics)
            return all_metrics
        elif scope_type == "job":
            # Use GSI3 for job queries (more efficient than scan)
            return self._query_by_job_gsi3(
                scope_value, start_date, end_date, service
            )
        elif scope_type == "environment":
            # Use enhanced GSI3 for environment queries (eliminates scans)
            return self._query_by_environment_gsi3(
                scope_value, start_date, end_date, service
            )
        else:
            # For other scopes, use scan with filters as fallback
            return self._scan_metrics_with_filters(
                scope_type, scope_value, start_date, end_date, service
            )

    def _query_by_job_gsi3(
        self,
        job_id: str,
        start_date: str,
        end_date: str,
        service: Optional[str] = None,
    ) -> List[AIUsageMetric]:
        """
        Query metrics by job_id using GSI3.

        This is more efficient than scanning as it uses the existing GSI3
        which has PK=JOB#{job_id} for job-based queries.
        """
        try:
            # Build GSI3 query
            key_condition = "GSI3PK = :pk"
            expression_values = {
                ":pk": {"S": f"JOB#{job_id}"},
            }

            # Add date filtering if needed (though GSI3SK uses timestamp, not date)
            # We'll filter results after querying since GSI3SK is AI_USAGE#{timestamp}

            # Add service filter if specified
            filter_expression = None
            if service:
                filter_expression = "#service = :service"
                expression_values[":service"] = {"S": service}

            query_params = {
                "TableName": self.dynamo_client.table_name,
                "IndexName": "GSI3",
                "KeyConditionExpression": key_condition,
                "ExpressionAttributeValues": expression_values,
            }

            if filter_expression:
                query_params["FilterExpression"] = filter_expression
                query_params["ExpressionAttributeNames"] = {
                    "#service": "service"
                }

            # Perform GSI3 query
            response = self.dynamo_client._client.query(**query_params)

            # Convert items to AIUsageMetric objects and filter by date
            metrics = []
            for item in response.get("Items", []):
                try:
                    metric = self._dynamodb_item_to_metric(item)
                    if (
                        metric
                        and start_date
                        <= metric.timestamp.strftime("%Y-%m-%d")
                        <= end_date
                    ):
                        metrics.append(metric)
                except Exception as e:
                    logger.warning(
                        f"Failed to convert DynamoDB item to metric: {e}"
                    )
                    continue

            logger.info(
                f"Queried {len(metrics)} metrics for job:{job_id} "
                f"between {start_date} and {end_date} using GSI3"
            )

            return metrics

        except Exception as e:
            logger.error(
                f"Failed to query metrics for job:{job_id} using GSI3: {e}"
            )
            # Fallback to scan if GSI3 query fails
            logger.info(f"Falling back to scan for job:{job_id}")
            return self._scan_metrics_with_filters(
                "job", job_id, start_date, end_date, service
            )

    def _query_by_user_gsi3(
        self,
        user_id: str,
        start_date: str,
        end_date: str,
        service: Optional[str] = None,
    ) -> List[AIUsageMetric]:
        """
        Query metrics by user_id using enhanced GSI3.

        Uses the enhanced GSI3 with PK=USER#{user_id} for user-based queries.
        """
        try:
            # Build GSI3 query
            key_condition = "GSI3PK = :pk"
            expression_values = {
                ":pk": {"S": f"USER#{user_id}"},
            }

            # Add service filter if specified
            filter_expression = None
            if service:
                filter_expression = "#service = :service"
                expression_values[":service"] = {"S": service}

            query_params = {
                "TableName": self.dynamo_client.table_name,
                "IndexName": "GSI3",
                "KeyConditionExpression": key_condition,
                "ExpressionAttributeValues": expression_values,
            }

            if filter_expression:
                query_params["FilterExpression"] = filter_expression
                query_params["ExpressionAttributeNames"] = {
                    "#service": "service"
                }

            # Perform GSI3 query
            response = self.dynamo_client._client.query(**query_params)

            # Convert items to AIUsageMetric objects and filter by date
            metrics = []
            for item in response.get("Items", []):
                try:
                    metric = self._dynamodb_item_to_metric(item)
                    if (
                        metric
                        and start_date
                        <= metric.timestamp.strftime("%Y-%m-%d")
                        <= end_date
                    ):
                        metrics.append(metric)
                except Exception as e:
                    logger.warning(
                        f"Failed to convert DynamoDB item to metric: {e}"
                    )
                    continue

            logger.info(
                f"Queried {len(metrics)} metrics for user:{user_id} "
                f"between {start_date} and {end_date} using enhanced GSI3"
            )

            return metrics

        except Exception as e:
            logger.error(
                f"Failed to query metrics for user:{user_id} using enhanced GSI3: {e}"
            )
            # Fallback to scan if GSI3 query fails
            logger.info(f"Falling back to scan for user:{user_id}")
            return self._scan_metrics_with_filters(
                "user", user_id, start_date, end_date, service
            )

    def _query_by_environment_gsi3(
        self,
        environment: str,
        start_date: str,
        end_date: str,
        service: Optional[str] = None,
    ) -> List[AIUsageMetric]:
        """
        Query metrics by environment using enhanced GSI3.

        Uses the enhanced GSI3 with PK=ENV#{environment} for environment-based queries.
        """
        try:
            # Build GSI3 query
            key_condition = "GSI3PK = :pk"
            expression_values = {
                ":pk": {"S": f"ENV#{environment}"},
            }

            # Add service filter if specified
            filter_expression = None
            if service:
                filter_expression = "#service = :service"
                expression_values[":service"] = {"S": service}

            query_params = {
                "TableName": self.dynamo_client.table_name,
                "IndexName": "GSI3",
                "KeyConditionExpression": key_condition,
                "ExpressionAttributeValues": expression_values,
            }

            if filter_expression:
                query_params["FilterExpression"] = filter_expression
                query_params["ExpressionAttributeNames"] = {
                    "#service": "service"
                }

            # Perform GSI3 query
            response = self.dynamo_client._client.query(**query_params)

            # Convert items to AIUsageMetric objects and filter by date
            metrics = []
            for item in response.get("Items", []):
                try:
                    metric = self._dynamodb_item_to_metric(item)
                    if (
                        metric
                        and start_date
                        <= metric.timestamp.strftime("%Y-%m-%d")
                        <= end_date
                    ):
                        metrics.append(metric)
                except Exception as e:
                    logger.warning(
                        f"Failed to convert DynamoDB item to metric: {e}"
                    )
                    continue

            logger.info(
                f"Queried {len(metrics)} metrics for environment:{environment} "
                f"between {start_date} and {end_date} using enhanced GSI3"
            )

            return metrics

        except Exception as e:
            logger.error(
                f"Failed to query metrics for environment:{environment} using enhanced GSI3: {e}"
            )
            # Fallback to scan if GSI3 query fails
            logger.info(f"Falling back to scan for environment:{environment}")
            return self._scan_metrics_with_filters(
                "environment", environment, start_date, end_date, service
            )

    def _scan_metrics_with_filters(
        self,
        scope_type: str,
        scope_value: str,
        start_date: str,
        end_date: str,
        service: Optional[str] = None,
    ) -> List[AIUsageMetric]:
        """
        Scan metrics with filters for job/environment scopes.

        This is less efficient than indexed queries but necessary
        for scope types that don't have dedicated GSIs.
        """
        try:
            # Build filter expression
            filter_expression = (
                "attribute_exists(#scope_attr) AND #scope_attr = :scope_value"
            )
            filter_expression += " AND #date BETWEEN :start_date AND :end_date"

            expression_attribute_names = {
                "#date": "date",
            }

            expression_attribute_values = {
                ":scope_value": {"S": scope_value},
                ":start_date": {"S": start_date},
                ":end_date": {"S": end_date},
            }

            if scope_type == "job":
                expression_attribute_names["#scope_attr"] = "job_id"
            elif scope_type == "environment":
                expression_attribute_names["#scope_attr"] = "environment"
            else:
                logger.warning(
                    f"Unsupported scope type for scan: {scope_type}"
                )
                return []

            # Add service filter if specified
            if service:
                filter_expression += " AND #service = :service"
                expression_attribute_names["#service"] = "service"
                expression_attribute_values[":service"] = {"S": service}

            # Perform scan
            response = self.dynamo_client._client.scan(
                TableName=self.dynamo_client.table_name,
                FilterExpression=filter_expression,
                ExpressionAttributeNames=expression_attribute_names,
                ExpressionAttributeValues=expression_attribute_values,
            )

            # Convert items back to AIUsageMetric objects
            metrics = []
            for item in response.get("Items", []):
                try:
                    # Convert DynamoDB item to AIUsageMetric
                    metric = self._dynamodb_item_to_metric(item)
                    if metric:
                        metrics.append(metric)
                except Exception as e:
                    logger.warning(
                        f"Failed to convert DynamoDB item to metric: {e}"
                    )
                    continue

            logger.info(
                f"Scanned {len(metrics)} metrics for {scope_type}:{scope_value} "
                f"between {start_date} and {end_date}"
            )

            return metrics

        except Exception as e:
            logger.error(
                f"Failed to scan metrics for {scope_type}:{scope_value}: {e}"
            )
            return []

    def _dynamodb_item_to_metric(self, item: Dict) -> Optional[AIUsageMetric]:
        """Convert DynamoDB item to AIUsageMetric object."""
        try:
            # Extract required fields
            service = item.get("service", {}).get("S", "")
            model = item.get("model", {}).get("S", "")
            operation = item.get("operation", {}).get("S", "")
            timestamp_str = item.get("timestamp", {}).get("S", "")

            if not all([service, model, operation, timestamp_str]):
                return None

            # Parse timestamp
            from datetime import datetime

            timestamp = datetime.fromisoformat(
                timestamp_str.replace("Z", "+00:00")
            )

            # Extract optional fields with proper type conversion
            def get_decimal_value(field_name: str) -> Optional[Decimal]:
                field = item.get(field_name, {})
                if "N" in field:
                    return Decimal(field["N"])
                elif "S" in field:
                    try:
                        return Decimal(field["S"])
                    except:
                        return None
                return None

            def get_int_value(field_name: str) -> Optional[int]:
                field = item.get(field_name, {})
                if "N" in field:
                    return int(field["N"])
                return None

            def get_string_value(field_name: str) -> Optional[str]:
                field = item.get(field_name, {})
                return field.get("S")

            # Create AIUsageMetric object
            metric = AIUsageMetric(
                service=service,
                model=model,
                operation=operation,
                timestamp=timestamp,
                request_id=get_string_value("request_id"),
                input_tokens=get_int_value("input_tokens"),
                output_tokens=get_int_value("output_tokens"),
                total_tokens=get_int_value("total_tokens"),
                api_calls=get_int_value("api_calls") or 1,
                cost_usd=(
                    float(cost_val)
                    if (cost_val := get_decimal_value("cost_usd")) is not None
                    else None
                ),
                latency_ms=get_int_value("latency_ms"),
                user_id=get_string_value("user_id"),
                job_id=get_string_value("job_id"),
                batch_id=get_string_value("batch_id"),
                github_pr=get_int_value("github_pr"),
                environment=get_string_value("environment"),
                error=get_string_value("error"),
            )

            return metric

        except Exception as e:
            logger.warning(f"Failed to parse DynamoDB item: {e}")
            return None
