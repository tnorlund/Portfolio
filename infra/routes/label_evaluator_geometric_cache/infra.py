"""
Infrastructure for Label Evaluator Geometric Anomaly Cache.

This component creates:
- S3 bucket for caching geometric anomaly examples
- Lambda for serving cached data (GET endpoint)
- Lambda for generating cached data (reads from label evaluator batch bucket)
- EventBridge schedule for weekly cache regeneration
"""

import json
import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import AssetArchive, ComponentResource, FileArchive, Input, Output, ResourceOptions

# Import shared components
from dynamo_db import dynamodb_table
from infra.components.lambda_layer import dynamo_layer

# Get stack configuration
stack = pulumi.get_stack()

# Reference the lambdas directory
LAMBDAS_DIR = os.path.join(os.path.dirname(__file__), "lambdas")


class LabelEvaluatorGeometricCache(ComponentResource):
    """Cache infrastructure for geometric anomaly visualization data."""

    def __init__(
        self,
        name: str,
        *,
        label_evaluator_batch_bucket: Input[str],
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(
            f"custom:label-evaluator-geometric-cache:{name}",
            name,
            None,
            opts,
        )

        # Convert to Output for proper resolution
        batch_bucket_output = Output.from_input(label_evaluator_batch_bucket)

        # ============================================================
        # S3 Cache Bucket
        # ============================================================
        self.cache_bucket = aws.s3.Bucket(
            f"{name}-cache-bucket",
            force_destroy=True,
            tags={
                "Name": f"{name}-cache-bucket",
                "Purpose": "GeometricAnomalyCache",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        aws.s3.BucketOwnershipControls(
            f"{name}-cache-bucket-ownership",
            bucket=self.cache_bucket.id,
            rule=aws.s3.BucketOwnershipControlsRuleArgs(
                object_ownership="BucketOwnerEnforced"
            ),
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # IAM Role for Lambda
        # ============================================================
        self.lambda_role = aws.iam.Role(
            f"{name}-lambda-role",
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }),
            tags={
                "Name": f"{name}-lambda-role",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Basic Lambda execution
        aws.iam.RolePolicyAttachment(
            f"{name}-basic-execution",
            role=self.lambda_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self.lambda_role),
        )

        # DynamoDB read access
        aws.iam.RolePolicy(
            f"{name}-dynamodb-policy",
            role=self.lambda_role.id,
            policy=dynamodb_table.arn.apply(
                lambda arn: json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:Query",
                            "dynamodb:GetItem",
                            "dynamodb:DescribeTable",
                        ],
                        "Resource": [arn, f"{arn}/index/*"],
                    }],
                })
            ),
            opts=ResourceOptions(parent=self),
        )

        # Read from label evaluator batch bucket
        aws.iam.RolePolicy(
            f"{name}-batch-bucket-policy",
            role=self.lambda_role.id,
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

        # Read/write to cache bucket
        aws.iam.RolePolicy(
            f"{name}-cache-bucket-policy",
            role=self.lambda_role.id,
            policy=self.cache_bucket.id.apply(
                lambda bucket: json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Effect": "Allow",
                        "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
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
            role=self.lambda_role.arn,
            code=AssetArchive({".": FileArchive(LAMBDAS_DIR)}),
            handler="index.handler",
            layers=[dynamo_layer.arn],
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "S3_CACHE_BUCKET": self.cache_bucket.id,
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                }
            ),
            memory_size=512,
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
        # Cache Generator Lambda
        # ============================================================
        self.generator_lambda = aws.lambda_.Function(
            f"{name}-generator-lambda",
            runtime="python3.12",
            architectures=["arm64"],
            role=self.lambda_role.arn,
            code=AssetArchive({".": FileArchive(LAMBDAS_DIR)}),
            handler="cache_generator.handler",
            layers=[dynamo_layer.arn],
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "S3_CACHE_BUCKET": self.cache_bucket.id,
                    "LABEL_EVALUATOR_BATCH_BUCKET": batch_bucket_output,
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                }
            ),
            memory_size=1024,
            timeout=300,  # 5 minutes for batch processing
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        # Log group for generator Lambda
        aws.cloudwatch.LogGroup(
            f"{name}-generator-lambda-logs",
            name=self.generator_lambda.name.apply(lambda n: f"/aws/lambda/{n}"),
            retention_in_days=30,
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # EventBridge Schedule (Weekly)
        # ============================================================
        self.schedule = aws.cloudwatch.EventRule(
            f"{name}-weekly-schedule",
            description="Generate geometric anomaly cache weekly",
            schedule_expression="rate(7 days)",
            opts=ResourceOptions(parent=self),
        )

        aws.cloudwatch.EventTarget(
            f"{name}-schedule-target",
            rule=self.schedule.name,
            arn=self.generator_lambda.arn,
            opts=ResourceOptions(parent=self),
        )

        aws.lambda_.Permission(
            f"{name}-eventbridge-permission",
            action="lambda:InvokeFunction",
            function=self.generator_lambda.name,
            principal="events.amazonaws.com",
            source_arn=self.schedule.arn,
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Exports
        # ============================================================
        self.register_outputs({
            "api_lambda_arn": self.api_lambda.arn,
            "api_lambda_name": self.api_lambda.name,
            "generator_lambda_arn": self.generator_lambda.arn,
            "cache_bucket_name": self.cache_bucket.id,
        })


def create_label_evaluator_geometric_cache(
    label_evaluator_batch_bucket: Input[str],
    opts: Optional[ResourceOptions] = None,
) -> LabelEvaluatorGeometricCache:
    """Factory function to create geometric anomaly cache infrastructure."""
    return LabelEvaluatorGeometricCache(
        f"geometric-anomaly-cache-{pulumi.get_stack()}",
        label_evaluator_batch_bucket=label_evaluator_batch_bucket,
        opts=opts,
    )
