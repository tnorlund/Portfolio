# AI Usage Cost Monitoring Guide

## Overview

The AI Usage Cost Monitoring system provides comprehensive cost control for AI service usage with real-time budget tracking, alerts, and analytics. This guide covers setup, configuration, and usage of the Phase 4 implementation.

## Features

- **Real-time Budget Monitoring**: Track spending against budgets as usage occurs
- **Multi-level Budgets**: Set budgets by user, service, job, or globally
- **Intelligent Alerts**: Multi-channel notifications when thresholds are crossed
- **Cost Analytics**: Trend analysis, anomaly detection, and optimization recommendations
- **Automatic Integration**: Seamless integration with existing AI usage tracking

## Quick Start

### 1. Basic Setup

```python
from receipt_dynamo import DynamoClient
from receipt_label.utils.cost_monitoring import (
    create_cost_monitored_tracker,
    BudgetManager,
    BudgetPeriod,
)

# Create a cost-monitored tracker
dynamo_client = DynamoClient(table_name="AIUsageMetrics")

tracker = create_cost_monitored_tracker(
    dynamo_client=dynamo_client,
    enable_email_alerts=True,
    email_addresses=["alerts@example.com"],
    enable_slack_alerts=True,
    slack_webhook_url="https://hooks.slack.com/...",
)

# Create budgets
budget_manager = BudgetManager(dynamo_client)

# Daily budget for a user
budget_manager.create_budget(
    scope="user:john-doe",
    amount=Decimal("50.00"),
    period=BudgetPeriod.DAILY,
    alert_thresholds=[50, 80, 95, 100],
)

# Monthly budget for OpenAI service
budget_manager.create_budget(
    scope="service:openai",
    amount=Decimal("1000.00"),
    period=BudgetPeriod.MONTHLY,
    rollover_enabled=True,
)
```

### 2. Using with OpenAI

```python
from openai import OpenAI

# Create wrapped client with automatic tracking
openai_client = OpenAI(api_key="your-key")
tracked_client = AIUsageTracker.create_wrapped_openai_client(
    openai_client,
    tracker
)

# Use normally - costs are automatically tracked and checked
response = tracked_client.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=[{"role": "user", "content": "Hello!"}]
)
# If this exceeds budget, alerts are sent automatically
```

## Configuration

### Environment Variables

```bash
# Budget defaults
DEFAULT_DAILY_BUDGET=100.00
DEFAULT_WEEKLY_BUDGET=500.00
DEFAULT_MONTHLY_BUDGET=2000.00

# Alert configuration
ENABLE_EMAIL_ALERTS=true
EMAIL_ALERT_RECIPIENTS=admin@example.com,alerts@example.com
EMAIL_FROM_ADDRESS=noreply@example.com

ENABLE_SLACK_ALERTS=true
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

# Alert behavior
ALERT_COOLDOWN_MINUTES=60
ALERT_RATE_LIMIT_MINUTES=60

# Analytics
ANOMALY_SENSITIVITY=2.0
TREND_LOOKBACK_DAYS=30

# Feature toggle
ENABLE_COST_MONITORING=true
```

### Configuration Object

```python
from receipt_label.utils.cost_monitoring import CostMonitoringConfig

# Load from environment
config = CostMonitoringConfig.from_env()

# Or configure manually
config = CostMonitoringConfig(
    default_daily_budget=Decimal("150.00"),
    alert_thresholds=[
        (50, ThresholdLevel.INFO),
        (80, ThresholdLevel.WARNING),
        (95, ThresholdLevel.CRITICAL),
        (100, ThresholdLevel.EXCEEDED),
    ],
    enable_email_alerts=True,
    email_recipients=["team@example.com"],
)
```

## Budget Management

### Budget Scopes

Budgets can be set at different levels:

- **User**: `user:john-doe` - Track individual user spending
- **Service**: `service:openai` - Track spending per AI service
- **Job**: `job:batch-123` - Track specific job/batch costs
- **Global**: `global:all` - Overall spending limit
- **Custom**: Any scope pattern you define

### Creating Budgets

```python
from decimal import Decimal
from receipt_label.utils.cost_monitoring import (
    BudgetManager,
    BudgetPeriod
)

budget_manager = BudgetManager(dynamo_client)

# Create a budget with custom thresholds
budget = budget_manager.create_budget(
    scope="user:premium-user",
    amount=Decimal("200.00"),
    period=BudgetPeriod.DAILY,
    alert_thresholds=[60, 85, 95, 100],  # Custom thresholds
    rollover_enabled=True,  # Unused budget rolls to next period
    metadata={
        "department": "engineering",
        "project": "chatbot",
        "cost_center": "R&D",
    }
)
```

### Budget Templates

Use templates for automatic budget creation:

```python
from receipt_label.utils.cost_monitoring import (
    BudgetTemplate,
    BudgetTemplateManager,
)

template_manager = BudgetTemplateManager()

# Add template for all users
template_manager.add_template(
    BudgetTemplate(
        scope_pattern="user:*",
        amount=Decimal("50.00"),
        period=BudgetPeriod.DAILY,
        alert_thresholds=[50, 80, 95, 100],
        metadata={"type": "standard_user"},
    )
)

# Automatically create budget when new user is detected
if not budget_manager.get_active_budget("user:new-user"):
    template_manager.create_budget_if_needed(
        budget_manager,
        "user:new-user"
    )
```

### Updating Budgets

```python
# Increase budget limit
updated_budget = budget_manager.update_budget(
    budget_id=budget.budget_id,
    amount=Decimal("300.00"),
    metadata_updates={"reason": "increased workload"},
)

# Change alert thresholds
budget_manager.update_budget(
    budget_id=budget.budget_id,
    alert_thresholds=[70, 85, 95, 100],
)

# Deactivate budget
budget_manager.deactivate_budget(budget.budget_id)
```

## Alert Configuration

### Alert Channels

Configure multiple alert channels:

```python
from receipt_label.utils.cost_monitoring import (
    AlertChannel,
    AlertManager,
    ThresholdLevel,
)

channels = [
    # Email for warnings and above
    AlertChannel(
        channel_type="email",
        destination="ops@example.com",
        enabled=True,
        min_level=ThresholdLevel.WARNING,
    ),

    # Slack for all alerts
    AlertChannel(
        channel_type="slack",
        destination="https://hooks.slack.com/...",
        enabled=True,
        min_level=ThresholdLevel.INFO,
    ),

    # Critical alerts to on-call
    AlertChannel(
        channel_type="email",
        destination="oncall@example.com",
        enabled=True,
        min_level=ThresholdLevel.CRITICAL,
    ),
]

alert_manager = AlertManager(
    channels=channels,
    rate_limit_minutes=30,  # Prevent alert spam
)
```

### Alert Types

1. **INFO (50%)**: Informational, halfway through budget
2. **WARNING (80%)**: Action needed soon
3. **CRITICAL (95%)**: Immediate action required
4. **EXCEEDED (100%)**: Budget limit exceeded

### Custom Alert Handling

```python
# Implement custom alert sender
class WebhookAlertSender(AlertSender):
    async def send(self, alert: ThresholdAlert, destination: str) -> bool:
        # Send to custom webhook
        response = await httpx.post(
            destination,
            json=alert.to_dict(),
        )
        return response.status_code == 200

# Register custom sender
alert_manager.senders["webhook"] = WebhookAlertSender()
```

## Cost Analytics

### Trend Analysis

```python
from receipt_label.utils.cost_monitoring import CostAnalytics

analytics = CostAnalytics(dynamo_client)

# Analyze spending trends
trend = analytics.analyze_trends(
    scope="service:openai",
    period="daily",
    lookback_days=30,
    forecast_days=7,
)

print(f"Trend: {trend.direction.value}")
print(f"Change: {trend.change_percent:.1f}%")
print(f"Current: ${trend.current_value}")
print(f"Forecast: ${trend.forecast_value}")
```

### Anomaly Detection

```python
# Detect unusual spending patterns
anomalies = analytics.detect_anomalies(
    scope="global:all",
    sensitivity=2.0,  # Standard deviations
    lookback_days=30,
)

for anomaly in anomalies:
    print(f"{anomaly.timestamp}: {anomaly.service}")
    print(f"  Expected: ${anomaly.expected_cost}")
    print(f"  Actual: ${anomaly.actual_cost}")
    print(f"  Severity: {anomaly.severity}")
    print(f"  Causes: {', '.join(anomaly.possible_causes)}")
```

### Optimization Recommendations

```python
# Get cost optimization suggestions
recommendations = analytics.generate_optimization_recommendations(
    scope="global:all",
    lookback_days=30,
)

for rec in recommendations:
    print(f"{rec.category}: Save ${rec.potential_savings}")
    print(f"Effort: {rec.implementation_effort}")
    print(f"Description: {rec.description}")
    print("Actions:")
    for action in rec.specific_actions:
        print(f"  - {action}")
```

### Cost Reports

```python
# Generate comprehensive cost report
report = analytics.generate_cost_report(
    scope="global:all",
    start_date="2024-01-01",
    end_date="2024-01-31",
    group_by="service",  # or "model", "user", etc.
)

print(f"Total Cost: ${report['summary']['total_cost']}")
print(f"Total Requests: {report['summary']['total_requests']}")

print("\nBreakdown by Service:")
for service, data in report['breakdown'].items():
    print(f"  {service}: ${data['cost']} ({data['percentage']:.1f}%)")

print("\nTop Users:")
for user_data in report['top_users'][:5]:
    print(f"  {user_data['user_id']}: ${user_data['cost']}")
```

## Integration with Context Managers

The cost monitoring system integrates seamlessly with Phase 3 context managers:

```python
from receipt_label.utils.ai_usage_context import ai_usage_context

# Context automatically propagates to cost monitoring
with ai_usage_context("batch_processing", job_id="job-123"):
    # All AI calls within this context are tracked
    # with job_id for budget checking
    for item in items:
        response = tracked_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": item}]
        )
        # Automatically checked against job:job-123 budget
```

## Best Practices

### 1. Budget Hierarchy

Set budgets at multiple levels for defense in depth:

```python
# Global limit
budget_manager.create_budget(
    scope="global:all",
    amount=Decimal("5000.00"),
    period=BudgetPeriod.MONTHLY,
)

# Service limits
for service in ["openai", "anthropic"]:
    budget_manager.create_budget(
        scope=f"service:{service}",
        amount=Decimal("2000.00"),
        period=BudgetPeriod.MONTHLY,
    )

# User limits
budget_manager.create_budget(
    scope="user:john-doe",
    amount=Decimal("100.00"),
    period=BudgetPeriod.DAILY,
)
```

### 2. Alert Escalation

Configure alerts with escalation:

```python
channels = [
    # Team notifications
    AlertChannel("slack", "team-channel", min_level=ThresholdLevel.INFO),

    # Manager notifications
    AlertChannel("email", "manager@example.com", min_level=ThresholdLevel.WARNING),

    # Executive notifications
    AlertChannel("email", "cto@example.com", min_level=ThresholdLevel.CRITICAL),
]
```

### 3. Cost Attribution

Use metadata for detailed cost attribution:

```python
with ai_usage_context(
    "analysis",
    job_id="job-123",
    metadata={
        "department": "marketing",
        "campaign": "Q1-2024",
        "cost_center": "MKT-001",
    }
):
    # Costs are tagged with metadata for reporting
    response = tracked_client.chat.completions.create(...)
```

### 4. Monitoring Dashboard

Create a monitoring dashboard:

```python
# Regular monitoring script
async def monitor_costs():
    # Check all active budgets
    for scope in ["global:all", "service:openai", "service:anthropic"]:
        budget = budget_manager.get_active_budget(scope)
        if not budget:
            continue

        current_spend = cost_monitor.get_current_spend(
            scope,
            budget.period.value
        )
        usage_percent = float(current_spend / budget.amount * 100)

        print(f"{scope}: ${current_spend:.2f} / ${budget.amount} ({usage_percent:.1f}%)")

        # Check for optimization opportunities
        if usage_percent > 70:
            recommendations = analytics.generate_optimization_recommendations(
                scope=scope,
                lookback_days=7,
            )
            if recommendations:
                print(f"  Optimization available: Save ${sum(r.potential_savings for r in recommendations):.2f}")
```

## Troubleshooting

### Common Issues

1. **Alerts not being sent**
   - Check alert channel configuration
   - Verify alert cooldown hasn't silenced alerts
   - Check minimum alert level settings

2. **Budget not found**
   - Ensure budget is active and not expired
   - Check scope format is correct
   - Verify budget period matches query

3. **Cost calculations incorrect**
   - Verify cost calculator has latest pricing
   - Check token counting is accurate
   - Ensure all services are tracked

### Debug Logging

Enable debug logging:

```python
import logging

logging.getLogger("receipt_label.utils.cost_monitoring").setLevel(logging.DEBUG)
```

### Manual Budget Check

Test budget checking manually:

```python
# Create test metric
test_metric = AIUsageMetric(
    service="openai",
    model="gpt-4",
    operation="completion",
    timestamp=datetime.now(timezone.utc),
    cost_usd=Decimal("50.00"),
    user_id="test-user",
)

# Check against budget
alert = cost_monitor.check_budget_threshold(
    current_usage=test_metric,
    budget_limit=Decimal("100.00"),
    scope="user:test-user",
    period="daily",
)

if alert:
    print(f"Alert triggered: {alert.message}")
```

## Migration Guide

To add cost monitoring to existing AI usage tracking:

1. **Update tracker creation**:
```python
# Old
tracker = AIUsageTracker(dynamo_client)

# New
tracker = create_cost_monitored_tracker(
    dynamo_client=dynamo_client,
    enable_email_alerts=True,
    email_addresses=["alerts@example.com"],
)
```

2. **Create initial budgets**:
```python
# Use templates for bulk creation
template_manager = BudgetTemplateManager()
template_manager.add_default_templates(config)

# Create budgets for existing users
for user_id in existing_users:
    template_manager.create_budget_if_needed(
        budget_manager,
        f"user:{user_id}"
    )
```

3. **Update wrapped clients**:
```python
# Existing wrapped clients work automatically
# Just ensure using the cost-aware tracker
tracked_client = AIUsageTracker.create_wrapped_openai_client(
    openai_client,
    tracker  # This is now cost-aware
)
```

## Performance Considerations

The cost monitoring system is designed for minimal overhead:

- **Budget checks**: < 5ms per operation
- **Alert delivery**: Asynchronous, non-blocking
- **Analytics queries**: Optimized DynamoDB access patterns
- **Memory usage**: Minimal with streaming aggregations

For high-volume applications:

1. Adjust alert cooldown to reduce notification overhead
2. Use batch processing for analytics queries
3. Consider caching budget lookups for frequently accessed scopes
4. Run analytics during off-peak hours

## Security

- **Alert destinations**: Validate webhook URLs and email addresses
- **Budget access**: Implement access controls for budget management
- **Sensitive data**: Don't include sensitive data in alert messages
- **Audit trail**: All budget changes are logged with metadata

## Conclusion

The AI Usage Cost Monitoring system provides comprehensive cost control with minimal integration effort. By following this guide, you can implement effective cost management for your AI-powered applications with real-time monitoring, intelligent alerts, and actionable insights.
