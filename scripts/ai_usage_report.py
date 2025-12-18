#!/usr/bin/env python3
"""
AI Usage Report - View AI service costs and usage metrics.

Usage:
    python scripts/ai_usage_report.py [--days 30] [--service openai]
"""
import argparse
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import boto3
import requests
from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table

console = Console()


def get_api_base_url() -> str:
    """Get the API base URL from environment or default."""
    return os.environ.get("API_BASE_URL", "https://api.tnorlund.com")


def fetch_usage_data(days: int = 30, service: Optional[str] = None) -> Dict:
    """Fetch usage data from the API."""
    base_url = get_api_base_url()

    # Calculate date range
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    # Build query parameters
    params = {
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "aggregation": "day,service,model,operation",
    }

    if service:
        params["service"] = service

    # Make API request
    url = f"{base_url}/ai_usage"

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        console.print(f"[red]Error fetching data from API: {e}[/red]")
        return None


def fetch_usage_data_direct(days: int = 30, service: Optional[str] = None) -> Dict:
    """Fetch usage data directly from DynamoDB (for local development)."""
    try:
        import sys

        sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

        from receipt_dynamo import DynamoClient
        from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric

        # Get DynamoDB client
        table_name = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable")
        dynamo_client = DynamoClient(table_name)

        # Calculate date range
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days)

        # Query metrics
        all_metrics = []
        services = [service] if service else ["openai", "anthropic", "google_places"]

        for svc in services:
            metrics = AIUsageMetric.query_by_service_date(
                dynamo_client,
                service=svc,
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
            )
            all_metrics.extend(metrics)

        # Convert to API response format
        return aggregate_metrics_local(all_metrics)

    except Exception as e:
        console.print(f"[red]Error fetching data from DynamoDB: {e}[/red]")
        return None


def aggregate_metrics_local(metrics: List) -> Dict:
    """Aggregate metrics locally (matches API response format)."""
    total_cost = sum(m.cost_usd or 0 for m in metrics)
    total_tokens = sum(m.total_tokens or 0 for m in metrics)

    # Aggregate by service
    by_service = {}
    by_day = {}
    by_model = {}

    for metric in metrics:
        # By service
        if metric.service not in by_service:
            by_service[metric.service] = {
                "cost_usd": 0,
                "tokens": 0,
                "api_calls": 0,
            }
        by_service[metric.service]["cost_usd"] += metric.cost_usd or 0
        by_service[metric.service]["tokens"] += metric.total_tokens or 0
        by_service[metric.service]["api_calls"] += 1

        # By day
        if metric.date not in by_day:
            by_day[metric.date] = {"cost_usd": 0, "tokens": 0, "api_calls": 0}
        by_day[metric.date]["cost_usd"] += metric.cost_usd or 0
        by_day[metric.date]["tokens"] += metric.total_tokens or 0
        by_day[metric.date]["api_calls"] += 1

        # By model
        if metric.model not in by_model:
            by_model[metric.model] = {
                "cost_usd": 0,
                "tokens": 0,
                "api_calls": 0,
                "service": metric.service,
            }
        by_model[metric.model]["cost_usd"] += metric.cost_usd or 0
        by_model[metric.model]["tokens"] += metric.total_tokens or 0
        by_model[metric.model]["api_calls"] += 1

    return {
        "summary": {
            "total_cost_usd": round(total_cost, 4),
            "total_tokens": total_tokens,
            "total_api_calls": len(metrics),
        },
        "aggregations": {
            "by_service": by_service,
            "by_day": by_day,
            "by_model": by_model,
        },
    }


def display_summary(data: Dict):
    """Display summary panel."""
    summary = data.get("summary", {})

    summary_text = f"""
[bold]Total Cost:[/bold] ${summary.get('total_cost_usd', 0):.2f}
[bold]Total API Calls:[/bold] {summary.get('total_api_calls', 0):,}
[bold]Total Tokens:[/bold] {summary.get('total_tokens', 0):,}
[bold]Avg Cost/Call:[/bold] ${summary.get('average_cost_per_call', 0):.4f}
"""

    console.print(Panel(summary_text, title="📊 AI Usage Summary", box=box.ROUNDED))


def display_service_breakdown(data: Dict):
    """Display service breakdown table."""
    by_service = data.get("aggregations", {}).get("by_service", {})

    if not by_service:
        return

    table = Table(title="💰 Cost by Service", box=box.ROUNDED)
    table.add_column("Service", style="cyan")
    table.add_column("Cost (USD)", justify="right", style="green")
    table.add_column("API Calls", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Avg Cost/Call", justify="right")

    total_cost = 0
    for service, stats in sorted(by_service.items()):
        cost = stats.get("cost_usd", 0)
        calls = stats.get("api_calls", 0)
        tokens = stats.get("tokens", 0)
        avg_cost = cost / calls if calls > 0 else 0
        total_cost += cost

        table.add_row(
            service.capitalize(),
            f"${cost:.2f}",
            f"{calls:,}",
            f"{tokens:,}",
            f"${avg_cost:.4f}",
        )

    table.add_row(
        "[bold]Total[/bold]",
        f"[bold]${total_cost:.2f}[/bold]",
        "",
        "",
        "",
        style="bold yellow",
    )

    console.print(table)


def display_model_breakdown(data: Dict):
    """Display model breakdown table."""
    by_model = data.get("aggregations", {}).get("by_model", {})

    if not by_model:
        return

    table = Table(title="🤖 Cost by Model", box=box.ROUNDED)
    table.add_column("Model", style="cyan")
    table.add_column("Service", style="blue")
    table.add_column("Cost (USD)", justify="right", style="green")
    table.add_column("API Calls", justify="right")
    table.add_column("Avg Tokens/Call", justify="right")

    # Sort by cost descending
    sorted_models = sorted(
        by_model.items(), key=lambda x: x[1].get("cost_usd", 0), reverse=True
    )

    for model, stats in sorted_models[:10]:  # Top 10 models
        cost = stats.get("cost_usd", 0)
        calls = stats.get("api_calls", 0)
        tokens = stats.get("tokens", 0)
        avg_tokens = tokens / calls if calls > 0 else 0

        table.add_row(
            model,
            stats.get("service", "unknown"),
            f"${cost:.2f}",
            f"{calls:,}",
            f"{avg_tokens:.0f}",
        )

    console.print(table)


def display_daily_trend(data: Dict, days: int):
    """Display daily cost trend."""
    by_day = data.get("aggregations", {}).get("by_day", {})

    if not by_day:
        return

    # Get last N days
    sorted_days = sorted(by_day.items())[-min(days, 14) :]  # Show max 14 days

    table = Table(title="📈 Daily Cost Trend", box=box.ROUNDED)
    table.add_column("Date", style="cyan")
    table.add_column("Cost (USD)", justify="right", style="green")
    table.add_column("API Calls", justify="right")
    table.add_column("Chart", justify="left")

    # Find max cost for scaling
    max_cost = (
        max(stats.get("cost_usd", 0) for _, stats in sorted_days) if sorted_days else 1
    )

    for date, stats in sorted_days:
        cost = stats.get("cost_usd", 0)
        calls = stats.get("api_calls", 0)

        # Create simple bar chart
        bar_width = int((cost / max_cost) * 30) if max_cost > 0 else 0
        bar = "█" * bar_width

        table.add_row(date, f"${cost:.2f}", f"{calls:,}", f"[blue]{bar}[/blue]")

    console.print(table)


def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="AI Usage Report")
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days to report (default: 30)",
    )
    parser.add_argument(
        "--service",
        choices=["openai", "anthropic", "google_places"],
        help="Filter by specific service",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Fetch data directly from DynamoDB (for local development)",
    )

    args = parser.parse_args()

    # Print header
    console.print(f"\n[bold cyan]AI Usage Report - Last {args.days} Days[/bold cyan]\n")

    # Fetch data
    if args.local:
        data = fetch_usage_data_direct(args.days, args.service)
    else:
        data = fetch_usage_data(args.days, args.service)

    if not data:
        console.print("[red]No data available[/red]")
        return

    # Display reports
    display_summary(data)
    console.print()
    display_service_breakdown(data)
    console.print()
    display_model_breakdown(data)
    console.print()
    display_daily_trend(data, args.days)


if __name__ == "__main__":
    # Check if rich is installed
    try:
        import rich
    except ImportError:
        print("Please install rich: pip install rich")
        exit(1)

    main()
