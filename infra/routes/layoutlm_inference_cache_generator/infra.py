import json
import os
from pathlib import Path
from typing import Optional

import pulumi
import pulumi_aws as aws

# Import the DynamoDB table name from the dynamo_db module
from dynamo_db import dynamodb_table

# Import the CodeBuildDockerImage component
from infra.components.codebuild_docker_image import CodeBuildDockerImage
from pulumi import ComponentResource, Input, Output, ResourceOptions

# Reference the directory containing index.py
HANDLER_DIR = os.path.join(os.path.dirname(__file__), "lambdas")
# Get the route name from the directory name
ROUTE_NAME = os.path.basename(os.path.dirname(__file__))
# Get the DynamoDB table name
DYNAMODB_TABLE_NAME = dynamodb_table.name

# Get stack configuration
stack = pulumi.get_stack()


class LayoutLMInferenceCacheGenerator(ComponentResource):
    """Container-based Lambda for generating LayoutLM inference cache.

    Supports both single-pass and two-pass inference modes:
    - Single-pass: Uses LAYOUTLM_TRAINING_BUCKET to auto-discover latest model
    - Two-pass: Uses PASS1_MODEL_S3_URI + PASS2_MODEL_S3_URI or auto-discovery
    """

    def __init__(
        self,
        name: str,
        *,
        layoutlm_training_bucket: Input[str],
        model_s3_uri: Optional[Input[str]] = None,
        cache_bucket_name: Optional[Input[str]] = None,
        # Two-pass mode configuration
        two_pass_enabled: bool = False,
        pass1_model_s3_uri: Optional[Input[str]] = None,
        pass2_model_s3_uri: Optional[Input[str]] = None,
        two_pass_auto_discover_bucket: Optional[Input[str]] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(
            f"custom:layoutlm-inference-cache:{name}",
            name,
            None,
            opts,
        )

        # Create dedicated S3 bucket for API cache (separate from training bucket)
        self.cache_bucket = aws.s3.Bucket(
            f"{name}-cache-bucket",
            force_destroy=True,  # Allow bucket deletion for dev environments
            tags={
                "Name": f"{name}-cache-bucket",
                "Purpose": "LayoutLMInferenceAPICache",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Configure bucket ownership controls
        aws.s3.BucketOwnershipControls(
            f"{name}-cache-bucket-ownership",
            bucket=self.cache_bucket.id,
            rule=aws.s3.BucketOwnershipControlsRuleArgs(
                object_ownership="BucketOwnerEnforced"
            ),
            opts=ResourceOptions(parent=self),
        )

        # Create IAM role for Lambda
        self.lambda_role = aws.iam.Role(
            f"{name}-role",
            assume_role_policy="""{
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }""",
            tags={
                "Name": f"{name}-role",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Attach basic Lambda execution policy
        aws.iam.RolePolicyAttachment(
            f"{name}-basic-execution",
            role=self.lambda_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self.lambda_role),
        )

        # Convert Input[str] to Output[str] for proper resolution
        layoutlm_training_bucket_output = Output.from_input(
            layoutlm_training_bucket
        )

        # Convert model_s3_uri to Output if provided
        model_s3_uri_output = (
            Output.from_input(model_s3_uri) if model_s3_uri else None
        )

        # DynamoDB access policy
        self.dynamodb_policy = aws.iam.RolePolicy(
            f"{name}-dynamodb-policy",
            role=self.lambda_role.id,
            policy=dynamodb_table.arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:Query",
                                    "dynamodb:GetItem",
                                    "dynamodb:DescribeTable",
                                ],
                                "Resource": [
                                    arn,
                                    f"{arn}/index/*",
                                ],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Training bucket read policy (for model artifacts)
        self.training_s3_policy = aws.iam.RolePolicy(
            f"{name}-training-s3-policy",
            role=self.lambda_role.id,
            policy=layoutlm_training_bucket_output.apply(
                lambda bucket: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:ListBucket",
                                ],
                                "Resource": [
                                    f"arn:aws:s3:::{bucket}/*",
                                    f"arn:aws:s3:::{bucket}",
                                ],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Cache bucket read/write policy
        self.cache_s3_policy = aws.iam.RolePolicy(
            f"{name}-cache-s3-policy",
            role=self.lambda_role.id,
            policy=self.cache_bucket.id.apply(
                lambda bucket: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:PutObject",
                                    "s3:ListBucket",
                                ],
                                "Resource": [
                                    f"arn:aws:s3:::{bucket}/*",
                                    f"arn:aws:s3:::{bucket}",
                                ],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Build Docker image using CodeBuild
        dockerfile_path = "infra/routes/layoutlm_inference_cache_generator/lambdas/Dockerfile"
        build_context_path = "."  # Project root

        # Create Lambda function name first (needed for CodeBuild)
        lambda_function_name = f"{name}-lambda-{stack}"

        # Build environment variables dict
        lambda_environment = {
            "DYNAMODB_TABLE_NAME": DYNAMODB_TABLE_NAME,
            "S3_CACHE_BUCKET": self.cache_bucket.id,
            "LAYOUTLM_TRAINING_BUCKET": layoutlm_training_bucket_output,
        }

        # Add two-pass configuration if enabled
        if two_pass_enabled:
            lambda_environment["TWO_PASS_ENABLED"] = "true"
            if pass1_model_s3_uri:
                lambda_environment["PASS1_MODEL_S3_URI"] = Output.from_input(
                    pass1_model_s3_uri
                )
            if pass2_model_s3_uri:
                lambda_environment["PASS2_MODEL_S3_URI"] = Output.from_input(
                    pass2_model_s3_uri
                )
            if two_pass_auto_discover_bucket:
                lambda_environment["TWO_PASS_AUTO_DISCOVER_BUCKET"] = Output.from_input(
                    two_pass_auto_discover_bucket
                )
        elif model_s3_uri_output:
            # Single-pass mode with explicit model URI
            lambda_environment["MODEL_S3_URI"] = model_s3_uri_output

        # Build Docker image using CodeBuild
        # Include receipt_layoutlm in source_paths since it's not in default packages
        self.docker_image = CodeBuildDockerImage(
            f"{name}-image",
            dockerfile_path=dockerfile_path,
            build_context_path=build_context_path,
            source_paths=[
                "receipt_layoutlm"
            ],  # Include receipt_layoutlm package
            lambda_function_name=lambda_function_name,
            lambda_config={
                "role_arn": self.lambda_role.arn,
                "timeout": 900,  # 15 minutes (model loading + inference can be slow)
                "memory_size": 3008,  # Maximum for Lambda (PyTorch needs memory)
                "ephemeral_storage": 10240,  # 10GB for model download
                "architectures": ["arm64"],
"environment": lambda_environment,
            },
            platform="linux/arm64",
            opts=ResourceOptions(parent=self),
        )

        # Use the Lambda function created by CodeBuildDockerImage
        self.lambda_function = self.docker_image.lambda_function

        # CloudWatch log group for the Lambda function
        self.log_group = aws.cloudwatch.LogGroup(
            f"{name}-log-group",
            name=self.lambda_function.name.apply(
                lambda function_name: f"/aws/lambda/{function_name}"
            ),
            retention_in_days=30,
            opts=ResourceOptions(parent=self),
        )

        # EventBridge schedule to run every 2 minutes
        self.schedule = aws.cloudwatch.EventRule(
            f"{name}-schedule",
            description="Trigger LayoutLM inference cache generation every 2 minutes",
            schedule_expression="rate(2 minutes)",
            opts=ResourceOptions(parent=self),
        )

        # EventBridge target
        self.event_target = aws.cloudwatch.EventTarget(
            f"{name}-event-target",
            rule=self.schedule.name,
            arn=self.lambda_function.arn,
            opts=ResourceOptions(parent=self),
        )

        # Lambda permission for EventBridge
        self.lambda_permission = aws.lambda_.Permission(
            f"{name}-eventbridge-permission",
            action="lambda:InvokeFunction",
            function=self.lambda_function.name,
            principal="events.amazonaws.com",
            source_arn=self.schedule.arn,
            opts=ResourceOptions(parent=self),
        )

        # Export Lambda details and cache bucket
        self.register_outputs(
            {
                "lambda_arn": self.lambda_function.arn,
                "lambda_name": self.lambda_function.name,
                "schedule_arn": self.schedule.arn,
                "cache_bucket_name": self.cache_bucket.id,
                "cache_bucket_arn": self.cache_bucket.arn,
            }
        )


def create_layoutlm_inference_cache_generator(
    layoutlm_training_bucket: Input[str],
    *,
    model_s3_uri: Optional[Input[str]] = None,
    two_pass_enabled: bool = False,
    pass1_model_s3_uri: Optional[Input[str]] = None,
    pass2_model_s3_uri: Optional[Input[str]] = None,
    two_pass_auto_discover_bucket: Optional[Input[str]] = None,
    opts: Optional[ResourceOptions] = None,
) -> LayoutLMInferenceCacheGenerator:
    """Factory function to create LayoutLM inference cache generator.

    Args:
        layoutlm_training_bucket: S3 bucket containing trained models.
        model_s3_uri: Optional S3 URI to a specific model. If not provided,
                      the lambda will auto-select the most recently modified model.
        two_pass_enabled: Enable two-pass inference mode.
        pass1_model_s3_uri: Explicit S3 URI for Pass 1 model.
        pass2_model_s3_uri: Explicit S3 URI for Pass 2 model.
        two_pass_auto_discover_bucket: S3 bucket to auto-discover Pass 1 and Pass 2 models.
        opts: Pulumi resource options.
    """
    return LayoutLMInferenceCacheGenerator(
        f"layoutlm-inference-cache-generator-{pulumi.get_stack()}",
        layoutlm_training_bucket=layoutlm_training_bucket,
        model_s3_uri=model_s3_uri,
        two_pass_enabled=two_pass_enabled,
        pass1_model_s3_uri=pass1_model_s3_uri,
        pass2_model_s3_uri=pass2_model_s3_uri,
        two_pass_auto_discover_bucket=two_pass_auto_discover_bucket,
        opts=opts,
    )


# Module-level variable to hold the cache bucket name
# This will be set when the cache generator is created in __main__.py
cache_bucket_name: Optional[Output[str]] = None
