"""
Pulumi infrastructure for Fix Place Lambda.

This component creates a container-based Lambda that fixes incorrect
ReceiptPlace records using receipt content analysis and Google Places API.

The Lambda can be invoked directly with:
{
    "image_id": "uuid-string",
    "receipt_id": 1,
    "reason": "Description of why the current data is wrong"
}

Architecture:
- Container Lambda with all receipt_* packages
- Uses Google Places API for place resolution
- Updates ReceiptPlace in DynamoDB
"""

import json
import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Config, Output, ResourceOptions
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment

# Import shared components
try:
    from codebuild_docker_image import CodeBuildDockerImage
except ImportError:
    CodeBuildDockerImage = None  # type: ignore

# Load secrets
config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")
openrouter_api_key = config.require_secret("OPENROUTER_API_KEY")
langchain_api_key = config.require_secret("LANGCHAIN_API_KEY")
google_places_api_key = config.require_secret("GOOGLE_PLACES_API_KEY")


class FixPlaceLambda(ComponentResource):
    """
    Container Lambda for fixing incorrect ReceiptPlace records.

    This Lambda:
    1. Receives (image_id, receipt_id, reason)
    2. Reads receipt content from DynamoDB
    3. Extracts merchant hints from labeled words
    4. Searches Google Places for correct match
    5. Updates ReceiptPlace with corrected data

    Exports:
    - lambda_function: The Lambda function resource
    - lambda_arn: ARN for invoking the Lambda
    """

    def __init__(
        self,
        name: str,
        *,
        dynamodb_table_name: pulumi.Input[str],
        dynamodb_table_arn: pulumi.Input[str],
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(f"{__name__}-{name}", name, None, opts)
        stack = pulumi.get_stack()

        # ============================================================
        # IAM Role
        # ============================================================
        lambda_role = Role(
            f"{name}-lambda-role",
            name=f"{name}-lambda-role",
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

        # Basic Lambda execution
        RolePolicyAttachment(
            f"{name}-lambda-basic-exec",
            role=lambda_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/"
                "AWSLambdaBasicExecutionRole"
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # ECR permissions for container Lambda
        RolePolicy(
            f"{name}-lambda-ecr-policy",
            role=lambda_role.id,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "ecr:GetAuthorizationToken",
                                "ecr:BatchGetImage",
                                "ecr:GetDownloadUrlForLayer",
                            ],
                            "Resource": "*",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # DynamoDB access policy
        dynamodb_policy = RolePolicy(
            f"{name}-lambda-dynamo-policy",
            role=lambda_role.id,
            policy=Output.from_input(dynamodb_table_arn).apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:DescribeTable",
                                    "dynamodb:GetItem",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:Query",
                                    "dynamodb:BatchGetItem",
                                    "dynamodb:BatchWriteItem",
                                ],
                                "Resource": [
                                    arn,
                                    f"{arn}/index/*",
                                ],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # ============================================================
        # Container Lambda
        # ============================================================
        lambda_config = {
            "role_arn": lambda_role.arn,
            "timeout": 120,  # 2 minutes - usually completes quickly
            "memory_size": 512,  # Lightweight - no LLM needed
            "tags": {"environment": stack},
            "environment": {
                "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                # Google Places API
                "GOOGLE_PLACES_API_KEY": google_places_api_key,
                "RECEIPT_PLACES_TABLE_NAME": dynamodb_table_name,
                "RECEIPT_PLACES_AWS_REGION": "us-east-1",
                # OpenAI (for embeddings if needed)
                "RECEIPT_AGENT_OPENAI_API_KEY": openai_api_key,
                # LangSmith tracing
                "LANGCHAIN_API_KEY": langchain_api_key,
                "LANGCHAIN_TRACING_V2": "true",
                "LANGCHAIN_ENDPOINT": "https://api.smith.langchain.com",
                "LANGCHAIN_PROJECT": config.get("langchain_project") or "fix-place",
            },
        }

        docker_image = CodeBuildDockerImage(
            f"{name}-img",
            dockerfile_path="infra/fix_place_lambda/lambdas/Dockerfile",
            build_context_path=".",
            source_paths=[
                "receipt_agent",
                "receipt_places",
                "receipt_upload",
            ],
            lambda_function_name=f"{name}-fix-place",
            lambda_config=lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(
                parent=self, depends_on=[lambda_role, dynamodb_policy]
            ),
        )

        self.lambda_function = docker_image.lambda_function
        self.lambda_arn = self.lambda_function.arn

        # Register outputs
        self.register_outputs(
            {
                "lambda_arn": self.lambda_arn,
                "lambda_name": self.lambda_function.name,
            }
        )


def create_fix_place_lambda(
    dynamodb_table_name: pulumi.Input[str],
    dynamodb_table_arn: pulumi.Input[str],
) -> FixPlaceLambda:
    """
    Factory function to create the Fix Place Lambda.

    Args:
        dynamodb_table_name: Name of the DynamoDB table
        dynamodb_table_arn: ARN of the DynamoDB table

    Returns:
        FixPlaceLambda component with lambda_arn output
    """
    return FixPlaceLambda(
        "fix-place",
        dynamodb_table_name=dynamodb_table_name,
        dynamodb_table_arn=dynamodb_table_arn,
    )
