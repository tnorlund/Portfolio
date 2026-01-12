"""
Infrastructure for Label Validation Visualization Cache.

This component creates:
1. An API Lambda that serves cached visualization data
2. A Step Function that orchestrates:
   - Step 1: Lambda queries DynamoDB for receipts, words, and labels
   - Step 2: Lambda triggers LangSmith bulk export
   - Step 3: Wait state (60s) - FREE, no compute
   - Step 4: Lambda checks export status
   - Step 5: Choice - loop back to wait or continue
   - Step 6: Start EMR Serverless job for visualization cache generation

Unlike Label Evaluator (which has 6 evaluator scanners), Label Validation has
a two-tier structure: ChromaDB consensus (Tier 1) and LLM fallback (Tier 2).
"""

import json
import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import (
    AssetArchive,
    ComponentResource,
    FileArchive,
    Input,
    Output,
    ResourceOptions,
    StringAsset,
)

from infra.components.lambda_layer import dynamo_layer

# Get stack configuration
stack = pulumi.get_stack()

# Reference the lambdas directory
LAMBDAS_DIR = os.path.join(os.path.dirname(__file__), "lambdas")


class LabelValidationVizCache(ComponentResource):
    """API Lambda and Step Function for label validation visualization data."""

    def __init__(
        self,
        name: str,
        *,
        langsmith_export_bucket: Input[str],
        langsmith_api_key: Input[str],
        langsmith_tenant_id: Input[str],
        dynamodb_table_name: Input[str],
        dynamodb_table_arn: Input[str],
        emr_application_id: Input[str],
        emr_job_role_arn: Input[str],
        spark_artifacts_bucket: Input[str],
        setup_lambda_name: Input[str],
        setup_lambda_arn: Input[str],
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(
            f"custom:label-validation-viz-cache:{name}",
            name,
            None,
            opts,
        )

        region = aws.get_region().name
        account_id = aws.get_caller_identity().account_id

        # Convert to Output for proper resolution
        langsmith_export_bucket_output = Output.from_input(langsmith_export_bucket)
        langsmith_api_key_output = Output.from_input(langsmith_api_key)
        langsmith_tenant_id_output = Output.from_input(langsmith_tenant_id)
        dynamodb_table_name_output = Output.from_input(dynamodb_table_name)
        dynamodb_table_arn_output = Output.from_input(dynamodb_table_arn)
        emr_application_id_output = Output.from_input(emr_application_id)
        emr_job_role_arn_output = Output.from_input(emr_job_role_arn)
        spark_artifacts_bucket_output = Output.from_input(spark_artifacts_bucket)
        setup_lambda_name_output = Output.from_input(setup_lambda_name)
        setup_lambda_arn_output = Output.from_input(setup_lambda_arn)

        # ============================================================
        # S3 Cache Bucket
        # ============================================================
        self.cache_bucket = aws.s3.Bucket(
            f"{name}-cache-bucket",
            acl="private",
            tags={
                "Name": f"{name}-cache-bucket",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )
        cache_bucket_output = self.cache_bucket.id

        # ============================================================
        # IAM Role for API Lambda
        # ============================================================
        self.api_lambda_role = aws.iam.Role(
            f"{name}-api-lambda-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            tags={
                "Name": f"{name}-api-lambda-role",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-api-basic-execution",
            role=self.api_lambda_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self.api_lambda_role),
        )

        aws.iam.RolePolicy(
            f"{name}-api-cache-bucket-policy",
            role=self.api_lambda_role.id,
            policy=cache_bucket_output.apply(
                lambda bucket: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["s3:GetObject", "s3:ListBucket"],
                                "Resource": [
                                    f"arn:aws:s3:::{bucket}/*",
                                    f"arn:aws:s3:::{bucket}",
                                ],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # API Lambda (GET endpoint)
        # ============================================================
        self.api_lambda = aws.lambda_.Function(
            f"{name}-api-lambda",
            runtime="python3.12",
            architectures=["arm64"],
            role=self.api_lambda_role.arn,
            code=AssetArchive({".": FileArchive(LAMBDAS_DIR)}),
            handler="index.handler",
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "S3_CACHE_BUCKET": cache_bucket_output,
                }
            ),
            memory_size=256,
            timeout=30,
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        # Lambda invoke ARN for API Gateway
        self.api_lambda_invoke_arn = self.api_lambda.invoke_arn

        aws.cloudwatch.LogGroup(
            f"{name}-api-lambda-logs",
            name=self.api_lambda.name.apply(lambda n: f"/aws/lambda/{n}"),
            retention_in_days=30,
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Step 1: DynamoDB Query Lambda (exports receipts + words + labels)
        # ============================================================
        self.dynamo_query_role = aws.iam.Role(
            f"{name}-dynamo-query-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            tags={
                "Name": f"{name}-dynamo-query-role",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-dynamo-query-basic-execution",
            role=self.dynamo_query_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self.dynamo_query_role),
        )

        # DynamoDB read access
        aws.iam.RolePolicy(
            f"{name}-dynamo-query-dynamodb-policy",
            role=self.dynamo_query_role.id,
            policy=dynamodb_table_arn_output.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:DescribeTable",
                                    "dynamodb:Scan",
                                    "dynamodb:Query",
                                    "dynamodb:GetItem",
                                ],
                                "Resource": [arn, f"{arn}/index/*"],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # S3 write access for receipts-lookup.json
        aws.iam.RolePolicy(
            f"{name}-dynamo-query-s3-policy",
            role=self.dynamo_query_role.id,
            policy=cache_bucket_output.apply(
                lambda bucket: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["s3:PutObject"],
                                "Resource": f"arn:aws:s3:::{bucket}/*",
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # DynamoDB query code - exports receipts, words, and labels
        dynamo_query_code = '''
import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    """Query receipts, words, and labels from DynamoDB and write to S3.

    The EMR job needs:
    - Receipt CDN keys (for image display)
    - Word bounding boxes (for overlays)
    - Labels (for validation word filtering)
    """
    logger.info("Received event: %s", json.dumps(event))

    table_name = os.environ["DYNAMODB_TABLE"]
    cache_bucket = os.environ["CACHE_BUCKET"]

    # Import receipt_dynamo (from layer)
    from receipt_dynamo import DynamoClient

    # Initialize DynamoDB client
    client = DynamoClient(table_name)

    logger.info("Querying receipts from DynamoDB table: %s", table_name)

    # Build receipt lookup with words and labels
    receipt_lookup = {}
    page_count = 0
    last_key = None

    # First page of receipts
    receipts, last_key = client.list_receipts(limit=500)
    page_count += 1

    for r in receipts:
        key = f"{r.image_id}_{r.receipt_id}"

        # Get words for this receipt
        words = client.list_receipt_words_from_receipt(r.image_id, r.receipt_id)
        words_data = [
            {
                "line_id": w.line_id,
                "word_id": w.word_id,
                "text": w.text,
                "bbox": w.bounding_box,
            }
            for w in words
        ]

        # Get labels for this receipt
        labels, _ = client.list_receipt_word_labels_for_receipt(r.image_id, r.receipt_id)
        labels_data = {
            f"{label.line_id}_{label.word_id}": label.label
            for label in labels
        }

        receipt_lookup[key] = {
            "cdn_s3_key": r.cdn_s3_key or "",
            "cdn_webp_s3_key": r.cdn_webp_s3_key,
            "cdn_avif_s3_key": r.cdn_avif_s3_key,
            "cdn_medium_s3_key": r.cdn_medium_s3_key,
            "cdn_medium_webp_s3_key": r.cdn_medium_webp_s3_key,
            "cdn_medium_avif_s3_key": r.cdn_medium_avif_s3_key,
            "width": r.width or 0,
            "height": r.height or 0,
            "words": words_data,
            "labels": labels_data,
        }

    # Paginate through remaining receipts
    while last_key:
        receipts, last_key = client.list_receipts(limit=500, last_evaluated_key=last_key)
        page_count += 1

        for r in receipts:
            key = f"{r.image_id}_{r.receipt_id}"

            words = client.list_receipt_words_from_receipt(r.image_id, r.receipt_id)
            words_data = [
                {
                    "line_id": w.line_id,
                    "word_id": w.word_id,
                    "text": w.text,
                    "bbox": w.bounding_box,
                }
                for w in words
            ]

            labels, _ = client.list_receipt_word_labels_for_receipt(r.image_id, r.receipt_id)
            labels_data = {
                f"{label.line_id}_{label.word_id}": label.label
                for label in labels
            }

            receipt_lookup[key] = {
                "cdn_s3_key": r.cdn_s3_key or "",
                "cdn_webp_s3_key": r.cdn_webp_s3_key,
                "cdn_avif_s3_key": r.cdn_avif_s3_key,
                "cdn_medium_s3_key": r.cdn_medium_s3_key,
                "cdn_medium_webp_s3_key": r.cdn_medium_webp_s3_key,
                "cdn_medium_avif_s3_key": r.cdn_medium_avif_s3_key,
                "width": r.width or 0,
                "height": r.height or 0,
                "words": words_data,
                "labels": labels_data,
            }

        logger.info("Processed page %d, total receipts: %d", page_count, len(receipt_lookup))

    logger.info("Found %d receipts across %d pages", len(receipt_lookup), page_count)

    # Write to S3
    s3 = boto3.client("s3")
    s3_key = "receipts-lookup.json"
    try:
        s3.put_object(
            Bucket=cache_bucket,
            Key=s3_key,
            Body=json.dumps(receipt_lookup),
            ContentType="application/json",
        )
    except ClientError:
        logger.exception("Failed to write receipts lookup to S3")
        raise

    logger.info("Wrote %s to s3://%s/%s", s3_key, cache_bucket, s3_key)

    return {
        "receipt_count": len(receipt_lookup),
        "receipts_s3_path": f"s3://{cache_bucket}/{s3_key}",
    }
'''

        self.dynamo_query_lambda = aws.lambda_.Function(
            f"{name}-dynamo-query-lambda",
            runtime="python3.12",
            architectures=["arm64"],
            role=self.dynamo_query_role.arn,
            code=AssetArchive(
                {
                    "index.py": StringAsset(dynamo_query_code),
                }
            ),
            handler="index.handler",
            layers=[dynamo_layer.arn],
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE": dynamodb_table_name_output,
                    "CACHE_BUCKET": cache_bucket_output,
                }
            ),
            memory_size=1024,  # More memory for words/labels
            timeout=900,  # 15 minutes for large tables with words/labels
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        aws.cloudwatch.LogGroup(
            f"{name}-dynamo-query-logs",
            name=self.dynamo_query_lambda.name.apply(lambda n: f"/aws/lambda/{n}"),
            retention_in_days=30,
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Step 2: Trigger LangSmith Export Lambda
        # ============================================================
        self.trigger_export_role = aws.iam.Role(
            f"{name}-trigger-export-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-trigger-export-basic-execution",
            role=self.trigger_export_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self.trigger_export_role),
        )

        # Permission to invoke setup lambda
        aws.iam.RolePolicy(
            f"{name}-trigger-export-invoke-setup-policy",
            role=self.trigger_export_role.id,
            policy=setup_lambda_arn_output.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["lambda:InvokeFunction"],
                                "Resource": arn,
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        trigger_export_code = f'''
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
import urllib3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

LANGSMITH_API_URL = "https://api.smith.langchain.com"
# Project name for receipt-label-validation traces (golden dataset for testing)
DEFAULT_PROJECT = "receipt-validation-jan-11"


def _ensure_destination_exists(setup_lambda_name):
    """Get destination from setup Lambda (which handles SSM caching)."""
    lambda_client = boto3.client("lambda")

    # Just invoke the setup Lambda - it handles SSM caching internally
    # with a unique parameter path per component
    logger.info("Invoking setup lambda: %s", setup_lambda_name)
    response = lambda_client.invoke(
        FunctionName=setup_lambda_name,
        InvocationType="RequestResponse",
        Payload=json.dumps({{"prefix": "traces"}}),
    )

    result = json.loads(response["Payload"].read().decode())
    if result.get("statusCode") != 200:
        raise Exception(f"Setup lambda failed: {{result}}")

    destination_id = result.get("destination_id")
    if not destination_id:
        raise Exception(f"No destination_id returned: {{result}}")

    logger.info("Got destination from setup lambda: %s", destination_id)
    return destination_id


def handler(event, context):
    """Trigger LangSmith bulk export for receipt-label-validation project."""
    logger.info("Received event: %s", json.dumps(event))

    langchain_project = event.get("langchain_project", DEFAULT_PROJECT)
    api_key = os.environ["LANGCHAIN_API_KEY"]
    tenant_id = os.environ.get("LANGSMITH_TENANT_ID")
    setup_lambda_name = os.environ.get("SETUP_LAMBDA_NAME")

    http = urllib3.PoolManager()

    headers = {{"x-api-key": api_key}}
    if tenant_id:
        headers["x-tenant-id"] = tenant_id

    # Get destination from setup Lambda (handles SSM caching)
    destination_id = _ensure_destination_exists(setup_lambda_name)

    # Get project_id from LangSmith
    response = http.request(
        "GET",
        f"{{LANGSMITH_API_URL}}/api/v1/sessions",
        headers=headers,
    )
    if response.status != 200:
        raise Exception(f"Failed to list projects: {{response.data.decode()}}")

    projects = json.loads(response.data.decode("utf-8"))
    project_id = None
    for proj in projects:
        if proj.get("name") == langchain_project:
            project_id = proj.get("id")
            break

    if not project_id:
        raise Exception(f"Project not found: {{langchain_project}}")

    logger.info("Found project_id: %s for %s", project_id, langchain_project)

    # Trigger bulk export (last 7 days for more data)
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=7)

    export_body = {{
        "bulk_export_destination_id": destination_id,
        "session_id": project_id,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
    }}

    post_headers = dict(headers)
    post_headers["Content-Type"] = "application/json"

    response = http.request(
        "POST",
        f"{{LANGSMITH_API_URL}}/api/v1/bulk-exports",
        headers=post_headers,
        body=json.dumps(export_body),
    )

    if response.status not in (200, 201, 202):
        raise Exception(f"Failed to trigger export: {{response.data.decode()}}")

    result = json.loads(response.data.decode("utf-8"))
    export_id = result.get("id")
    logger.info("Triggered export: %s", export_id)

    return {{
        "export_id": export_id,
        "status": result.get("status", "pending"),
    }}
'''

        self.trigger_export_lambda = aws.lambda_.Function(
            f"{name}-trigger-export-lambda",
            runtime="python3.12",
            architectures=["arm64"],
            role=self.trigger_export_role.arn,
            code=AssetArchive(
                {
                    "index.py": StringAsset(trigger_export_code),
                }
            ),
            handler="index.handler",
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "LANGCHAIN_API_KEY": langsmith_api_key_output,
                    "LANGSMITH_TENANT_ID": langsmith_tenant_id_output,
                    "SETUP_LAMBDA_NAME": setup_lambda_name_output,
                    "STACK": stack,
                }
            ),
            memory_size=128,
            timeout=60,
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        aws.cloudwatch.LogGroup(
            f"{name}-trigger-export-logs",
            name=self.trigger_export_lambda.name.apply(lambda n: f"/aws/lambda/{n}"),
            retention_in_days=30,
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Step 4: Check Export Status Lambda
        # ============================================================
        self.check_export_role = aws.iam.Role(
            f"{name}-check-export-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-check-export-basic-execution",
            role=self.check_export_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self.check_export_role),
        )

        check_export_code = '''
import json
import logging
import os

import urllib3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

LANGSMITH_API_URL = "https://api.smith.langchain.com"


def handler(event, context):
    """Check LangSmith export status."""
    logger.info("Received event: %s", json.dumps(event))

    export_id = event.get("export_id")
    if not export_id:
        raise ValueError("export_id is required")

    api_key = os.environ["LANGCHAIN_API_KEY"]

    http = urllib3.PoolManager()

    response = http.request(
        "GET",
        f"{LANGSMITH_API_URL}/api/v1/bulk-exports/{export_id}",
        headers={"x-api-key": api_key},
    )

    if response.status != 200:
        logger.error("Failed to check export: %s", response.data.decode())
        return {"status": "error", "export_id": export_id}

    result = json.loads(response.data.decode("utf-8"))
    status = result.get("status", "unknown")

    # Normalize status
    if status in ("Complete", "Completed"):
        status = "completed"
    elif status in ("Failed", "Cancelled"):
        status = "failed"
    elif status in ("Pending", "Running", "InProgress"):
        status = "pending"

    logger.info("Export %s status: %s", export_id, status)

    return {
        "export_id": export_id,
        "status": status,
    }
'''

        self.check_export_lambda = aws.lambda_.Function(
            f"{name}-check-export-lambda",
            runtime="python3.12",
            architectures=["arm64"],
            role=self.check_export_role.arn,
            code=AssetArchive(
                {
                    "index.py": StringAsset(check_export_code),
                }
            ),
            handler="index.handler",
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "LANGCHAIN_API_KEY": langsmith_api_key_output,
                }
            ),
            memory_size=128,
            timeout=30,
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        aws.cloudwatch.LogGroup(
            f"{name}-check-export-logs",
            name=self.check_export_lambda.name.apply(lambda n: f"/aws/lambda/{n}"),
            retention_in_days=30,
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Step Function IAM Role
        # ============================================================
        self.step_function_role = aws.iam.Role(
            f"{name}-sf-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "states.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            tags={
                "Name": f"{name}-sf-role",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Allow Step Function to invoke Lambdas
        aws.iam.RolePolicy(
            f"{name}-sf-lambda-policy",
            role=self.step_function_role.id,
            policy=Output.all(
                self.dynamo_query_lambda.arn,
                self.trigger_export_lambda.arn,
                self.check_export_lambda.arn,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": "lambda:InvokeFunction",
                                "Resource": list(args),
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Allow Step Function to start EMR Serverless jobs
        aws.iam.RolePolicy(
            f"{name}-sf-emr-policy",
            role=self.step_function_role.id,
            policy=Output.all(
                emr_application_id_output,
                emr_job_role_arn_output,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "emr-serverless:StartJobRun",
                                    "emr-serverless:GetJobRun",
                                    "emr-serverless:CancelJobRun",
                                ],
                                "Resource": [
                                    f"arn:aws:emr-serverless:{region}:{account_id}"
                                    f":/applications/{args[0]}",
                                    f"arn:aws:emr-serverless:{region}:{account_id}"
                                    f":/applications/{args[0]}/jobruns/*",
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": "iam:PassRole",
                                "Resource": args[1],
                                "Condition": {
                                    "StringEquals": {
                                        "iam:PassedToService": "emr-serverless.amazonaws.com"
                                    }
                                },
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "events:PutTargets",
                                    "events:PutRule",
                                    "events:DescribeRule",
                                    "events:DeleteRule",
                                    "events:RemoveTargets",
                                ],
                                "Resource": [
                                    f"arn:aws:events:{region}:{account_id}:rule/StepFunctions*",
                                ],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Grant EMR job role access to cache and export buckets
        aws.iam.RolePolicy(
            f"{name}-emr-bucket-policy",
            role=emr_job_role_arn_output.apply(lambda arn: arn.split("/")[-1]),
            policy=Output.all(
                langsmith_export_bucket_output,
                cache_bucket_output,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            # Read from LangSmith export bucket
                            {
                                "Effect": "Allow",
                                "Action": ["s3:GetObject", "s3:ListBucket"],
                                "Resource": [
                                    f"arn:aws:s3:::{args[0]}",
                                    f"arn:aws:s3:::{args[0]}/*",
                                ],
                            },
                            # Read/write to cache bucket
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:PutObject",
                                    "s3:ListBucket",
                                ],
                                "Resource": [
                                    f"arn:aws:s3:::{args[1]}",
                                    f"arn:aws:s3:::{args[1]}/*",
                                ],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Step Function Definition
        # ============================================================
        step_function_definition = Output.all(
            self.dynamo_query_lambda.arn,
            self.trigger_export_lambda.arn,
            self.check_export_lambda.arn,
            emr_application_id_output,
            emr_job_role_arn_output,
            spark_artifacts_bucket_output,
            langsmith_export_bucket_output,
            cache_bucket_output,
        ).apply(
            lambda args: json.dumps(
                {
                    "Comment": "Label Validation viz cache generation with EMR Serverless",
                    "StartAt": "QueryDynamoDB",
                    "States": {
                        # Step 1: Query DynamoDB for receipts + words + labels
                        "QueryDynamoDB": {
                            "Type": "Task",
                            "Resource": args[0],
                            "ResultPath": "$.dynamo_result",
                            "Next": "TriggerLangSmithExport",
                        },
                        # Step 2: Trigger LangSmith bulk export
                        "TriggerLangSmithExport": {
                            "Type": "Task",
                            "Resource": args[1],
                            "Parameters": {
                                "langchain_project": "receipt-validation-jan-11",
                            },
                            "ResultPath": "$.export_result",
                            "Next": "InitializePollCount",
                        },
                        # Initialize poll counter
                        "InitializePollCount": {
                            "Type": "Pass",
                            "Result": 0,
                            "ResultPath": "$.poll_count",
                            "Next": "WaitForExport",
                        },
                        # Step 3: Wait 60 seconds
                        "WaitForExport": {
                            "Type": "Wait",
                            "Seconds": 60,
                            "Next": "CheckExportStatus",
                        },
                        # Step 4: Check export status
                        "CheckExportStatus": {
                            "Type": "Task",
                            "Resource": args[2],
                            "Parameters": {
                                "export_id.$": "$.export_result.export_id",
                            },
                            "ResultPath": "$.check_result",
                            "Next": "IncrementPollCount",
                        },
                        "IncrementPollCount": {
                            "Type": "Pass",
                            "Parameters": {
                                "value.$": "States.MathAdd($.poll_count, 1)",
                            },
                            "ResultPath": "$.poll_count_obj",
                            "Next": "UpdatePollCount",
                        },
                        "UpdatePollCount": {
                            "Type": "Pass",
                            "InputPath": "$.poll_count_obj.value",
                            "ResultPath": "$.poll_count",
                            "Next": "IsExportComplete",
                        },
                        # Step 5: Choice - loop, continue, or fail
                        "IsExportComplete": {
                            "Type": "Choice",
                            "Choices": [
                                {
                                    "Variable": "$.check_result.status",
                                    "StringEquals": "completed",
                                    "Next": "StartEMRJob",
                                },
                                {
                                    "Variable": "$.check_result.status",
                                    "StringEquals": "failed",
                                    "Next": "ExportFailed",
                                },
                                {
                                    "Variable": "$.poll_count",
                                    "NumericGreaterThanEquals": 30,
                                    "Next": "MaxRetriesExceeded",
                                },
                            ],
                            "Default": "WaitForExport",
                        },
                        "ExportFailed": {
                            "Type": "Fail",
                            "Error": "ExportFailed",
                            "Cause": "LangSmith export failed",
                        },
                        "MaxRetriesExceeded": {
                            "Type": "Fail",
                            "Error": "MaxRetriesExceeded",
                            "Cause": "Export check exceeded 30 minutes",
                        },
                        # Step 6: Start EMR job
                        "StartEMRJob": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::emr-serverless:startJobRun.sync",
                            "Parameters": {
                                "ApplicationId": args[3],
                                "ExecutionRoleArn": args[4],
                                "Name.$": "States.Format('label-val-viz-{}', $$.Execution.Name)",
                                "JobDriver": {
                                    "SparkSubmit": {
                                        "EntryPoint": f"s3://{args[5]}/spark/label_validation_viz_cache_job.py",
                                        "EntryPointArguments.$": (
                                            f"States.Array("
                                            f"'--parquet-bucket', '{args[6]}', "
                                            f"'--cache-bucket', '{args[7]}', "
                                            f"'--receipts-json', "
                                            f"$.dynamo_result.receipts_s3_path)"
                                        ),
                                        "SparkSubmitParameters": (
                                            "--conf spark.sql.legacy.parquet.nanosAsLong=true "
                                            "--conf spark.executor.cores=2 "
                                            "--conf spark.executor.memory=4g "
                                            "--conf spark.executor.instances=2 "
                                            "--conf spark.driver.cores=2 "
                                            "--conf spark.driver.memory=4g"
                                        ),
                                    }
                                },
                                "ConfigurationOverrides": {
                                    "MonitoringConfiguration": {
                                        "S3MonitoringConfiguration": {
                                            "LogUri": f"s3://{args[5]}/logs/"
                                        }
                                    }
                                },
                            },
                            "ResultPath": "$.emr_result",
                            "End": True,
                        },
                    },
                }
            )
        )

        self.step_function = aws.sfn.StateMachine(
            f"{name}-sf",
            role_arn=self.step_function_role.arn,
            definition=step_function_definition,
            tags={
                "Name": f"{name}-sf",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Outputs
        # ============================================================
        self.register_outputs(
            {
                "cache_bucket_name": self.cache_bucket.id,
                "api_lambda_arn": self.api_lambda.arn,
                "api_lambda_invoke_arn": self.api_lambda_invoke_arn,
                "step_function_arn": self.step_function.arn,
            }
        )


def create_label_validation_viz_cache(
    name: str,
    *,
    langsmith_export_bucket: Input[str],
    langsmith_api_key: Input[str],
    langsmith_tenant_id: Input[str],
    dynamodb_table_name: Input[str],
    dynamodb_table_arn: Input[str],
    emr_application_id: Input[str],
    emr_job_role_arn: Input[str],
    spark_artifacts_bucket: Input[str],
    setup_lambda_name: Input[str],
    setup_lambda_arn: Input[str],
    opts: Optional[ResourceOptions] = None,
) -> LabelValidationVizCache:
    """Factory function to create LabelValidationVizCache component.

    Args:
        name: Resource name prefix.
        langsmith_export_bucket: S3 bucket for LangSmith Parquet exports.
        langsmith_api_key: LangSmith API key.
        langsmith_tenant_id: LangSmith tenant ID.
        dynamodb_table_name: DynamoDB table name.
        dynamodb_table_arn: DynamoDB table ARN.
        emr_application_id: EMR Serverless application ID.
        emr_job_role_arn: EMR job execution role ARN.
        spark_artifacts_bucket: S3 bucket with Spark job scripts.
        setup_lambda_name: LangSmith setup Lambda name.
        setup_lambda_arn: LangSmith setup Lambda ARN.
        opts: Pulumi resource options.

    Returns:
        LabelValidationVizCache component.
    """
    return LabelValidationVizCache(
        name,
        langsmith_export_bucket=langsmith_export_bucket,
        langsmith_api_key=langsmith_api_key,
        langsmith_tenant_id=langsmith_tenant_id,
        dynamodb_table_name=dynamodb_table_name,
        dynamodb_table_arn=dynamodb_table_arn,
        emr_application_id=emr_application_id,
        emr_job_role_arn=emr_job_role_arn,
        spark_artifacts_bucket=spark_artifacts_bucket,
        setup_lambda_name=setup_lambda_name,
        setup_lambda_arn=setup_lambda_arn,
        opts=opts,
    )
