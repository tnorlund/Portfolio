import json
import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Input, Output, ResourceOptions

# Import the ChromaDB bucket name from the shared chromadb_buckets module
from chromadb_buckets import bucket_name as chromadb_bucket_name

# Import the DynamoDB table name from the dynamo_db module
from dynamo_db import dynamodb_table

# Import the CodeBuildDockerImage component
from infra.components.codebuild_docker_image import CodeBuildDockerImage

# Get the DynamoDB table name
DYNAMODB_TABLE_NAME = dynamodb_table.name

# Get stack configuration
stack = pulumi.get_stack()
is_production = stack == "prod"

# Load portfolio config for Chroma Cloud settings
portfolio_config = pulumi.Config("portfolio")


class WordSimilarityCacheGenerator(ComponentResource):
    """Container-based Lambda for generating word similarity cache from ChromaDB."""

    def __init__(
        self,
        name: str,
        *,
        chromadb_bucket_name: Input[str],
        vpc_subnet_ids: Input[list[str]] | None = None,
        lambda_security_group_id: Input[str] | None = None,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(
            f"custom:word-similarity-cache:{name}",
            name,
            None,
            opts,
        )

        # Create dedicated S3 bucket for API cache (separate from ChromaDB bucket)
        self.cache_bucket = aws.s3.Bucket(
            f"{name}-cache-bucket",
            force_destroy=not is_production,  # Prevent accidental data loss in prod
            tags={
                "Name": f"{name}-cache-bucket",
                "Purpose": "WordSimilarityAPICache",
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

        # Attach VPC access policy if VPC config provided
        if vpc_subnet_ids is not None and lambda_security_group_id is not None:
            aws.iam.RolePolicyAttachment(
                f"{name}-vpc-access",
                role=self.lambda_role.name,
                policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
                opts=ResourceOptions(parent=self.lambda_role),
            )

        # Convert Input[str] to Output[str] for proper resolution
        chromadb_bucket_name_output = Output.from_input(chromadb_bucket_name)

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

        # ChromaDB bucket read policy
        self.chromadb_s3_policy = aws.iam.RolePolicy(
            f"{name}-chromadb-s3-policy",
            role=self.lambda_role.id,
            policy=chromadb_bucket_name_output.apply(
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
        dockerfile_path = (
            "infra/routes/word_similarity_cache_generator/lambdas/Dockerfile"
        )
        build_context_path = "."  # Project root

        # Create Lambda function name
        lambda_function_name = f"{name}-lambda-{stack}"

        # Build VPC config if parameters provided
        vpc_config = None
        if vpc_subnet_ids is not None and lambda_security_group_id is not None:
            vpc_config = {
                "subnet_ids": vpc_subnet_ids,
                "security_group_ids": [lambda_security_group_id],
            }

        # Build Docker image using CodeBuild
        self.docker_image = CodeBuildDockerImage(
            f"{name}-image",
            dockerfile_path=dockerfile_path,
            build_context_path=build_context_path,
            source_paths=None,
            lambda_function_name=lambda_function_name,
            lambda_config={
                "role_arn": self.lambda_role.arn,
                "timeout": 300,  # 5 minutes
                "memory_size": 2048,  # More memory for ChromaDB operations
                "ephemeral_storage": 10240,  # 10GB for snapshot download
                "architectures": ["arm64"],
                "vpc_config": vpc_config,
                "environment": {
                    "DYNAMODB_TABLE_NAME": DYNAMODB_TABLE_NAME,
                    "CHROMADB_BUCKET": Output.from_input(chromadb_bucket_name),
                    "S3_CACHE_BUCKET": self.cache_bucket.id,
                    # Chroma Cloud config for faster queries (skip S3 download)
                    "CHROMA_CLOUD_ENABLED": (
                        portfolio_config.get("CHROMA_CLOUD_ENABLED") or "false"
                    ),
                    "CHROMA_CLOUD_API_KEY": (
                        portfolio_config.get_secret("CHROMA_CLOUD_API_KEY")
                        or ""
                    ),
                    "CHROMA_CLOUD_TENANT": (
                        portfolio_config.get("CHROMA_CLOUD_TENANT") or ""
                    ),
                    "CHROMA_CLOUD_DATABASE": (
                        portfolio_config.get("CHROMA_CLOUD_DATABASE") or ""
                    ),
                },
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

        # EventBridge schedule to run once per day
        self.schedule = aws.cloudwatch.EventRule(
            f"{name}-schedule",
            description="Trigger word similarity cache generation once per day",
            schedule_expression="rate(1 day)",
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


def create_word_similarity_cache_generator(
    chromadb_bucket_name: Input[str],
    vpc_subnet_ids: Input[list[str]] | None = None,
    lambda_security_group_id: Input[str] | None = None,
    opts: Optional[ResourceOptions] = None,
) -> WordSimilarityCacheGenerator:
    """Factory function to create word similarity cache generator."""
    return WordSimilarityCacheGenerator(
        f"word-similarity-cache-generator-{pulumi.get_stack()}",
        chromadb_bucket_name=chromadb_bucket_name,
        vpc_subnet_ids=vpc_subnet_ids,
        lambda_security_group_id=lambda_security_group_id,
        opts=opts,
    )


# Note: Component is now created in __main__.py to allow VPC configuration
# The factory function create_word_similarity_cache_generator() should be called
# from __main__.py with vpc_subnet_ids and lambda_security_group_id parameters
# to enable DynamoDB Gateway endpoint access for reduced latency variance.
