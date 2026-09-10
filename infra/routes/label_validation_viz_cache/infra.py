"""Serve label-validation caches and build them from native S3 traces."""

import json
import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import (
    AssetArchive,
    ComponentResource,
    FileArchive,
    FileAsset,
    Input,
    Output,
    ResourceOptions,
)

from infra.components.lambda_layer import dynamo_layer

from .definition import build_state_machine_definition

# Get stack configuration
stack = pulumi.get_stack()

# Reference the lambdas directory
LAMBDAS_DIR = os.path.join(os.path.dirname(__file__), "lambdas")
HANDLERS_DIR = os.path.join(os.path.dirname(__file__), "handlers")


class LabelValidationVizCache(ComponentResource):
    """API Lambda and Step Function for label validation visualization data."""

    def __init__(
        self,
        name: str,
        *,
        langsmith_export_bucket: Input[str],
        dynamodb_table_name: Input[str],
        dynamodb_table_arn: Input[str],
        emr_application_id: Input[str],
        emr_job_role_arn: Input[str],
        spark_artifacts_bucket: Input[str],
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
        langsmith_export_bucket_output = Output.from_input(
            langsmith_export_bucket
        )
        dynamodb_table_name_output = Output.from_input(dynamodb_table_name)
        dynamodb_table_arn_output = Output.from_input(dynamodb_table_arn)
        emr_application_id_output = Output.from_input(emr_application_id)
        emr_job_role_arn_output = Output.from_input(emr_job_role_arn)
        spark_artifacts_bucket_output = Output.from_input(
            spark_artifacts_bucket
        )

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

        # Server-side encryption
        aws.s3.BucketServerSideEncryptionConfiguration(
            f"{name}-cache-bucket-encryption",
            bucket=self.cache_bucket.id,
            rules=[
                aws.s3.BucketServerSideEncryptionConfigurationRuleArgs(
                    apply_server_side_encryption_by_default=(
                        aws.s3.BucketServerSideEncryptionConfigurationRuleApplyServerSideEncryptionByDefaultArgs(
                            sse_algorithm="AES256",
                        )
                    ),
                    bucket_key_enabled=True,
                ),
            ],
            opts=ResourceOptions(parent=self),
        )

        # Block public access
        aws.s3.BucketPublicAccessBlock(
            f"{name}-cache-bucket-pab",
            bucket=self.cache_bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=ResourceOptions(parent=self),
        )

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
            runtime="python3.13",
            architectures=["arm64"],
            role=self.api_lambda_role.arn,
            code=AssetArchive({".": FileArchive(LAMBDAS_DIR)}),
            handler="index.handler",
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "S3_CACHE_BUCKET": cache_bucket_output,
                }
            ),
            memory_size=1024,
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
        aws.iam.RolePolicy(
            f"{name}-query-native-traces-policy",
            role=self.dynamo_query_role.id,
            policy=langsmith_export_bucket_output.apply(
                lambda bucket: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["s3:GetObject", "s3:ListBucket"],
                                "Resource": [
                                    f"arn:aws:s3:::{bucket}",
                                    f"arn:aws:s3:::{bucket}/native-traces/*",
                                ],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        self.dynamo_query_lambda = aws.lambda_.Function(
            f"{name}-dynamo-query-lambda",
            runtime="python3.13",
            architectures=["arm64"],
            role=self.dynamo_query_role.arn,
            code=AssetArchive(
                {
                    "index.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "dynamo_query.py")
                    ),
                }
            ),
            handler="index.handler",
            layers=[dynamo_layer.arn],
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE": dynamodb_table_name_output,
                    "CACHE_BUCKET": cache_bucket_output,
                    "NATIVE_TRACE_BUCKET": langsmith_export_bucket_output,
                }
            ),
            memory_size=1024,  # More memory for words/labels
            timeout=900,  # 15 minutes for large tables with words/labels
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        aws.cloudwatch.LogGroup(
            f"{name}-dynamo-query-logs",
            name=self.dynamo_query_lambda.name.apply(
                lambda n: f"/aws/lambda/{n}"
            ),
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
            emr_application_id_output,
            emr_job_role_arn_output,
            spark_artifacts_bucket_output,
            langsmith_export_bucket_output,
            cache_bucket_output,
        ).apply(lambda args: json.dumps(build_state_machine_definition(*args)))

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
    dynamodb_table_name: Input[str],
    dynamodb_table_arn: Input[str],
    emr_application_id: Input[str],
    emr_job_role_arn: Input[str],
    spark_artifacts_bucket: Input[str],
    opts: Optional[ResourceOptions] = None,
) -> LabelValidationVizCache:
    """Factory function to create LabelValidationVizCache component.

    Args:
        name: Resource name prefix.
        langsmith_export_bucket: S3 bucket for LangSmith Parquet exports.
        dynamodb_table_name: DynamoDB table name.
        dynamodb_table_arn: DynamoDB table ARN.
        emr_application_id: EMR Serverless application ID.
        emr_job_role_arn: EMR job execution role ARN.
        spark_artifacts_bucket: S3 bucket with Spark job scripts.
        opts: Pulumi resource options.

    Returns:
        LabelValidationVizCache component.
    """
    return LabelValidationVizCache(
        name,
        langsmith_export_bucket=langsmith_export_bucket,
        dynamodb_table_name=dynamodb_table_name,
        dynamodb_table_arn=dynamodb_table_arn,
        emr_application_id=emr_application_id,
        emr_job_role_arn=emr_job_role_arn,
        spark_artifacts_bucket=spark_artifacts_bucket,
        opts=opts,
    )
