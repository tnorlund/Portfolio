"""
Lambda handler for AI usage metrics and cost analysis.
"""

import json
import os
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Attr, Key


def decimal_default(obj):
    """Convert Decimal to float for JSON serialization."""
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Get AI usage metrics and costs.

    Query parameters:
    - start_date: Start date (YYYY-MM-DD)
    - end_date: End date (YYYY-MM-DD)
    - service: Filter by service (openai, anthropic, google_places)
    - operation: Filter by operation (completion, embedding, code_review, etc.)
    - aggregation: Aggregation level (day, service, model, operation)
    """
    try:
        # Get DynamoDB client
        dynamodb = boto3.resource("dynamodb")
        table_name = os.environ["DYNAMODB_TABLE_NAME"]
        table = dynamodb.Table(table_name)

        # Parse query parameters
        query_params = event.get("queryStringParameters") or {}
        start_date = query_params.get(
            "start_date",
            (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d"),
        )
        end_date = query_params.get("end_date", datetime.utcnow().strftime("%Y-%m-%d"))
        service = query_params.get("service")
        operation = query_params.get("operation")
        aggregation = query_params.get("aggregation", "day").split(",")

        # Query AI usage metrics
        metrics = query_ai_usage_metrics(
            table,
            start_date=start_date,
            end_date=end_date,
            service=service,
            operation=operation,
        )

        # Aggregate the data
        summary = aggregate_metrics(metrics, aggregation)

        # Calculate totals
        total_cost = sum(m.get("costUSD", 0) for m in metrics)
        total_tokens = sum(m.get("totalTokens", 0) for m in metrics)
        total_calls = len(metrics)

        response_body = {
            "summary": {
                "total_cost_usd": round(total_cost, 4),
                "total_tokens": total_tokens,
                "total_api_calls": total_calls,
                "average_cost_per_call": (
                    round(total_cost / total_calls, 6) if total_calls > 0 else 0
                ),
                "date_range": {"start": start_date, "end": end_date},
            },
            "aggregations": summary,
            "query": {
                "service": service,
                "operation": operation,
                "aggregation": aggregation,
            },
        }

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps(response_body, default=decimal_default),
        }

    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps({"error": str(e)}),
        }


def query_ai_usage_metrics(
    table,
    start_date: str,
    end_date: str,
    service: Optional[str] = None,
    operation: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Query AI usage metrics from DynamoDB."""
    metrics = []

    if service:
        # Query specific service
        response = table.query(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1PK").eq(f"AI_USAGE#{service}")
            & Key("GSI1SK").between(f"DATE#{start_date}", f"DATE#{end_date}"),
        )
        items = response.get("Items", [])

        # Handle pagination
        while "LastEvaluatedKey" in response:
            response = table.query(
                IndexName="GSI1",
                KeyConditionExpression=Key("GSI1PK").eq(f"AI_USAGE#{service}")
                & Key("GSI1SK").between(f"DATE#{start_date}", f"DATE#{end_date}"),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items.extend(response.get("Items", []))

        # Add items to metrics list
        metrics.extend(items)
    else:
        # Query all services by date range
        services = ["openai", "anthropic", "google_places"]
        for svc in services:
            response = table.query(
                IndexName="GSI1",
                KeyConditionExpression=Key("GSI1PK").eq(f"AI_USAGE#{svc}")
                & Key("GSI1SK").between(f"DATE#{start_date}", f"DATE#{end_date}"),
            )
            items = response.get("Items", [])

            # Handle pagination
            while "LastEvaluatedKey" in response:
                response = table.query(
                    IndexName="GSI1",
                    KeyConditionExpression=Key("GSI1PK").eq(f"AI_USAGE#{svc}")
                    & Key("GSI1SK").between(f"DATE#{start_date}", f"DATE#{end_date}"),
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                items.extend(response.get("Items", []))

            metrics.extend(items)

    # Filter by operation if specified
    if operation:
        metrics = [m for m in metrics if m.get("operation") == operation]

    return metrics


def aggregate_metrics(
    metrics: List[Dict[str, Any]], aggregation_levels: List[str]
) -> Dict[str, Any]:
    """Aggregate metrics by specified levels."""
    result = {}

    if "day" in aggregation_levels:
        result["by_day"] = aggregate_by_day(metrics)

    if "service" in aggregation_levels:
        result["by_service"] = aggregate_by_service(metrics)

    if "model" in aggregation_levels:
        result["by_model"] = aggregate_by_model(metrics)

    if "operation" in aggregation_levels:
        result["by_operation"] = aggregate_by_operation(metrics)

    if "hour" in aggregation_levels:
        result["by_hour"] = aggregate_by_hour(metrics)

    return result


def aggregate_by_day(metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate metrics by day."""
    daily = {}

    for metric in metrics:
        date = metric.get("date", "unknown")
        if date not in daily:
            daily[date] = {
                "cost_usd": 0,
                "tokens": 0,
                "api_calls": 0,
                "services": set(),
            }

        daily[date]["cost_usd"] += float(metric.get("costUSD", 0))
        daily[date]["tokens"] += int(metric.get("totalTokens", 0))
        daily[date]["api_calls"] += 1
        daily[date]["services"].add(metric.get("service", "unknown"))

    # Convert sets to lists for JSON serialization
    for date in daily:
        daily[date]["services"] = list(daily[date]["services"])
        daily[date]["cost_usd"] = round(daily[date]["cost_usd"], 4)

    return daily


def aggregate_by_service(metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate metrics by service."""
    by_service = {}

    for metric in metrics:
        service = metric.get("service", "unknown")
        if service not in by_service:
            by_service[service] = {
                "cost_usd": 0,
                "tokens": 0,
                "api_calls": 0,
                "models": set(),
                "operations": set(),
            }

        by_service[service]["cost_usd"] += float(metric.get("costUSD", 0))
        by_service[service]["tokens"] += int(metric.get("totalTokens", 0))
        by_service[service]["api_calls"] += 1
        by_service[service]["models"].add(metric.get("model", "unknown"))
        by_service[service]["operations"].add(metric.get("operation", "unknown"))

    # Convert sets to lists and round costs
    for service in by_service:
        by_service[service]["models"] = list(by_service[service]["models"])
        by_service[service]["operations"] = list(by_service[service]["operations"])
        by_service[service]["cost_usd"] = round(by_service[service]["cost_usd"], 4)
        by_service[service]["average_cost_per_call"] = (
            round(
                by_service[service]["cost_usd"] / by_service[service]["api_calls"],
                6,
            )
            if by_service[service]["api_calls"] > 0
            else 0
        )

    return by_service


def aggregate_by_model(metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate metrics by model."""
    by_model = {}

    for metric in metrics:
        model = metric.get("model", "unknown")
        if model not in by_model:
            by_model[model] = {
                "cost_usd": 0,
                "tokens": 0,
                "api_calls": 0,
                "service": metric.get("service", "unknown"),
            }

        by_model[model]["cost_usd"] += float(metric.get("costUSD", 0))
        by_model[model]["tokens"] += int(metric.get("totalTokens", 0))
        by_model[model]["api_calls"] += 1

    # Round costs
    for model in by_model:
        by_model[model]["cost_usd"] = round(by_model[model]["cost_usd"], 4)
        by_model[model]["average_tokens_per_call"] = (
            round(by_model[model]["tokens"] / by_model[model]["api_calls"], 2)
            if by_model[model]["api_calls"] > 0
            else 0
        )

    return by_model


def aggregate_by_operation(metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate metrics by operation type."""
    by_operation = {}

    for metric in metrics:
        operation = metric.get("operation", "unknown")
        if operation not in by_operation:
            by_operation[operation] = {
                "cost_usd": 0,
                "tokens": 0,
                "api_calls": 0,
                "services": set(),
            }

        by_operation[operation]["cost_usd"] += float(metric.get("costUSD", 0))
        by_operation[operation]["tokens"] += int(metric.get("totalTokens", 0))
        by_operation[operation]["api_calls"] += 1
        by_operation[operation]["services"].add(metric.get("service", "unknown"))

    # Convert sets to lists and round costs
    for operation in by_operation:
        by_operation[operation]["services"] = list(by_operation[operation]["services"])
        by_operation[operation]["cost_usd"] = round(
            by_operation[operation]["cost_usd"], 4
        )

    return by_operation


def aggregate_by_hour(metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate metrics by hour of day."""
    by_hour = {}

    for metric in metrics:
        hour_str = metric.get("hour", "unknown")
        if hour_str != "unknown":
            # Extract hour from format "YYYY-MM-DD-HH"
            hour = hour_str.split("-")[-1] if "-" in hour_str else "00"

            if hour not in by_hour:
                by_hour[hour] = {"cost_usd": 0, "api_calls": 0}

            by_hour[hour]["cost_usd"] += float(metric.get("costUSD", 0))
            by_hour[hour]["api_calls"] += 1

    # Round costs
    for hour in by_hour:
        by_hour[hour]["cost_usd"] = round(by_hour[hour]["cost_usd"], 4)

    return by_hour
