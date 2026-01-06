"""
Setup Lambda for LangSmith bulk export destination registration.

This Lambda registers an S3 destination with LangSmith's bulk export API.
It should be invoked once after initial deployment to set up the export destination.

The destination_id is stored in SSM Parameter Store for use by the trigger Lambda.
"""

import json
import logging
import os
from typing import Any

import boto3
import urllib3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# LangSmith API endpoint
LANGSMITH_API_URL = "https://api.smith.langchain.com"


def _get_s3_credentials() -> dict[str, str]:
    """Get S3 credentials from Secrets Manager."""
    secret_arn = os.environ["S3_CREDENTIALS_SECRET_ARN"]

    secretsmanager = boto3.client("secretsmanager")
    response = secretsmanager.get_secret_value(SecretId=secret_arn)

    return json.loads(response["SecretString"])


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Register S3 destination with LangSmith bulk export API.

    Environment variables:
        LANGCHAIN_API_KEY: LangSmith API key
        EXPORT_BUCKET: S3 bucket name for exports
        S3_CREDENTIALS_SECRET_ARN: ARN of Secrets Manager secret with S3 credentials
        LANGSMITH_PROJECT: LangSmith project name
        STACK: Pulumi stack name (dev/prod)

    Returns:
        Dict with destination_id and status
    """
    api_key = os.environ["LANGCHAIN_API_KEY"]
    tenant_id = os.environ["LANGSMITH_TENANT_ID"]
    bucket_name = os.environ["EXPORT_BUCKET"]
    project_name = os.environ["LANGSMITH_PROJECT"]
    stack = os.environ["STACK"]

    logger.info(f"Registering bulk export destination for project: {project_name}")
    logger.info(f"Export bucket: {bucket_name}")

    # Get S3 credentials from Secrets Manager
    s3_credentials = _get_s3_credentials()
    logger.info("Retrieved S3 credentials from Secrets Manager")

    # Check if destination already exists
    ssm = boto3.client("ssm")
    param_name = f"/langsmith/{stack}/destination_id"

    try:
        existing = ssm.get_parameter(Name=param_name)
        destination_id = existing["Parameter"]["Value"]
        logger.info(f"Destination already registered: {destination_id}")
        return {
            "statusCode": 200,
            "message": "Destination already registered",
            "destination_id": destination_id,
        }
    except ssm.exceptions.ParameterNotFound:
        logger.info("No existing destination found, creating new one")

    # Create HTTP client
    http = urllib3.PoolManager()

    # Get AWS region for the bucket
    s3 = boto3.client("s3")
    bucket_location = s3.get_bucket_location(Bucket=bucket_name)
    region = bucket_location.get("LocationConstraint") or "us-east-1"
    logger.info(f"Bucket region: {region}")

    # Test that credentials work before sending to LangSmith
    # Test with the same prefix we'll use for exports
    prefix = "traces/"
    test_s3 = boto3.client(
        "s3",
        aws_access_key_id=s3_credentials["access_key_id"],
        aws_secret_access_key=s3_credentials["secret_access_key"],
        region_name=region,
    )
    try:
        # Test various operations LangSmith might use during validation
        test_key = f"{prefix}_validation_test"
        test_s3.put_object(Bucket=bucket_name, Key=test_key, Body=b"test")
        logger.info(f"PutObject to {test_key} succeeded")
        test_s3.head_object(Bucket=bucket_name, Key=test_key)
        logger.info(f"HeadObject for {test_key} succeeded")
        test_s3.get_object(Bucket=bucket_name, Key=test_key)
        logger.info(f"GetObject for {test_key} succeeded")
        test_s3.delete_object(Bucket=bucket_name, Key=test_key)
        logger.info(f"DeleteObject for {test_key} succeeded")
        test_s3.head_bucket(Bucket=bucket_name)
        logger.info(f"HeadBucket for {bucket_name} succeeded")
        test_s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix, MaxKeys=1)
        logger.info(f"ListObjectsV2 for {prefix} succeeded")
        logger.info("All local credential tests passed")
    except Exception as e:
        logger.error(f"Local credential test failed: {e}")
        return {
            "statusCode": 500,
            "message": f"Credentials don't work locally: {str(e)}",
        }

    # Register destination with LangSmith
    # Note: endpoint_url is optional for AWS S3, only needed for S3-compatible buckets
    request_body = {
        "destination_type": "s3",
        "display_name": f"portfolio-traces-{stack}",
        "config": {
            "bucket_name": bucket_name,
            "prefix": "traces/",
            "region": region,
        },
        "credentials": {
            "access_key_id": s3_credentials["access_key_id"],
            "secret_access_key": s3_credentials["secret_access_key"],
        },
    }
    logger.info(f"Registering with bucket={bucket_name}, region={region}, prefix=traces/")
    logger.info(f"Request body: {json.dumps({k: v if k != 'credentials' else '***' for k, v in request_body.items()})}")

    try:
        response = http.request(
            "POST",
            f"{LANGSMITH_API_URL}/api/v1/bulk-exports/destinations",
            headers={
                "x-api-key": api_key,
                "X-Tenant-Id": tenant_id,
                "Content-Type": "application/json",
            },
            body=json.dumps(request_body),
        )

        if response.status not in (200, 201):
            error_msg = response.data.decode("utf-8")
            logger.error(f"LangSmith API error: {response.status} - {error_msg}")
            return {
                "statusCode": response.status,
                "message": f"Failed to register destination: {error_msg}",
            }

        result = json.loads(response.data.decode("utf-8"))
        destination_id = result.get("id")

        if not destination_id:
            logger.error(f"No destination_id in response: {result}")
            return {
                "statusCode": 500,
                "message": "No destination_id in LangSmith response",
                "response": result,
            }

        logger.info(f"Destination registered: {destination_id}")

        # Store destination_id in SSM Parameter Store
        ssm.put_parameter(
            Name=param_name,
            Value=destination_id,
            Type="String",
            Description=f"LangSmith bulk export destination ID for {project_name}",
            Overwrite=True,
        )

        logger.info(f"Stored destination_id in SSM: {param_name}")

        return {
            "statusCode": 200,
            "message": "Destination registered successfully",
            "destination_id": destination_id,
        }

    except Exception as e:
        logger.exception("Error registering destination")
        return {
            "statusCode": 500,
            "message": f"Error: {str(e)}",
        }
