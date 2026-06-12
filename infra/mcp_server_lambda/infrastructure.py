"""
Pulumi infrastructure for Receipt MCP Server Lambda.

This component creates a container-based Lambda with a Function URL
that exposes the receipt MCP server for remote MCP access.  The
``run-mcp-servers-with-aws-lambda`` (mcp_lambda) adapter translates
incoming HTTP requests into stdio MCP messages.

The Function URL uses ``RESPONSE_STREAM`` invoke mode so MCP
streaming works end-to-end without API Gateway.

Architecture:
- Container Lambda with all receipt_* packages + mcp_lambda adapter
- Lambda Function URL (no API Gateway) for HTTP streaming
- IAM permissions for DynamoDB, S3, SQS, Lambda invoke
"""

import json
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
        "CodeBuildDockerImage is required for McpServerLambda. "
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


class McpServerLambda(ComponentResource):
    """
    Container Lambda exposing the receipt MCP server via Function URL.

    Uses ``run-mcp-servers-with-aws-lambda`` to bridge HTTP <-> stdio
    so that any MCP client (Claude Desktop, ``mcp-remote``, etc.) can
    reach the server over HTTPS.

    Exports:
    - lambda_function: The Lambda function resource
    - lambda_arn: ARN for invoking the Lambda
    - function_url: The Function URL endpoint for MCP access
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
        account_id = aws.get_caller_identity().account_id

        # ============================================================
        # IAM Role
        # ============================================================
        lambda_role = Role(
            f"{name}-lambda-role",
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

        # S3 access policy — scoped to receipt/chroma buckets in this account
        RolePolicy(
            f"{name}-lambda-s3-policy",
            role=lambda_role.id,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "s3:GetObject",
                                "s3:ListBucket",
                                "s3:GetBucketLocation",
                            ],
                            "Resource": [
                                "arn:aws:s3:::upload-images-*",
                                "arn:aws:s3:::upload-images-*/*",
                                "arn:aws:s3:::raw-image-bucket-*",
                                "arn:aws:s3:::raw-image-bucket-*/*",
                                "arn:aws:s3:::chromadb-*",
                                "arn:aws:s3:::chromadb-*/*",
                                "arn:aws:s3:::sitebucket-*",
                                "arn:aws:s3:::sitebucket-*/*",
                            ],
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # SQS access policy — scoped to upload-images queues
        RolePolicy(
            f"{name}-lambda-sqs-policy",
            role=lambda_role.id,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "sqs:SendMessage",
                                "sqs:GetQueueUrl",
                                "sqs:GetQueueAttributes",
                            ],
                            "Resource": f"arn:aws:sqs:us-east-1:{account_id}:upload-images-*",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # Lambda invoke policy — scoped to this account's functions
        RolePolicy(
            f"{name}-lambda-invoke-policy",
            role=lambda_role.id,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": ["lambda:InvokeFunction"],
                            "Resource": f"arn:aws:lambda:us-east-1:{account_id}:function:*",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # ============================================================
        # Container Lambda
        # ============================================================
        lambda_config = {
            "role_arn": lambda_role.arn,
            "timeout": 900,  # 15 min - MCP sessions can be long-lived
            "memory_size": 3072,  # 3 GB for MCP server + ChromaDB
            "tags": {"environment": stack},
            "environment": {
                "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                "PORTFOLIO_ENV": stack,
                # OpenAI (for embeddings)
                "OPENAI_API_KEY": openai_api_key,
                "RECEIPT_AGENT_OPENAI_API_KEY": openai_api_key,
                # OpenRouter (for LLM calls)
                "OPENROUTER_API_KEY": openrouter_api_key,
                # Chroma Cloud
                "CHROMA_CLOUD_API_KEY": chroma_cloud_api_key,
                "CHROMA_CLOUD_TENANT": chroma_cloud_tenant,
                "CHROMA_CLOUD_DATABASE": chroma_cloud_database,
                # Google Places API
                "GOOGLE_PLACES_API_KEY": google_places_api_key,
                # LangSmith tracing
                "LANGCHAIN_API_KEY": langchain_api_key,
                "LANGCHAIN_TRACING_V2": "true",
                "LANGCHAIN_ENDPOINT": "https://api.smith.langchain.com",
                "LANGCHAIN_PROJECT": (
                    config.get("langchain_project") or "receipt-mcp"
                ),
            },
        }

        docker_image = CodeBuildDockerImage(
            f"{name}-img",
            dockerfile_path="infra/mcp_server_lambda/lambdas/Dockerfile",
            build_context_path=".",
            source_paths=[
                "receipt_agent",
                "receipt_chroma",
                "receipt_dynamo",
                "receipt_dynamo_stream",
                "receipt_places",
                "receipt_upload",
            ],
            lambda_function_name=f"{name}-{stack}-mcp-server",
            lambda_config=lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(
                parent=self, depends_on=[lambda_role, dynamodb_policy]
            ),
        )

        self.lambda_function = docker_image.lambda_function

        # ============================================================
        # Lambda Function URL (streaming, no API Gateway)
        # ============================================================
        function_url = aws.lambda_.FunctionUrl(
            f"{name}-function-url",
            function_name=self.lambda_function.name,
            authorization_type="NONE",
            invoke_mode="RESPONSE_STREAM",
            opts=ResourceOptions(parent=self),
        )

        self.lambda_arn = self.lambda_function.arn
        self.function_url = function_url.function_url
        self.lambda_role_name = lambda_role.name

        # Register outputs
        self.register_outputs(
            {
                "lambda_arn": self.lambda_arn,
                "lambda_name": self.lambda_function.name,
                "lambda_role_name": self.lambda_role_name,
                "function_url": self.function_url,
            }
        )
