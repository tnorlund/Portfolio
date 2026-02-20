"""
Pulumi infrastructure for Fix Place Lambda.

This component creates a container-based Lambda that fixes incorrect
ReceiptPlace records using a LangGraph agent with ChromaDB similarity
search and Google Places API.

The Lambda can be invoked directly with:
{
    "image_id": "uuid-string",
    "receipt_id": 1,
    "reason": "Description of why the current data is wrong"
}

Architecture:
- Container Lambda with all receipt_* packages
- LangGraph agent with receipt context tools + similarity search
- Chroma Cloud for vector similarity search
- Google Places API for place resolution
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
except ImportError as e:
    raise ImportError(
        "CodeBuildDockerImage is required for FixPlaceLambda. "
        "Ensure codebuild_docker_image.py is in the Pulumi project root."
    ) from e

# Load secrets
config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")
openrouter_api_key = config.require_secret("OPENROUTER_API_KEY")
langchain_api_key = config.require_secret("LANGCHAIN_API_KEY")
google_places_api_key = config.require_secret("GOOGLE_PLACES_API_KEY")
chroma_cloud_api_key = config.require_secret("CHROMA_CLOUD_API_KEY")
chroma_cloud_tenant = config.get("CHROMA_CLOUD_TENANT") or ""
chroma_cloud_database = config.get("CHROMA_CLOUD_DATABASE") or ""


class FixPlaceLambda(ComponentResource):
    """
    Container Lambda for fixing incorrect ReceiptPlace records.

    This Lambda uses a LangGraph agent to:
    1. Receive (image_id, receipt_id, reason)
    2. Examine receipt content (lines, words, labels)
    3. Search ChromaDB for similar receipts
    4. Search Google Places for correct match
    5. Submit place data with confidence scoring
    6. Update ReceiptPlace with corrected data

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
            # Let Pulumi auto-generate unique name (exported for MCP server use)
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
            "timeout": 900,  # 15 min - LangGraph agent with tools
            "memory_size": 3072,  # 3 GB for LLM agent + ChromaDB
            "tags": {"environment": stack},
            "environment": {
                "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                # Google Places API
                "GOOGLE_PLACES_API_KEY": google_places_api_key,
                "RECEIPT_PLACES_TABLE_NAME": dynamodb_table_name,
                "RECEIPT_PLACES_AWS_REGION": "us-east-1",
                # OpenRouter (for LLM calls)
                "OPENROUTER_API_KEY": openrouter_api_key,
                # OpenAI (for embeddings)
                "RECEIPT_AGENT_OPENAI_API_KEY": openai_api_key,
                # Chroma Cloud
                "CHROMA_CLOUD_API_KEY": chroma_cloud_api_key,
                "CHROMA_CLOUD_TENANT": chroma_cloud_tenant,
                "CHROMA_CLOUD_DATABASE": chroma_cloud_database,
                # LangSmith tracing
                "LANGCHAIN_API_KEY": langchain_api_key,
                "LANGCHAIN_TRACING_V2": "true",
                "LANGCHAIN_ENDPOINT": ("https://api.smith.langchain.com"),
                "LANGCHAIN_PROJECT": (
                    config.get("langchain_project") or "fix-place"
                ),
            },
        }

        docker_image = CodeBuildDockerImage(
            f"{name}-img",
            dockerfile_path="infra/fix_place_lambda/lambdas/Dockerfile",
            build_context_path=".",
            source_paths=[
                "receipt_agent",
                "receipt_chroma",
                "receipt_dynamo",
                "receipt_dynamo_stream",
                "receipt_places",
                "receipt_upload",
            ],
            lambda_function_name=f"{name}-{stack}-fix-place",
            lambda_config=lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(
                parent=self, depends_on=[lambda_role, dynamodb_policy]
            ),
        )

        self.lambda_function = docker_image.lambda_function
        self.lambda_arn = self.lambda_function.arn
        self.lambda_role_name = lambda_role.name

        # Register outputs
        self.register_outputs(
            {
                "lambda_arn": self.lambda_arn,
                "lambda_name": self.lambda_function.name,
                "lambda_role_name": self.lambda_role_name,
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
