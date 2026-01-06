"""
Infrastructure for Label Evaluator Visualization Cache.

This component creates:
1. An API Lambda that serves cached visualization data
2. A cache generator Lambda that builds the cache from batch bucket data
"""

import json
import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import AssetArchive, ComponentResource, FileArchive, Input, Output, ResourceOptions

# Get stack configuration
stack = pulumi.get_stack()

# Reference the lambdas directory
LAMBDAS_DIR = os.path.join(os.path.dirname(__file__), "lambdas")


class LabelEvaluatorVizCache(ComponentResource):
    """API and cache generator Lambdas for label evaluator visualization data."""

    def __init__(
        self,
        name: str,
        *,
        llm_cache_bucket: Input[str],
        batch_bucket: Input[str],
        dynamodb_table_name: Input[str],
        dynamodb_table_arn: Input[str],
        receipt_dynamo_layer_arn: Input[str],
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(
            f"custom:label-evaluator-viz-cache:{name}",
            name,
            None,
            opts,
        )

        # Convert to Output for proper resolution
        cache_bucket_output = Output.from_input(llm_cache_bucket)
        batch_bucket_output = Output.from_input(batch_bucket)
        dynamodb_table_name_output = Output.from_input(dynamodb_table_name)
        dynamodb_table_arn_output = Output.from_input(dynamodb_table_arn)
        receipt_dynamo_layer_arn_output = Output.from_input(receipt_dynamo_layer_arn)

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

        # Basic Lambda execution for API Lambda
        aws.iam.RolePolicyAttachment(
            f"{name}-api-basic-execution",
            role=self.api_lambda_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self.api_lambda_role),
        )

        # Read from cache bucket for API Lambda
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

        # Log group for API Lambda
        aws.cloudwatch.LogGroup(
            f"{name}-api-lambda-logs",
            name=self.api_lambda.name.apply(lambda n: f"/aws/lambda/{n}"),
            retention_in_days=30,
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # IAM Role for Cache Generator Lambda
        # ============================================================
        self.generator_lambda_role = aws.iam.Role(
            f"{name}-generator-lambda-role",
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }),
            tags={
                "Name": f"{name}-generator-lambda-role",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Basic Lambda execution for Generator Lambda
        aws.iam.RolePolicyAttachment(
            f"{name}-generator-basic-execution",
            role=self.generator_lambda_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self.generator_lambda_role),
        )

        # Read from batch bucket (label evaluator data) for Generator Lambda
        aws.iam.RolePolicy(
            f"{name}-generator-batch-bucket-policy",
            role=self.generator_lambda_role.id,
            policy=batch_bucket_output.apply(
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

        # Read/write to cache bucket for Generator Lambda (ListBucket needed for cache operations)
        aws.iam.RolePolicy(
            f"{name}-generator-cache-bucket-policy",
            role=self.generator_lambda_role.id,
            policy=cache_bucket_output.apply(
                lambda bucket: json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Effect": "Allow",
                        "Action": ["s3:PutObject", "s3:GetObject", "s3:ListBucket"],
                        "Resource": [
                            f"arn:aws:s3:::{bucket}/*",
                            f"arn:aws:s3:::{bucket}",
                        ],
                    }],
                })
            ),
            opts=ResourceOptions(parent=self),
        )

        # DynamoDB access for Generator Lambda (to fetch receipt CDN keys)
        aws.iam.RolePolicy(
            f"{name}-generator-dynamodb-policy",
            role=self.generator_lambda_role.id,
            policy=dynamodb_table_arn_output.apply(
                lambda table_arn: json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:GetItem",
                            "dynamodb:Query",
                            "dynamodb:DescribeTable",
                        ],
                        "Resource": [table_arn],
                    }],
                })
            ),
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Cache Generator Lambda
        # ============================================================
        self.generator_lambda = aws.lambda_.Function(
            f"{name}-generator-lambda",
            runtime="python3.12",
            architectures=["arm64"],
            role=self.generator_lambda_role.arn,
            code=AssetArchive({".": FileArchive(LAMBDAS_DIR)}),
            handler="cache_generator.handler",
            layers=[receipt_dynamo_layer_arn_output],
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "S3_CACHE_BUCKET": cache_bucket_output,
                    "LABEL_EVALUATOR_BATCH_BUCKET": batch_bucket_output,
                    "DYNAMODB_TABLE_NAME": dynamodb_table_name_output,
                }
            ),
            memory_size=512,
            timeout=120,  # 2 minutes for cache generation
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        # Log group for Generator Lambda
        aws.cloudwatch.LogGroup(
            f"{name}-generator-lambda-logs",
            name=self.generator_lambda.name.apply(lambda n: f"/aws/lambda/{n}"),
            retention_in_days=30,
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Exports
        # ============================================================
        self.register_outputs({
            "api_lambda_arn": self.api_lambda.arn,
            "api_lambda_name": self.api_lambda.name,
            "generator_lambda_arn": self.generator_lambda.arn,
            "generator_lambda_name": self.generator_lambda.name,
        })


def create_label_evaluator_viz_cache(
    llm_cache_bucket: Input[str],
    batch_bucket: Input[str],
    dynamodb_table_name: Input[str],
    dynamodb_table_arn: Input[str],
    receipt_dynamo_layer_arn: Input[str],
    opts: Optional[ResourceOptions] = None,
) -> LabelEvaluatorVizCache:
    """Factory function to create label evaluator viz cache infrastructure."""
    return LabelEvaluatorVizCache(
        f"label-evaluator-viz-cache-{pulumi.get_stack()}",
        llm_cache_bucket=llm_cache_bucket,
        batch_bucket=batch_bucket,
        dynamodb_table_name=dynamodb_table_name,
        dynamodb_table_arn=dynamodb_table_arn,
        receipt_dynamo_layer_arn=receipt_dynamo_layer_arn,
        opts=opts,
    )
