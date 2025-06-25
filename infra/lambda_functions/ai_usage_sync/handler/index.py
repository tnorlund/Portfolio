"""
Lambda handler for syncing AI usage data from provider APIs.
Runs periodically to pull actual usage/costs from OpenAI, Anthropic, etc.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

import boto3
import requests
from openai import OpenAI

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict, context) -> Dict:
    """
    Sync AI usage data from provider APIs.
    This runs on a schedule (e.g., every hour) to pull actual usage data.
    """
    try:
        # Get DynamoDB client
        dynamodb = boto3.resource("dynamodb")
        table_name = os.environ["DYNAMODB_TABLE_NAME"]
        table = dynamodb.Table(table_name)

        results = {
            "openai": None,
            "anthropic": None,
            "google_cloud": None,
            "errors": [],
        }

        # Sync OpenAI usage
        try:
            results["openai"] = sync_openai_usage(table)
        except Exception as e:
            logger.error(f"Failed to sync OpenAI usage: {e}")
            results["errors"].append(f"OpenAI: {str(e)}")

        # Sync Anthropic usage (when API becomes available)
        try:
            results["anthropic"] = sync_anthropic_usage(table)
        except Exception as e:
            logger.error(f"Failed to sync Anthropic usage: {e}")
            results["errors"].append(f"Anthropic: {str(e)}")

        # Sync Google Cloud usage
        try:
            results["google_cloud"] = sync_google_cloud_usage(table)
        except Exception as e:
            logger.error(f"Failed to sync Google Cloud usage: {e}")
            results["errors"].append(f"Google Cloud: {str(e)}")

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Usage sync completed",
                    "results": results,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            ),
        }

    except Exception as e:
        logger.error(f"Usage sync failed: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps(
                {"error": str(e), "timestamp": datetime.utcnow().isoformat()}
            ),
        }


def sync_openai_usage(table) -> Dict:
    """
    Sync OpenAI usage data from their API.

    Note: OpenAI provides usage data through their API with some delay.
    We check the last 24 hours and update our records.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set")

    client = OpenAI(api_key=api_key)

    # Get organization ID for usage endpoint
    # Note: This is a simplified example. The actual OpenAI usage API
    # might require different authentication or endpoints
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Get usage for the last 24 hours
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=1)

    # OpenAI Usage API endpoint (this may change - check OpenAI docs)
    # Currently, OpenAI doesn't have a public usage API, so this is hypothetical
    # You might need to use their billing/usage dashboard data

    logger.info(f"Syncing OpenAI usage from {start_date} to {end_date}")

    # For now, return a placeholder
    # In reality, you'd parse the API response and store in DynamoDB
    return {
        "status": "not_implemented",
        "message": "OpenAI usage API integration pending",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }


def sync_anthropic_usage(table) -> Dict:
    """
    Sync Anthropic usage data from their API.

    Note: Anthropic may provide usage through their console API.
    This is a placeholder for when their usage API becomes available.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set, skipping Anthropic sync")
        return {"status": "skipped", "reason": "No API key"}

    # Anthropic doesn't currently have a public usage API
    # You might need to scrape their dashboard or wait for API availability

    return {
        "status": "not_implemented",
        "message": "Anthropic usage API not yet available",
    }


def sync_google_cloud_usage(table) -> Dict:
    """
    Sync Google Cloud usage data using Cloud Billing API.

    This requires setting up Google Cloud billing export to BigQuery
    or using the Cloud Billing API.
    """
    # Google Cloud provides billing data through:
    # 1. Cloud Billing API
    # 2. BigQuery billing export
    # 3. Cloud Monitoring API

    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT_ID")
    if not project_id:
        logger.warning("GOOGLE_CLOUD_PROJECT_ID not set, skipping Google sync")
        return {"status": "skipped", "reason": "No project ID"}

    # For Google Cloud, you typically set up billing export to BigQuery
    # Then query that data periodically

    try:
        # Example using Cloud Monitoring API to get metrics
        from google.cloud import monitoring_v3

        client = monitoring_v3.MetricServiceClient()
        project_path = f"projects/{project_id}"

        # Query for Places API usage
        interval = monitoring_v3.TimeInterval(
            {
                "end_time": {"seconds": int(datetime.utcnow().timestamp())},
                "start_time": {
                    "seconds": int(
                        (datetime.utcnow() - timedelta(hours=24)).timestamp()
                    )
                },
            }
        )

        # This would query actual metrics - simplified for example
        results = client.list_time_series(
            request={
                "name": project_path,
                "filter": 'metric.type="serviceruntime.googleapis.com/api/request_count"',
                "interval": interval,
            }
        )

        # Process results and store in DynamoDB
        usage_count = 0
        for result in results:
            usage_count += sum(point.value.int64_value for point in result.points)

        return {
            "status": "success",
            "api_calls": usage_count,
            "period": "last_24_hours",
        }

    except Exception as e:
        logger.error(f"Google Cloud sync error: {e}")
        return {"status": "error", "message": str(e)}


def store_provider_usage(table, service: str, usage_data: Dict):
    """
    Store usage data from provider in DynamoDB.
    This reconciles our tracked data with actual provider data.
    """
    timestamp = datetime.utcnow()

    item = {
        "PK": {"S": f"AI_USAGE_SYNC#{service}"},
        "SK": {"S": f"SYNC#{timestamp.isoformat()}"},
        "TYPE": {"S": "AIUsageSync"},
        "service": {"S": service},
        "timestamp": {"S": timestamp.isoformat()},
        "data": {"M": convert_to_dynamodb_format(usage_data)},
        "TTL": {
            "N": str(int((timestamp + timedelta(days=90)).timestamp()))
        },  # 90 day retention
    }

    table.put_item(Item=item)


def convert_to_dynamodb_format(data: Dict) -> Dict:
    """Convert Python dict to DynamoDB format."""
    result = {}
    for key, value in data.items():
        if value is None:
            result[key] = {"NULL": True}
        elif isinstance(value, str):
            result[key] = {"S": value}
        elif isinstance(value, (int, float)):
            result[key] = {"N": str(value)}
        elif isinstance(value, bool):
            result[key] = {"BOOL": value}
        elif isinstance(value, dict):
            result[key] = {"M": convert_to_dynamodb_format(value)}
        elif isinstance(value, list):
            result[key] = {"L": [_convert_value_to_dynamodb(v) for v in value]}
    return result


def _convert_value_to_dynamodb(value):
    """Convert a single value to DynamoDB format."""
    if value is None:
        return {"NULL": True}
    elif isinstance(value, str):
        return {"S": value}
    elif isinstance(value, (int, float)):
        return {"N": str(value)}
    elif isinstance(value, bool):
        return {"BOOL": value}
    elif isinstance(value, dict):
        return {"M": convert_to_dynamodb_format(value)}
    elif isinstance(value, list):
        return {"L": [_convert_value_to_dynamodb(v) for v in value]}
    else:
        # Fallback to string representation
        return {"S": str(value)}
