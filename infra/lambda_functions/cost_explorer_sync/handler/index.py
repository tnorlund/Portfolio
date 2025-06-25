"""
Lambda handler for syncing AI costs using AWS Cost Explorer.
This tracks costs for AWS-based AI services (Bedrock, SageMaker, etc.)
and estimates for API Gateway calls to external services.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict, context) -> Dict:
    """
    Sync cost data from AWS Cost Explorer and CloudWatch Logs.
    """
    try:
        # Initialize clients
        ce_client = boto3.client("ce")  # Cost Explorer
        logs_client = boto3.client("logs")
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(os.environ["DYNAMODB_TABLE_NAME"])

        # Get costs for the last 7 days
        end_date = datetime.utcnow().date()
        start_date = end_date - timedelta(days=7)

        results = {
            "aws_ai_costs": None,
            "api_gateway_metrics": None,
            "cloudwatch_insights": None,
            "errors": [],
        }

        # 1. Get AWS AI service costs (Bedrock, Textract, etc.)
        try:
            results["aws_ai_costs"] = get_aws_ai_costs(
                ce_client, start_date, end_date, table
            )
        except Exception as e:
            logger.error(f"Failed to get AWS AI costs: {e}")
            results["errors"].append(f"AWS AI costs: {str(e)}")

        # 2. Get API Gateway metrics (for external API calls)
        try:
            results["api_gateway_metrics"] = get_api_gateway_metrics(
                logs_client, start_date, end_date, table
            )
        except Exception as e:
            logger.error(f"Failed to get API Gateway metrics: {e}")
            results["errors"].append(f"API Gateway: {str(e)}")

        # 3. Analyze CloudWatch Logs for API patterns
        try:
            results["cloudwatch_insights"] = analyze_api_logs(
                logs_client, start_date, end_date, table
            )
        except Exception as e:
            logger.error(f"Failed to analyze logs: {e}")
            results["errors"].append(f"Log analysis: {str(e)}")

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Cost sync completed",
                    "results": results,
                    "period": {
                        "start": start_date.isoformat(),
                        "end": end_date.isoformat(),
                    },
                },
                default=str,
            ),
        }

    except Exception as e:
        logger.error(f"Cost sync failed: {e}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def get_aws_ai_costs(ce_client, start_date, end_date, table) -> Dict:
    """
    Get costs for AWS AI services using Cost Explorer.
    """
    # Services to track
    ai_services = [
        "Amazon Bedrock",
        "Amazon Textract",
        "Amazon Comprehend",
        "Amazon Rekognition",
        "Amazon Polly",
        "Amazon Transcribe",
        "Amazon Translate",
        "AWS Lambda",  # For Lambda-based AI workloads
    ]

    response = ce_client.get_cost_and_usage(
        TimePeriod={
            "Start": start_date.strftime("%Y-%m-%d"),
            "End": end_date.strftime("%Y-%m-%d"),
        },
        Granularity="DAILY",
        Metrics=["UnblendedCost", "UsageQuantity"],
        GroupBy=[
            {"Type": "DIMENSION", "Key": "SERVICE"},
            {"Type": "DIMENSION", "Key": "USAGE_TYPE"},
        ],
        Filter={"Dimensions": {"Key": "SERVICE", "Values": ai_services}},
    )

    # Process and store costs
    total_cost = 0
    service_costs = {}

    for result in response["ResultsByTime"]:
        date = result["TimePeriod"]["Start"]

        for group in result.get("Groups", []):
            service = group["Keys"][0]  # Service name
            usage_type = group["Keys"][1]  # Usage type
            cost = float(group["Metrics"]["UnblendedCost"]["Amount"])

            if cost > 0:
                # Store in DynamoDB
                store_aws_cost(table, date, service, usage_type, cost)

                # Aggregate
                if service not in service_costs:
                    service_costs[service] = 0
                service_costs[service] += cost
                total_cost += cost

    return {
        "total_cost": round(total_cost, 4),
        "service_breakdown": service_costs,
        "days_analyzed": (end_date - start_date).days,
    }


def get_api_gateway_metrics(logs_client, start_date, end_date, table) -> Dict:
    """
    Analyze API Gateway logs to estimate external API usage.
    """
    # Query CloudWatch Logs Insights for API Gateway logs
    log_group = "/aws/apigateway/my-api"  # Your API Gateway log group

    query = """
    fields @timestamp, @message
    | filter @message like /ai_usage|process|receipts/
    | stats count() by bin(1h) as time_window
    """

    try:
        response = logs_client.start_query(
            logGroupName=log_group,
            startTime=int(start_date.timestamp() * 1000),
            endTime=int(end_date.timestamp() * 1000),
            queryString=query,
        )

        query_id = response["queryId"]

        # Wait for query to complete
        status = "Running"
        while status == "Running":
            response = logs_client.get_query_results(queryId=query_id)
            status = response["status"]

        # Process results
        api_calls = 0
        for result in response.get("results", []):
            for field in result:
                if field["field"] == "count()":
                    api_calls += int(field["value"])

        return {
            "total_api_calls": api_calls,
            "log_group": log_group,
            "status": "success",
        }

    except Exception as e:
        logger.error(f"CloudWatch Logs query failed: {e}")
        return {"status": "error", "error": str(e)}


def analyze_api_logs(logs_client, start_date, end_date, table) -> Dict:
    """
    Deep analysis of Lambda logs to identify AI API calls.
    """
    # Look for patterns in Lambda logs that indicate AI API usage
    lambda_log_groups = [
        "/aws/lambda/api_process_GET_lambda",
        "/aws/lambda/receipt_processor_lambda",
        "/aws/lambda/merchant_validation_lambda",
    ]

    insights = {"openai_calls": 0, "anthropic_calls": 0, "google_calls": 0}

    # Patterns to search for
    patterns = {
        "openai": "openai.com|gpt-|embedding",
        "anthropic": "anthropic|claude",
        "google": "googleapis.com|places",
    }

    for log_group in lambda_log_groups:
        for service, pattern in patterns.items():
            try:
                query = f"""
                fields @timestamp, @message
                | filter @message like /{pattern}/
                | stats count() as api_calls
                """

                response = logs_client.start_query(
                    logGroupName=log_group,
                    startTime=int(start_date.timestamp() * 1000),
                    endTime=int(end_date.timestamp() * 1000),
                    queryString=query,
                )

                # Wait and get results (simplified - add proper polling)
                query_id = response["queryId"]
                # ... wait for completion ...

                insights[f"{service}_calls"] += 1  # Placeholder

            except Exception as e:
                logger.warning(f"Failed to query {log_group}: {e}")

    return insights


def store_aws_cost(table, date: str, service: str, usage_type: str, cost: float):
    """
    Store AWS cost data in DynamoDB.
    """
    timestamp = datetime.utcnow()

    item = {
        "PK": f"AWS_COST#{service}",
        "SK": f"COST#{date}#{usage_type}",
        "GSI1PK": "AWS_COST",
        "GSI1SK": f"DATE#{date}",
        "TYPE": "AWSCost",
        "service": service,
        "usageType": usage_type,
        "date": date,
        "costUSD": Decimal(str(cost)),
        "timestamp": timestamp.isoformat(),
        "source": "cost_explorer",
    }

    table.put_item(Item=item)


def estimate_external_api_costs(api_calls: Dict, table):
    """
    Estimate costs for external APIs based on call counts.
    """
    # Rough estimates based on average usage patterns
    estimates = {
        "openai": {
            "avg_tokens_per_call": 1000,
            "model": "gpt-3.5-turbo",
            "cost_per_1k_tokens": 0.002,
        },
        "anthropic": {
            "avg_tokens_per_call": 2000,
            "model": "claude-3-sonnet",
            "cost_per_1k_tokens": 0.018,  # Combined input/output
        },
        "google_places": {"cost_per_call": 0.017},
    }

    for service, count in api_calls.items():
        if count > 0 and service in estimates:
            est = estimates[service]

            if service == "google_places":
                estimated_cost = count * est["cost_per_call"]
            else:
                estimated_tokens = count * est["avg_tokens_per_call"]
                estimated_cost = (estimated_tokens / 1000) * est["cost_per_1k_tokens"]

            # Store estimate
            store_cost_estimate(table, service, count, estimated_cost)
