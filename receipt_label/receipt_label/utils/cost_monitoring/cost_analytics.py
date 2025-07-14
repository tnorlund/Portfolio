"""
Cost analytics engine for AI usage insights and reporting.

This module provides advanced analytics including trend analysis,
forecasting, anomaly detection, and cost optimization recommendations.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric

logger = logging.getLogger(__name__)


class TrendDirection(Enum):
    """Trend direction indicators."""

    INCREASING = "increasing"
    DECREASING = "decreasing"
    STABLE = "stable"


@dataclass
class CostTrend:
    """Represents a cost trend analysis."""

    period: str
    direction: TrendDirection
    change_percent: float
    current_value: Decimal
    previous_value: Decimal
    forecast_value: Optional[Decimal] = None
    confidence_interval: Optional[Tuple[Decimal, Decimal]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "period": self.period,
            "direction": self.direction.value,
            "change_percent": self.change_percent,
            "current_value": str(self.current_value),
            "previous_value": str(self.previous_value),
            "forecast_value": (
                str(self.forecast_value) if self.forecast_value else None
            ),
            "confidence_interval": (
                [
                    str(self.confidence_interval[0]),
                    str(self.confidence_interval[1]),
                ]
                if self.confidence_interval
                else None
            ),
        }


@dataclass
class CostAnomaly:
    """Represents a detected cost anomaly."""

    timestamp: datetime
    service: str
    actual_cost: Decimal
    expected_cost: Decimal
    deviation_percent: float
    severity: str  # low, medium, high
    possible_causes: List[str]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "service": self.service,
            "actual_cost": str(self.actual_cost),
            "expected_cost": str(self.expected_cost),
            "deviation_percent": self.deviation_percent,
            "severity": self.severity,
            "possible_causes": self.possible_causes,
        }


@dataclass
class CostOptimizationRecommendation:
    """Cost optimization recommendation."""

    category: str  # model_selection, batch_processing, caching, etc.
    potential_savings: Decimal
    implementation_effort: str  # low, medium, high
    description: str
    specific_actions: List[str]
    affected_services: List[str]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "category": self.category,
            "potential_savings": str(self.potential_savings),
            "implementation_effort": self.implementation_effort,
            "description": self.description,
            "specific_actions": self.specific_actions,
            "affected_services": self.affected_services,
        }


class CostAnalytics:
    """
    Advanced cost analytics engine.

    Provides:
    - Trend analysis and forecasting
    - Anomaly detection
    - Cost optimization recommendations
    - Detailed usage reports
    """

    def __init__(self, dynamo_client: DynamoClient):
        """Initialize analytics engine."""
        self.dynamo_client = dynamo_client

    def analyze_trends(
        self,
        scope: str,
        period: str = "daily",
        lookback_days: int = 30,
        forecast_days: int = 7,
    ) -> CostTrend:
        """
        Analyze cost trends for a scope.

        Args:
            scope: Budget scope to analyze
            period: Analysis period (daily, weekly, monthly)
            lookback_days: Days of history to analyze
            forecast_days: Days to forecast ahead

        Returns:
            CostTrend analysis
        """
        # Get historical data
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=lookback_days)

        daily_costs = self._get_daily_costs(
            scope,
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d"),
        )

        if len(daily_costs) < 2:
            return CostTrend(
                period=period,
                direction=TrendDirection.STABLE,
                change_percent=0.0,
                current_value=Decimal("0"),
                previous_value=Decimal("0"),
            )

        # Calculate trend
        current_period = self._aggregate_by_period(daily_costs[-7:], period)
        previous_period = self._aggregate_by_period(
            daily_costs[-14:-7], period
        )

        if previous_period > 0:
            change_percent = float(
                (current_period - previous_period) / previous_period * 100
            )
        else:
            change_percent = 100.0 if current_period > 0 else 0.0

        # Determine direction
        if abs(change_percent) < 5:
            direction = TrendDirection.STABLE
        elif change_percent > 0:
            direction = TrendDirection.INCREASING
        else:
            direction = TrendDirection.DECREASING

        # Simple linear forecast
        forecast_value = None
        confidence_interval = None

        if forecast_days > 0 and len(daily_costs) >= 7:
            forecast_value, confidence_interval = self._forecast_linear(
                daily_costs,
                forecast_days,
            )

        return CostTrend(
            period=period,
            direction=direction,
            change_percent=change_percent,
            current_value=current_period,
            previous_value=previous_period,
            forecast_value=forecast_value,
            confidence_interval=confidence_interval,
        )

    def detect_anomalies(
        self,
        scope: str,
        sensitivity: float = 2.0,
        lookback_days: int = 30,
    ) -> List[CostAnomaly]:
        """
        Detect cost anomalies using statistical methods.

        Args:
            scope: Budget scope to analyze
            sensitivity: Standard deviations for anomaly threshold
            lookback_days: Days of history to analyze

        Returns:
            List of detected anomalies
        """
        # Get historical data by service
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=lookback_days)

        service_costs = self._get_service_daily_costs(
            scope,
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d"),
        )

        anomalies = []

        for service, daily_costs in service_costs.items():
            if len(daily_costs) < 7:
                continue

            # Calculate statistics
            costs_array = np.array([float(cost) for _, cost in daily_costs])
            mean = np.mean(costs_array)
            std = np.std(costs_array)

            if std == 0:
                continue

            # Check for anomalies
            for date, cost in daily_costs:
                z_score = abs(float(cost) - mean) / std

                if z_score > sensitivity:
                    deviation_percent = (
                        ((float(cost) - mean) / mean * 100)
                        if mean > 0
                        else 100
                    )

                    # Determine severity
                    if z_score > 3:
                        severity = "high"
                    elif z_score > 2.5:
                        severity = "medium"
                    else:
                        severity = "low"

                    # Analyze possible causes
                    causes = self._analyze_anomaly_causes(
                        service,
                        date,
                        cost,
                        Decimal(str(mean)),
                    )

                    anomalies.append(
                        CostAnomaly(
                            timestamp=date,
                            service=service,
                            actual_cost=cost,
                            expected_cost=Decimal(str(mean)),
                            deviation_percent=deviation_percent,
                            severity=severity,
                            possible_causes=causes,
                        )
                    )

        return sorted(anomalies, key=lambda a: a.timestamp, reverse=True)

    def generate_optimization_recommendations(
        self,
        scope: str,
        lookback_days: int = 30,
    ) -> List[CostOptimizationRecommendation]:
        """
        Generate cost optimization recommendations.

        Args:
            scope: Budget scope to analyze
            lookback_days: Days of history to analyze

        Returns:
            List of optimization recommendations
        """
        recommendations = []

        # Get usage patterns
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=lookback_days)

        metrics = self._get_detailed_metrics(
            scope,
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d"),
        )

        # Analyze model usage
        model_rec = self._analyze_model_optimization(metrics)
        if model_rec:
            recommendations.append(model_rec)

        # Check for batch processing opportunities
        batch_rec = self._analyze_batch_opportunities(metrics)
        if batch_rec:
            recommendations.append(batch_rec)

        # Look for caching opportunities
        cache_rec = self._analyze_caching_opportunities(metrics)
        if cache_rec:
            recommendations.append(cache_rec)

        # Check for off-peak usage
        timing_rec = self._analyze_timing_optimization(metrics)
        if timing_rec:
            recommendations.append(timing_rec)

        return sorted(
            recommendations,
            key=lambda r: r.potential_savings,
            reverse=True,
        )

    def generate_cost_report(
        self,
        scope: str,
        start_date: str,
        end_date: str,
        group_by: str = "service",
    ) -> Dict[str, Any]:
        """
        Generate comprehensive cost report.

        Args:
            scope: Budget scope for report
            start_date: Report start date
            end_date: Report end date
            group_by: Grouping dimension (service, model, user, etc.)

        Returns:
            Detailed cost report
        """
        metrics = self._get_detailed_metrics(scope, start_date, end_date)

        # Calculate totals
        total_cost = sum(m.cost_usd or Decimal("0") for m in metrics)
        total_requests = len(metrics)

        # Group by dimension
        grouped = {}
        for metric in metrics:
            key = getattr(metric, group_by, "unknown")
            if key not in grouped:
                grouped[key] = {
                    "cost": Decimal("0"),
                    "requests": 0,
                    "tokens": 0,
                }
            grouped[key]["cost"] += metric.cost_usd or Decimal("0")
            grouped[key]["requests"] += 1
            grouped[key]["tokens"] += metric.total_tokens or 0

        # Calculate top users if applicable
        top_users = []
        if scope.startswith("global:") or scope.startswith("service:"):
            user_costs = {}
            for metric in metrics:
                user = metric.user_id
                if user:
                    user_costs[user] = user_costs.get(user, Decimal("0")) + (
                        metric.cost_usd or Decimal("0")
                    )
            top_users = sorted(
                [(u, c) for u, c in user_costs.items()],
                key=lambda x: x[1],
                reverse=True,
            )[:10]

        return {
            "period": {
                "start": start_date,
                "end": end_date,
            },
            "scope": scope,
            "summary": {
                "total_cost": str(total_cost),
                "total_requests": total_requests,
                "average_cost_per_request": (
                    str(total_cost / total_requests)
                    if total_requests > 0
                    else "0"
                ),
            },
            "breakdown": {
                key: {
                    "cost": str(values["cost"]),
                    "requests": values["requests"],
                    "tokens": values["tokens"],
                    "percentage": (
                        float(values["cost"] / total_cost * 100)
                        if total_cost > 0
                        else 0
                    ),
                }
                for key, values in grouped.items()
            },
            "top_users": [
                {"user_id": user, "cost": str(cost)}
                for user, cost in top_users
            ],
        }

    def _get_daily_costs(
        self,
        scope: str,
        start_date: str,
        end_date: str,
    ) -> List[Tuple[datetime, Decimal]]:
        """Get daily cost aggregations."""
        scope_type, scope_value = self._parse_scope(scope)

        # Query metrics
        metrics = self._query_metrics_by_scope(
            scope_type,
            scope_value,
            start_date,
            end_date,
        )

        # Aggregate by day
        daily_costs = {}
        for metric in metrics:
            date = metric.timestamp.date()
            cost = metric.cost_usd or Decimal("0")
            daily_costs[date] = daily_costs.get(date, Decimal("0")) + cost

        # Convert to sorted list
        return sorted(
            [
                (
                    datetime.combine(date, datetime.min.time(), timezone.utc),
                    cost,
                )
                for date, cost in daily_costs.items()
            ]
        )

    def _get_service_daily_costs(
        self,
        scope: str,
        start_date: str,
        end_date: str,
    ) -> Dict[str, List[Tuple[datetime, Decimal]]]:
        """Get daily costs broken down by service."""
        scope_type, scope_value = self._parse_scope(scope)

        metrics = self._query_metrics_by_scope(
            scope_type,
            scope_value,
            start_date,
            end_date,
        )

        # Aggregate by service and day
        service_daily = {}
        for metric in metrics:
            service = metric.service
            date = metric.timestamp.date()
            cost = metric.cost_usd or Decimal("0")

            if service not in service_daily:
                service_daily[service] = {}
            service_daily[service][date] = (
                service_daily[service].get(date, Decimal("0")) + cost
            )

        # Convert to sorted lists
        result = {}
        for service, daily_costs in service_daily.items():
            result[service] = sorted(
                [
                    (
                        datetime.combine(
                            date, datetime.min.time(), timezone.utc
                        ),
                        cost,
                    )
                    for date, cost in daily_costs.items()
                ]
            )

        return result

    def _aggregate_by_period(
        self,
        daily_costs: List[Tuple[datetime, Decimal]],
        period: str,
    ) -> Decimal:
        """Aggregate daily costs by period."""
        if not daily_costs:
            return Decimal("0")

        return sum(cost for _, cost in daily_costs)

    def _forecast_linear(
        self,
        daily_costs: List[Tuple[datetime, Decimal]],
        forecast_days: int,
    ) -> Tuple[Decimal, Tuple[Decimal, Decimal]]:
        """Simple linear forecast with confidence interval."""
        if len(daily_costs) < 2:
            return Decimal("0"), (Decimal("0"), Decimal("0"))

        # Extract costs
        costs = [float(cost) for _, cost in daily_costs]
        x = np.arange(len(costs))

        # Fit linear regression
        coeffs = np.polyfit(x, costs, 1)

        # Forecast
        future_x = len(costs) + forecast_days - 1
        forecast = coeffs[0] * future_x + coeffs[1]

        # Simple confidence interval (±20%)
        lower = forecast * 0.8
        upper = forecast * 1.2

        return (
            Decimal(str(max(0, forecast))),
            (Decimal(str(max(0, lower))), Decimal(str(upper))),
        )

    def _analyze_anomaly_causes(
        self,
        service: str,
        date: datetime,
        actual_cost: Decimal,
        expected_cost: Decimal,
    ) -> List[str]:
        """Analyze possible causes for cost anomaly."""
        causes = []

        if actual_cost > expected_cost * 2:
            causes.append("Significant spike in usage volume")
            causes.append("Possible runaway process or infinite loop")

        if service == "openai" and actual_cost > expected_cost * Decimal(
            "1.5"
        ):
            causes.append("Increased usage of expensive models (GPT-4)")
            causes.append("Longer prompt/completion tokens than usual")

        if date.weekday() in [5, 6]:  # Weekend
            causes.append("Unusual weekend activity")

        if date.hour >= 22 or date.hour <= 6:
            causes.append("Off-hours processing spike")

        return causes or ["Unexpected usage pattern"]

    def _analyze_model_optimization(
        self,
        metrics: List[AIUsageMetric],
    ) -> Optional[CostOptimizationRecommendation]:
        """Analyze model usage for optimization opportunities."""
        model_costs = {}
        model_counts = {}

        for metric in metrics:
            if metric.service == "openai":
                model = metric.model
                cost = metric.cost_usd or Decimal("0")
                model_costs[model] = (
                    model_costs.get(model, Decimal("0")) + cost
                )
                model_counts[model] = model_counts.get(model, 0) + 1

        # Check for GPT-4 usage that could use GPT-3.5
        gpt4_cost = model_costs.get("gpt-4", Decimal("0"))
        gpt35_cost = model_costs.get("gpt-3.5-turbo", Decimal("0"))

        if gpt4_cost > gpt35_cost:
            # Estimate 80% of GPT-4 usage could use GPT-3.5
            potential_savings = (
                gpt4_cost * Decimal("0.8") * Decimal("0.9")
            )  # 90% cost reduction

            return CostOptimizationRecommendation(
                category="model_selection",
                potential_savings=potential_savings,
                implementation_effort="low",
                description="Optimize model selection for cost efficiency",
                specific_actions=[
                    "Review GPT-4 usage and identify tasks suitable for GPT-3.5-turbo",
                    "Implement model routing based on task complexity",
                    "Use GPT-4 only for complex reasoning or creative tasks",
                    "Consider fine-tuned GPT-3.5 models for specific use cases",
                ],
                affected_services=["openai"],
            )

        return None

    def _analyze_batch_opportunities(
        self,
        metrics: List[AIUsageMetric],
    ) -> Optional[CostOptimizationRecommendation]:
        """Analyze for batch processing opportunities."""
        # Group by hour to find patterns
        hourly_requests = {}
        total_cost = Decimal("0")

        for metric in metrics:
            hour = metric.timestamp.hour
            hourly_requests[hour] = hourly_requests.get(hour, 0) + 1
            total_cost += metric.cost_usd or Decimal("0")

        # Check for burst patterns
        peak_hour = max(hourly_requests.items(), key=lambda x: x[1])[1]
        avg_hour = sum(hourly_requests.values()) / 24

        if peak_hour > avg_hour * 3:
            # Significant burst pattern - batch processing could help
            potential_savings = total_cost * Decimal(
                "0.5"
            )  # 50% savings with batch API

            return CostOptimizationRecommendation(
                category="batch_processing",
                potential_savings=potential_savings,
                implementation_effort="medium",
                description="Implement batch processing for burst workloads",
                specific_actions=[
                    "Use OpenAI Batch API for non-time-sensitive requests",
                    "Queue requests during peak hours for batch processing",
                    "Implement request aggregation for similar prompts",
                    "Schedule batch jobs during off-peak hours",
                ],
                affected_services=["openai"],
            )

        return None

    def _analyze_caching_opportunities(
        self,
        metrics: List[AIUsageMetric],
    ) -> Optional[CostOptimizationRecommendation]:
        """Analyze for caching opportunities."""
        # This is simplified - in reality would need request content
        total_requests = len(metrics)
        total_cost = sum(m.cost_usd or Decimal("0") for m in metrics)

        if total_requests > 1000:
            # Assume 20% of requests could be cached
            potential_savings = total_cost * Decimal("0.2")

            return CostOptimizationRecommendation(
                category="caching",
                potential_savings=potential_savings,
                implementation_effort="medium",
                description="Implement response caching for repeated queries",
                specific_actions=[
                    "Implement Redis or similar caching layer",
                    "Cache embedding results for common inputs",
                    "Cache completion results for template-based prompts",
                    "Set appropriate TTL based on data freshness needs",
                ],
                affected_services=["openai", "anthropic"],
            )

        return None

    def _analyze_timing_optimization(
        self,
        metrics: List[AIUsageMetric],
    ) -> Optional[CostOptimizationRecommendation]:
        """Analyze for timing-based optimization."""
        # Group by hour
        hourly_costs = {}
        for metric in metrics:
            hour = metric.timestamp.hour
            cost = metric.cost_usd or Decimal("0")
            hourly_costs[hour] = hourly_costs.get(hour, Decimal("0")) + cost

        # Find peak vs off-peak
        business_hours_cost = sum(
            cost for hour, cost in hourly_costs.items() if 9 <= hour <= 17
        )
        total_cost = sum(hourly_costs.values())

        if business_hours_cost > total_cost * Decimal("0.7"):
            potential_savings = total_cost * Decimal(
                "0.1"
            )  # 10% from better distribution

            return CostOptimizationRecommendation(
                category="timing",
                potential_savings=potential_savings,
                implementation_effort="low",
                description="Shift non-urgent processing to off-peak hours",
                specific_actions=[
                    "Schedule batch jobs for overnight processing",
                    "Implement priority queues for time-sensitive requests",
                    "Use async processing for non-critical tasks",
                    "Consider time-based pricing if available",
                ],
                affected_services=["openai", "anthropic", "google_places"],
            )

        return None

    def _parse_scope(self, scope: str) -> Tuple[str, str]:
        """Parse scope string."""
        if ":" not in scope:
            raise ValueError(f"Invalid scope format: {scope}")
        return scope.split(":", 1)

    def _query_metrics_by_scope(
        self,
        scope_type: str,
        scope_value: str,
        start_date: str,
        end_date: str,
    ) -> List[AIUsageMetric]:
        """Query metrics based on scope type."""
        if scope_type == "service":
            return AIUsageMetric.query_by_service_date(
                self.dynamo_client,
                service=scope_value,
                start_date=start_date,
                end_date=end_date,
            )
        elif scope_type == "user":
            return AIUsageMetric.query_by_user(
                self.dynamo_client,
                user_id=scope_value,
                start_date=start_date,
                end_date=end_date,
            )
        elif scope_type == "global":
            # Query all services
            all_metrics = []
            for service in ["openai", "anthropic", "google_places"]:
                metrics = AIUsageMetric.query_by_service_date(
                    self.dynamo_client,
                    service=service,
                    start_date=start_date,
                    end_date=end_date,
                )
                all_metrics.extend(metrics)
            return all_metrics
        else:
            return []

    def _get_detailed_metrics(
        self,
        scope: str,
        start_date: str,
        end_date: str,
    ) -> List[AIUsageMetric]:
        """Get detailed metrics for analysis."""
        scope_type, scope_value = self._parse_scope(scope)
        return self._query_metrics_by_scope(
            scope_type,
            scope_value,
            start_date,
            end_date,
        )
