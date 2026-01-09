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
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# LangSmith API endpoint
LANGSMITH_API_URL = "https://api.smith.langchain.com"


def _get_s3_credentials() -> dict[str, str]:
    """Get S3 credentials from Secrets Manager."""
    secret_arn = os.environ["S3_CREDENTIALS_SECRET_ARN"]

    secretsmanager = boto3.client("secretsmanager")
    try:
        response = secretsmanager.get_secret_value(SecretId=secret_arn)
        credentials = json.loads(response["SecretString"])
    except secretsmanager.exceptions.ResourceNotFoundException:
        logger.exception("Secret not found: %s", secret_arn)
        raise
    except json.JSONDecodeError:
        logger.exception("Invalid JSON in secret: %s", secret_arn)
        raise
    except Exception:
        logger.exception("Error retrieving credentials from %s", secret_arn)
        raise

    # Validate required keys exist and are non-empty
    required_keys = ("access_key_id", "secret_access_key")
    for key in required_keys:
        if not credentials.get(key):
            logger.error("Missing or empty required key '%s' in secret: %s", key, secret_arn)
            raise ValueError(f"Secret missing required key: {key}")

    return credentials



def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Register S3 destination with LangSmith bulk export API.

    Environment variables (used as defaults):
        LANGCHAIN_API_KEY: LangSmith API key
        LANGSMITH_TENANT_ID: LangSmith workspace/tenant ID
        EXPORT_BUCKET: S3 bucket name for exports
        S3_CREDENTIALS_SECRET_ARN: ARN of Secrets Manager secret with S3 credentials
        LANGSMITH_PROJECT: LangSmith project name
        STACK: Pulumi stack name (dev/prod)

    Event parameters (override environment variables):
        tenant_id: LangSmith workspace ID (overrides LANGSMITH_TENANT_ID)
        display_name: Custom display name for the destination
        prefix: S3 prefix for exports (default: "traces/")
        skip_ssm: If True, don't store destination_id in SSM (default: False)

    Returns:
        Dict with destination_id and status
    """
    # Get config from event or fall back to environment variables
    api_key = event.get("api_key") or os.environ.get("LANGCHAIN_API_KEY")
    tenant_id = event.get("tenant_id") or os.environ.get("LANGSMITH_TENANT_ID")
    bucket_name = event.get("bucket_name") or os.environ.get("EXPORT_BUCKET")
    stack = os.environ.get("STACK", "dev")
    prefix = event.get("prefix", "traces/")
    skip_ssm = event.get("skip_ssm", False)

    if not api_key:
        return {
            "statusCode": 400,
            "message": "api_key required (via event or LANGCHAIN_API_KEY env var)",
        }

    if not tenant_id:
        return {
            "statusCode": 400,
            "message": "tenant_id required (via event or LANGSMITH_TENANT_ID env var)",
        }

    if not bucket_name:
        return {
            "statusCode": 400,
            "message": "bucket_name required (via event or EXPORT_BUCKET env var)",
        }


    logger.info(f"Registering bulk export destination for tenant: {tenant_id}")
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
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ParameterNotFound":
            raise
        logger.info("No existing destination found, creating new one")

    # Create HTTP client with timeout to prevent indefinite hangs
    http = urllib3.PoolManager(timeout=urllib3.Timeout(connect=5.0, read=30.0))

    # Get AWS region for the bucket
    s3 = boto3.client("s3")
    try:
        bucket_location = s3.get_bucket_location(Bucket=bucket_name)
        region = bucket_location.get("LocationConstraint") or "us-east-1"
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        if error_code == "NoSuchBucket":
            logger.exception("Bucket does not exist: %s", bucket_name)
            return {
                "statusCode": 404,
                "message": f"Bucket does not exist: {bucket_name}",
            }
        logger.exception("Failed to get bucket location for: %s", bucket_name)
        return {
            "statusCode": 500,
            "message": f"Failed to get bucket location for: {bucket_name}",
        }
    logger.info(f"Bucket region: {region}")

    # Test that credentials work before sending to LangSmith
    # Test with the same prefix we'll use for exports
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
        logger.exception("Local credential test failed")
        return {
            "statusCode": 500,
            "message": f"Credentials don't work locally: {e!s}",
        }

    # Register destination with LangSmith
    display_name = event.get("display_name", f"portfolio-traces-{stack}")

    # Build credentials dict, including session_token if present
    credentials = {
        "access_key_id": s3_credentials["access_key_id"],
        "secret_access_key": s3_credentials["secret_access_key"],
    }
    if s3_credentials.get("session_token"):
        credentials["session_token"] = s3_credentials["session_token"]

    # Build config - endpoint_url not needed for AWS S3
    config = {
        "bucket_name": bucket_name,
        "prefix": prefix,
        "region": region,
        "include_bucket_in_prefix": True,
    }

    # Only include endpoint_url for non-AWS S3 backends
    endpoint_url = event.get("endpoint_url")
    if endpoint_url:
        config["endpoint_url"] = endpoint_url

    request_body = {
        "destination_type": "s3",
        "display_name": display_name,
        "config": config,
        "credentials": credentials,
    }
    logger.info(
        f"Registering with bucket={bucket_name}, region={region}, prefix={prefix}"
    )
    logger.info(
        f"Request body: {json.dumps({k: v if k != 'credentials' else '***' for k, v in request_body.items()})}"
    )

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

        # Store destination_id in SSM Parameter Store (unless skip_ssm is True)
        if not skip_ssm:
            ssm.put_parameter(
                Name=param_name,
                Value=destination_id,
                Type="String",
                Description=f"LangSmith bulk export destination ID for {tenant_id}",
                Overwrite=True,
            )
            logger.info(f"Stored destination_id in SSM: {param_name}")
        else:
            logger.info("Skipping SSM storage (skip_ssm=True)")

        return {
            "statusCode": 200,
            "message": "Destination registered successfully",
            "destination_id": destination_id,
            "tenant_id": tenant_id,
            "display_name": display_name,
        }

    except Exception as e:
        logger.exception("Error registering destination")
        return {
            "statusCode": 500,
            "message": f"Error: {e!s}",
        }
