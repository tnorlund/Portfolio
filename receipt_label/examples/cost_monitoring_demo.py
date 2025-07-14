"""
Comprehensive example demonstrating the cost monitoring system.

This example shows how to:
1. Set up budgets for different scopes
2. Track AI usage with automatic budget checking
3. Receive alerts when thresholds are crossed
4. Analyze spending trends and anomalies
5. Generate cost reports and optimization recommendations
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from receipt_dynamo import DynamoClient

from receipt_label.utils.ai_usage_tracker import AIUsageTracker
from receipt_label.utils.cost_monitoring import (
    AlertChannel,
    AlertManager,
    Budget,
    BudgetManager,
    BudgetPeriod,
    CostAnalytics,
    CostAwareAIUsageTracker,
    CostMonitor,
    create_cost_monitored_tracker,
)


async def main():
    """Run cost monitoring demonstration."""

    # Initialize DynamoDB client
    dynamo_client = DynamoClient(
        table_name=os.environ.get("DYNAMODB_TABLE_NAME", "AIUsageMetrics")
    )

    print("=== AI Usage Cost Monitoring Demo ===\n")

    # 1. Set up budget management
    print("1. Setting up budgets...")
    budget_manager = BudgetManager(dynamo_client)

    # Create daily budget for a user
    user_budget = budget_manager.create_budget(
        scope="user:demo-user",
        amount=Decimal("50.00"),
        period=BudgetPeriod.DAILY,
        alert_thresholds=[50, 80, 95, 100],
        metadata={"department": "engineering", "project": "demo"},
    )
    print(
        f"   ✓ Created daily budget: ${user_budget.amount} for {user_budget.scope}"
    )

    # Create monthly budget for OpenAI service
    service_budget = budget_manager.create_budget(
        scope="service:openai",
        amount=Decimal("1000.00"),
        period=BudgetPeriod.MONTHLY,
        alert_thresholds=[60, 85, 95, 100],
        rollover_enabled=True,
    )
    print(
        f"   ✓ Created monthly budget: ${service_budget.amount} for {service_budget.scope}"
    )

    # Create global daily budget
    global_budget = budget_manager.create_budget(
        scope="global:all",
        amount=Decimal("200.00"),
        period=BudgetPeriod.DAILY,
        metadata={"purpose": "cost_control"},
    )
    print(f"   ✓ Created global daily budget: ${global_budget.amount}")

    # 2. Set up alert channels
    print("\n2. Configuring alert channels...")

    alert_channels = [
        # Email alerts for critical issues
        AlertChannel(
            channel_type="email",
            destination="admin@example.com",
            enabled=True,
            min_level="WARNING",
            metadata={"recipient": "Admin Team"},
        ),
        # Slack for all alerts
        AlertChannel(
            channel_type="slack",
            destination=os.environ.get(
                "SLACK_WEBHOOK_URL", "https://hooks.slack.com/demo"
            ),
            enabled=True,
            min_level="INFO",
        ),
    ]

    alert_manager = AlertManager(alert_channels)
    print("   ✓ Configured email and Slack alerts")

    # 3. Create cost-aware AI usage tracker
    print("\n3. Creating cost-monitored AI tracker...")

    tracker = create_cost_monitored_tracker(
        dynamo_client=dynamo_client,
        alert_channels=alert_channels,
        table_name=dynamo_client.table_name,
        user_id="demo-user",
        track_to_dynamo=True,
        track_to_file=True,  # Also log to file for demo
        log_file="/tmp/ai_usage_demo.jsonl",  # nosec B108 - temp file for demo only
    )

    print("   ✓ Tracker created with automatic budget checking")

    # 4. Simulate AI usage that triggers alerts
    print("\n4. Simulating AI usage...")

    # Create a wrapped OpenAI client
    from openai import OpenAI

    openai_client = OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY", "demo-key")
    )
    tracked_client = AIUsageTracker.create_wrapped_openai_client(
        openai_client, tracker
    )

    # Simulate some usage (in real scenario, these would be actual API calls)
    print("   - Simulating API calls...")

    # Mock some metrics to demonstrate the system
    from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric

    # Simulate normal usage
    for i in range(5):
        metric = AIUsageMetric(
            service="openai",
            model="gpt-3.5-turbo",
            operation="completion",
            timestamp=datetime.now(timezone.utc) - timedelta(hours=i),
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            cost_usd=Decimal("0.50"),
            user_id="demo-user",
            metadata={"request_type": "normal", "demo": True},
        )
        tracker._store_metric(metric)

    print("   ✓ Normal usage recorded: 5 requests, $2.50 total")

    # Simulate spike that triggers warning (80% threshold)
    expensive_metric = AIUsageMetric(
        service="openai",
        model="gpt-4",
        operation="completion",
        timestamp=datetime.now(timezone.utc),
        input_tokens=1000,
        output_tokens=500,
        total_tokens=1500,
        cost_usd=Decimal("40.00"),  # This will trigger 80% alert
        user_id="demo-user",
        metadata={"request_type": "expensive", "demo": True},
    )
    tracker._store_metric(expensive_metric)

    print("   ✓ Expensive request recorded: $40.00 (should trigger alert)")

    # Give async alerts time to process
    await asyncio.sleep(1)

    # 5. Cost analytics
    print("\n5. Running cost analytics...")

    analytics = CostAnalytics(dynamo_client)

    # Analyze trends
    trend = analytics.analyze_trends(
        scope="user:demo-user",
        period="daily",
        lookback_days=7,
        forecast_days=3,
    )

    print(f"   ✓ Trend Analysis:")
    print(f"     - Direction: {trend.direction.value}")
    print(f"     - Change: {trend.change_percent:.1f}%")
    print(f"     - Current: ${trend.current_value}")
    if trend.forecast_value:
        print(f"     - Forecast: ${trend.forecast_value}")

    # Detect anomalies
    anomalies = analytics.detect_anomalies(
        scope="global:all",
        sensitivity=2.0,
        lookback_days=7,
    )

    if anomalies:
        print(f"   ✓ Detected {len(anomalies)} anomalies:")
        for anomaly in anomalies[:2]:  # Show first 2
            print(
                f"     - {anomaly.service}: ${anomaly.actual_cost} "
                f"(expected ${anomaly.expected_cost})"
            )

    # Generate optimization recommendations
    recommendations = analytics.generate_optimization_recommendations(
        scope="global:all",
        lookback_days=30,
    )

    if recommendations:
        print(f"   ✓ Optimization Recommendations:")
        for rec in recommendations[:2]:  # Show top 2
            print(f"     - {rec.category}: Save ${rec.potential_savings}")
            print(f"       {rec.description}")

    # 6. Generate cost report
    print("\n6. Generating cost report...")

    report = analytics.generate_cost_report(
        scope="global:all",
        start_date=(datetime.now(timezone.utc) - timedelta(days=7)).strftime(
            "%Y-%m-%d"
        ),
        end_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        group_by="service",
    )

    print(f"   ✓ Cost Report Summary:")
    print(f"     - Total Cost: ${report['summary']['total_cost']}")
    print(f"     - Total Requests: {report['summary']['total_requests']}")
    print(f"     - Service Breakdown:")

    for service, data in report["breakdown"].items():
        print(
            f"       • {service}: ${data['cost']} ({data['percentage']:.1f}%)"
        )

    # 7. Budget status check
    print("\n7. Checking budget status...")

    cost_monitor = CostMonitor(dynamo_client)

    for scope in ["user:demo-user", "service:openai", "global:all"]:
        current_spend = cost_monitor.get_current_spend(scope, "daily")
        budget = budget_manager.get_active_budget(scope)

        if budget:
            usage_percent = float(current_spend / budget.amount * 100)
            print(
                f"   • {scope}: ${current_spend:.2f} / ${budget.amount} ({usage_percent:.1f}%)"
            )

    # 8. Demonstrate budget update
    print("\n8. Updating budget...")

    updated_budget = budget_manager.update_budget(
        budget_id=user_budget.budget_id,
        amount=Decimal("75.00"),
        metadata_updates={"reason": "increased workload"},
    )

    print(
        f"   ✓ Updated user budget from ${user_budget.amount} to ${updated_budget.amount}"
    )

    # 9. Cost breakdown by service
    print("\n9. Cost breakdown by service...")

    breakdown = cost_monitor.get_cost_breakdown("global:all", "daily")

    for service, cost in breakdown.items():
        print(f"   • {service}: ${cost:.2f}")

    print("\n=== Demo Complete ===")
    print("\nKey Features Demonstrated:")
    print("✓ Budget creation and management")
    print("✓ Automatic threshold monitoring")
    print("✓ Multi-channel alert delivery")
    print("✓ Cost trend analysis")
    print("✓ Anomaly detection")
    print("✓ Optimization recommendations")
    print("✓ Comprehensive reporting")
    print("\nCheck /tmp/ai_usage_demo.jsonl for detailed usage logs")


if __name__ == "__main__":
    # Run the demo
    asyncio.run(main())
