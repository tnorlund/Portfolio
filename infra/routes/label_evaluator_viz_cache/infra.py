"""
Infrastructure for Label Evaluator Visualization Cache.

This component creates:
1. An API Lambda that serves cached visualization data
2. A Step Function that orchestrates:
   - Step 1: Lambda queries DynamoDB for receipt CDN keys
   - Step 2: Lambda triggers LangSmith bulk export
   - Step 3: Wait state (60s) - FREE, no compute
   - Step 4: Lambda checks export status
   - Step 5: Choice - loop back to wait or continue
   - Step 6: Lambda starts EMR Serverless job
3. EventBridge rule triggers the Step Function on Label Evaluator completion
"""

import json
import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import AssetArchive, ComponentResource, FileArchive, Input, Output, ResourceOptions, StringAsset

from infra.components.lambda_layer import dynamo_layer

# Get stack configuration
stack = pulumi.get_stack()

# Reference the lambdas directory
LAMBDAS_DIR = os.path.join(os.path.dirname(__file__), "lambdas")


class LabelEvaluatorVizCache(ComponentResource):
    """API Lambda and Step Function for label evaluator visualization data."""

    def __init__(
        self,
        name: str,
        *,
        langsmith_export_bucket: Input[str],
        langsmith_api_key: Input[str],
        langsmith_tenant_id: Input[str],
        batch_bucket: Input[str],
        dynamodb_table_name: Input[str],
        dynamodb_table_arn: Input[str],
        emr_application_id: Input[str],
        emr_job_role_arn: Input[str],
        spark_artifacts_bucket: Input[str],
        label_evaluator_sf_arn: Input[str],
        setup_lambda_name: Input[str],
        setup_lambda_arn: Input[str],
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(
            f"custom:label-evaluator-viz-cache:{name}",
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
        batch_bucket_output = Output.from_input(batch_bucket)
        dynamodb_table_name_output = Output.from_input(dynamodb_table_name)
        dynamodb_table_arn_output = Output.from_input(dynamodb_table_arn)
        emr_application_id_output = Output.from_input(emr_application_id)
        emr_job_role_arn_output = Output.from_input(emr_job_role_arn)
        spark_artifacts_bucket_output = Output.from_input(spark_artifacts_bucket)
        label_evaluator_sf_arn_output = Output.from_input(label_evaluator_sf_arn)
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
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }),
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
                lambda bucket: json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Effect": "Allow",
                        "Action": ["s3:GetObject", "s3:ListBucket"],
                        "Resource": [
                            f"arn:aws:s3:::{bucket}/*",
                            f"arn:aws:s3:::{bucket}",
                        ],
                    }],
                })
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

        aws.cloudwatch.LogGroup(
            f"{name}-api-lambda-logs",
            name=self.api_lambda.name.apply(lambda n: f"/aws/lambda/{n}"),
            retention_in_days=30,
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Step 1: DynamoDB Query Lambda (uses receipt_dynamo layer)
        # ============================================================
        self.dynamo_query_role = aws.iam.Role(
            f"{name}-dynamo-query-role",
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }),
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
                lambda arn: json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:DescribeTable",
                            "dynamodb:Scan",
                            "dynamodb:Query",
                            "dynamodb:GetItem",
                        ],
                        "Resource": [arn, f"{arn}/index/*"],
                    }],
                })
            ),
            opts=ResourceOptions(parent=self),
        )

        # S3 write access for receipts.json
        aws.iam.RolePolicy(
            f"{name}-dynamo-query-s3-policy",
            role=self.dynamo_query_role.id,
            policy=cache_bucket_output.apply(
                lambda bucket: json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Effect": "Allow",
                        "Action": ["s3:PutObject"],
                        "Resource": f"arn:aws:s3:::{bucket}/*",
                    }],
                })
            ),
            opts=ResourceOptions(parent=self),
        )

        dynamo_query_code = '''
import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    """Query all receipts from DynamoDB and write to S3."""
    logger.info("Received event: %s", json.dumps(event))

    table_name = os.environ["DYNAMODB_TABLE"]
    cache_bucket = os.environ["CACHE_BUCKET"]

    # Import receipt_dynamo (from layer)
    from receipt_dynamo import DynamoClient

    client = DynamoClient(table_name)

    logger.info("Querying receipts from DynamoDB table: %s", table_name)

    # Build receipt lookup: {image_id}_{receipt_id} -> cdn_s3_key
    receipt_lookup = {}
    receipts, last_key = client.list_receipts(limit=1000)

    for r in receipts:
        key = f"{r.image_id}_{r.receipt_id}"
        receipt_lookup[key] = r.cdn_s3_key

    # Paginate through all receipts
    while last_key:
        receipts, last_key = client.list_receipts(limit=1000, last_evaluated_key=last_key)
        for r in receipts:
            key = f"{r.image_id}_{r.receipt_id}"
            receipt_lookup[key] = r.cdn_s3_key

    logger.info("Found %d receipts", len(receipt_lookup))

    # Write to S3
    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=cache_bucket,
        Key="receipts-lookup.json",
        Body=json.dumps(receipt_lookup),
        ContentType="application/json",
    )

    logger.info("Wrote receipts-lookup.json to s3://%s/receipts-lookup.json", cache_bucket)

    return {
        "receipt_count": len(receipt_lookup),
        "receipts_s3_path": f"s3://{cache_bucket}/receipts-lookup.json",
    }
'''

        self.dynamo_query_lambda = aws.lambda_.Function(
            f"{name}-dynamo-query-lambda",
            runtime="python3.12",
            architectures=["arm64"],
            role=self.dynamo_query_role.arn,
            code=AssetArchive({
                "index.py": StringAsset(dynamo_query_code),
            }),
            handler="index.handler",
            layers=[dynamo_layer.arn],
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE": dynamodb_table_name_output,
                    "CACHE_BUCKET": cache_bucket_output,
                }
            ),
            memory_size=512,
            timeout=300,  # 5 minutes for large tables
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
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }),
            opts=ResourceOptions(parent=self),
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-trigger-export-basic-execution",
            role=self.trigger_export_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self.trigger_export_role),
        )

        # SSM access for LangSmith destination_id (read and delete stale)
        aws.iam.RolePolicy(
            f"{name}-trigger-export-ssm-policy",
            role=self.trigger_export_role.id,
            policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Action": ["ssm:GetParameter", "ssm:DeleteParameter"],
                    "Resource": f"arn:aws:ssm:{region}:{account_id}:parameter/langsmith/{stack}/*",
                }],
            }),
            opts=ResourceOptions(parent=self),
        )

        # Permission to invoke setup lambda for destination creation
        aws.iam.RolePolicy(
            f"{name}-trigger-export-invoke-setup-policy",
            role=self.trigger_export_role.id,
            policy=setup_lambda_arn_output.apply(lambda arn: json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Action": ["lambda:InvokeFunction"],
                    "Resource": arn,
                }],
            })),
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
SETUP_LAMBDA_NAME = "{name}-setup-lambda"


def _ensure_destination_exists(ssm, http, headers, stack, setup_lambda_name):
    """Ensure destination exists in LangSmith, create if needed."""
    param_name = f"/langsmith/{{stack}}/destination_id"
    lambda_client = boto3.client("lambda")

    # Try to get existing destination_id from SSM
    destination_id = None
    try:
        response = ssm.get_parameter(Name=param_name)
        destination_id = response["Parameter"]["Value"]
        logger.info("Found destination_id in SSM: %s", destination_id)
    except ssm.exceptions.ParameterNotFound:
        logger.info("No destination_id in SSM, will create new one")

    # If we have a destination_id, verify it exists in LangSmith
    if destination_id:
        response = http.request(
            "GET",
            f"{{LANGSMITH_API_URL}}/api/v1/bulk-export-destinations/{{destination_id}}",
            headers=headers,
        )
        if response.status == 200:
            logger.info("Destination verified in LangSmith: %s", destination_id)
            return destination_id
        else:
            logger.warning("Destination %s not found in LangSmith (status %s), will recreate",
                          destination_id, response.status)
            # Delete stale SSM parameter
            try:
                ssm.delete_parameter(Name=param_name)
            except Exception:
                pass

    # Create new destination by invoking setup lambda
    logger.info("Invoking setup lambda to create destination: %s", setup_lambda_name)
    response = lambda_client.invoke(
        FunctionName=setup_lambda_name,
        InvocationType="RequestResponse",
        Payload=json.dumps({{"prefix": "traces"}}),
    )

    result = json.loads(response["Payload"].read().decode())
    if result.get("statusCode") != 200:
        raise Exception(f"Setup lambda failed: {{result.get('message', result)}}")

    destination_id = result.get("destination_id")
    if not destination_id:
        raise Exception(f"Setup lambda did not return destination_id: {{result}}")

    logger.info("Created new destination: %s", destination_id)
    return destination_id


def handler(event, context):
    """Trigger LangSmith bulk export and return export_id."""
    logger.info("Received event: %s", json.dumps(event))

    langchain_project = event.get("langchain_project", "label-evaluator-viz")
    api_key = os.environ["LANGCHAIN_API_KEY"]
    tenant_id = os.environ.get("LANGSMITH_TENANT_ID")
    stack = os.environ.get("STACK", "dev")
    setup_lambda_name = os.environ.get("SETUP_LAMBDA_NAME", SETUP_LAMBDA_NAME)

    ssm = boto3.client("ssm")
    http = urllib3.PoolManager()

    # Build headers - include tenant_id if provided
    headers = {{"x-api-key": api_key}}
    if tenant_id:
        headers["x-tenant-id"] = tenant_id
        logger.info("Using tenant_id: %s", tenant_id)

    # Ensure destination exists (verify or create)
    destination_id = _ensure_destination_exists(ssm, http, headers, stack, setup_lambda_name)
    logger.info("Using destination_id: %s", destination_id)

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

    logger.info("Found project_id: %s", project_id)

    # Trigger bulk export
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=1)

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
            code=AssetArchive({
                "index.py": StringAsset(trigger_export_code),
            }),
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
            timeout=30,
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
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }),
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
        logger.error("Failed to check export status: %s", response.data.decode())
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
            code=AssetArchive({
                "index.py": StringAsset(check_export_code),
            }),
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
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "states.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }),
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
            ).apply(lambda args: json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Action": "lambda:InvokeFunction",
                    "Resource": list(args),
                }],
            })),
            opts=ResourceOptions(parent=self),
        )

        # Allow Step Function to start EMR Serverless jobs (native integration)
        aws.iam.RolePolicy(
            f"{name}-sf-emr-policy",
            role=self.step_function_role.id,
            policy=Output.all(
                emr_application_id_output,
                emr_job_role_arn_output,
            ).apply(lambda args: json.dumps({
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
                            f"arn:aws:emr-serverless:{region}:{account_id}:/applications/{args[0]}",
                            f"arn:aws:emr-serverless:{region}:{account_id}:/applications/{args[0]}/jobruns/*",
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
            })),
            opts=ResourceOptions(parent=self),
        )

        # Grant EMR job role access to batch and cache buckets
        aws.iam.RolePolicy(
            f"{name}-emr-bucket-policy",
            role=emr_job_role_arn_output.apply(lambda arn: arn.split("/")[-1]),
            policy=Output.all(
                batch_bucket_output,
                cache_bucket_output,
            ).apply(lambda args: json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    # Read from batch bucket (label evaluation results)
                    {
                        "Effect": "Allow",
                        "Action": ["s3:GetObject", "s3:ListBucket"],
                        "Resource": [
                            f"arn:aws:s3:::{args[0]}",
                            f"arn:aws:s3:::{args[0]}/*",
                        ],
                    },
                    # Read/write to cache bucket (viz cache output + receipts lookup)
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
            })),
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Step Function Definition (with native EMR Serverless integration)
        # ============================================================
        step_function_definition = Output.all(
            self.dynamo_query_lambda.arn,
            self.trigger_export_lambda.arn,
            self.check_export_lambda.arn,
            emr_application_id_output,
            emr_job_role_arn_output,
            spark_artifacts_bucket_output,
            langsmith_export_bucket_output,
            batch_bucket_output,
            cache_bucket_output,
        ).apply(lambda args: json.dumps({
            "Comment": "Viz cache generation with native EMR Serverless integration",
            "StartAt": "QueryDynamoDB",
            "States": {
                # Step 1: Query DynamoDB for receipt CDN keys
                "QueryDynamoDB": {
                    "Type": "Task",
                    "Resource": args[0],
                    "ResultPath": "$.dynamo_result",
                    "Next": "TriggerLangSmithExport",
                },
                # Step 2: Trigger LangSmith bulk export
                # Note: Lambda uses default "label-evaluator" if langchain_project not provided
                "TriggerLangSmithExport": {
                    "Type": "Task",
                    "Resource": args[1],
                    "ResultPath": "$.export_result",
                    "Next": "WaitForExport",
                },
                # Step 3: Wait 60 seconds (FREE - no compute)
                "WaitForExport": {
                    "Type": "Wait",
                    "Seconds": 60,
                    "Next": "CheckExportStatus",
                },
                # Step 4: Check export status (~200ms Lambda call)
                "CheckExportStatus": {
                    "Type": "Task",
                    "Resource": args[2],
                    "Parameters": {
                        "export_id.$": "$.export_result.export_id",
                    },
                    "ResultPath": "$.check_result",
                    "Next": "IsExportComplete",
                },
                # Step 5: Choice - loop or continue
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
                    ],
                    "Default": "WaitForExport",
                },
                # Export failed state
                "ExportFailed": {
                    "Type": "Fail",
                    "Error": "ExportFailed",
                    "Cause": "LangSmith export failed or was cancelled",
                },
                # Step 6: Start EMR job using native Step Functions integration
                # Uses .sync to wait for job completion
                # Note: Container image has receipt_langsmith pre-installed, no archives needed
                "StartEMRJob": {
                    "Type": "Task",
                    "Resource": "arn:aws:states:::emr-serverless:startJobRun.sync",
                    "Parameters": {
                        "ApplicationId": args[3],
                        "ExecutionRoleArn": args[4],
                        "Name.$": "States.Format('viz-cache-{}', $$.Execution.Name)",
                        "JobDriver": {
                            "SparkSubmit": {
                                "EntryPoint": f"s3://{args[5]}/spark/viz_cache_job.py",
                                "EntryPointArguments.$": f"States.Array('--parquet-bucket', '{args[6]}', '--parquet-prefix', 'traces/', '--batch-bucket', '{args[7]}', '--cache-bucket', '{args[8]}', '--receipts-json', $.dynamo_result.receipts_s3_path, '--max-receipts', '10')",
                                "SparkSubmitParameters": "--conf spark.sql.legacy.parquet.nanosAsLong=true --conf spark.executor.cores=2 --conf spark.executor.memory=4g --conf spark.executor.instances=2 --conf spark.driver.cores=2 --conf spark.driver.memory=4g",
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
        }))

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
        # EventBridge Trigger Lambda (starts the Step Function)
        # ============================================================
        self.eventbridge_trigger_role = aws.iam.Role(
            f"{name}-eb-trigger-role",
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }),
            opts=ResourceOptions(parent=self),
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-eb-trigger-basic-execution",
            role=self.eventbridge_trigger_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self.eventbridge_trigger_role),
        )

        aws.iam.RolePolicy(
            f"{name}-eb-trigger-sf-policy",
            role=self.eventbridge_trigger_role.id,
            policy=self.step_function.arn.apply(
                lambda arn: json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Effect": "Allow",
                        "Action": "states:StartExecution",
                        "Resource": arn,
                    }],
                })
            ),
            opts=ResourceOptions(parent=self),
        )

        eventbridge_trigger_code = '''
import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    """Start the viz cache Step Function on Label Evaluator completion."""
    logger.info("Received event: %s", json.dumps(event))

    detail = event.get("detail", {})
    execution_arn = detail.get("executionArn", "")
    status = detail.get("status", "")

    execution_name = execution_arn.split(":")[-1] if execution_arn else "unknown"

    if status != "SUCCEEDED":
        logger.info("Skipping - execution did not succeed")
        return {"statusCode": 200, "body": "Skipped"}

    # Get langchain_project from SF output (now at top level)
    output_str = detail.get("output", "{}")
    try:
        sf_output = json.loads(output_str)
        langchain_project = sf_output.get("langchain_project") or "label-evaluator"
    except json.JSONDecodeError:
        langchain_project = "label-evaluator"

    # Start viz cache Step Function
    sfn = boto3.client("stepfunctions")
    response = sfn.start_execution(
        stateMachineArn=os.environ["VIZ_CACHE_SF_ARN"],
        name=f"viz-{execution_name[:50]}",
        input=json.dumps({
            "execution_name": execution_name,
            "langchain_project": langchain_project,
        }),
    )

    logger.info("Started viz cache SF: %s", response["executionArn"])
    return {"statusCode": 200, "executionArn": response["executionArn"]}
'''

        self.eventbridge_trigger_lambda = aws.lambda_.Function(
            f"{name}-eb-trigger-lambda",
            runtime="python3.12",
            architectures=["arm64"],
            role=self.eventbridge_trigger_role.arn,
            code=AssetArchive({
                "index.py": StringAsset(eventbridge_trigger_code),
            }),
            handler="index.handler",
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "VIZ_CACHE_SF_ARN": self.step_function.arn,
                }
            ),
            memory_size=128,
            timeout=30,
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        aws.cloudwatch.LogGroup(
            f"{name}-eb-trigger-logs",
            name=self.eventbridge_trigger_lambda.name.apply(lambda n: f"/aws/lambda/{n}"),
            retention_in_days=30,
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # EventBridge Rule
        # ============================================================
        self.eventbridge_rule = aws.cloudwatch.EventRule(
            f"{name}-sfn-complete-rule",
            description="Trigger viz cache generation on Label Evaluator SF completion",
            event_pattern=label_evaluator_sf_arn_output.apply(
                lambda arn: json.dumps({
                    "source": ["aws.states"],
                    "detail-type": ["Step Functions Execution Status Change"],
                    "detail": {
                        "stateMachineArn": [arn],
                        "status": ["SUCCEEDED"],
                    },
                })
            ),
            tags={
                "Name": f"{name}-sfn-complete-rule",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        aws.cloudwatch.EventTarget(
            f"{name}-sfn-complete-target",
            rule=self.eventbridge_rule.name,
            arn=self.eventbridge_trigger_lambda.arn,
            opts=ResourceOptions(parent=self.eventbridge_rule),
        )

        aws.lambda_.Permission(
            f"{name}-eb-trigger-permission",
            action="lambda:InvokeFunction",
            function=self.eventbridge_trigger_lambda.name,
            principal="events.amazonaws.com",
            source_arn=self.eventbridge_rule.arn,
            opts=ResourceOptions(parent=self.eventbridge_trigger_lambda),
        )

        # ============================================================
        # Exports
        # ============================================================
        self.register_outputs({
            "cache_bucket_id": self.cache_bucket.id,
            "cache_bucket_arn": self.cache_bucket.arn,
            "api_lambda_arn": self.api_lambda.arn,
            "api_lambda_name": self.api_lambda.name,
            "step_function_arn": self.step_function.arn,
            "eventbridge_rule_arn": self.eventbridge_rule.arn,
        })


def create_label_evaluator_viz_cache(
    langsmith_export_bucket: Input[str],
    langsmith_api_key: Input[str],
    langsmith_tenant_id: Input[str],
    batch_bucket: Input[str],
    dynamodb_table_name: Input[str],
    dynamodb_table_arn: Input[str],
    emr_application_id: Input[str],
    emr_job_role_arn: Input[str],
    spark_artifacts_bucket: Input[str],
    label_evaluator_sf_arn: Input[str],
    setup_lambda_name: Input[str],
    setup_lambda_arn: Input[str],
    opts: Optional[ResourceOptions] = None,
) -> LabelEvaluatorVizCache:
    """Factory function to create label evaluator viz cache infrastructure."""
    return LabelEvaluatorVizCache(
        f"label-evaluator-viz-cache-{pulumi.get_stack()}",
        langsmith_export_bucket=langsmith_export_bucket,
        langsmith_api_key=langsmith_api_key,
        langsmith_tenant_id=langsmith_tenant_id,
        batch_bucket=batch_bucket,
        dynamodb_table_name=dynamodb_table_name,
        dynamodb_table_arn=dynamodb_table_arn,
        emr_application_id=emr_application_id,
        emr_job_role_arn=emr_job_role_arn,
        spark_artifacts_bucket=spark_artifacts_bucket,
        label_evaluator_sf_arn=label_evaluator_sf_arn,
        setup_lambda_name=setup_lambda_name,
        setup_lambda_arn=setup_lambda_arn,
        opts=opts,
    )
