"""Pulumi infrastructure for the Glyph Studio MCP Server Lambda.

Creates a container-based Lambda with a Function URL that exposes the
read-only Glyph Studio inspection tools for remote MCP access. The
``run-mcp-servers-with-aws-lambda`` (mcp_lambda) adapter translates
incoming HTTP requests into stdio MCP messages.

The Function URL uses ``RESPONSE_STREAM`` invoke mode so MCP streaming
works end-to-end without API Gateway.

Architecture:
- Container Lambda with the glyphstudio package + baked font sources
- Lambda Function URL (no API Gateway) for HTTP streaming
- IAM: basic execution, ECR pull, and read-only S3 for the letterform
  corpora (raw-image-bucket-*/merchant_fonts/<font>/corpus.npz)
"""

import json
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, ResourceOptions
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment

try:
    from codebuild_docker_image import CodeBuildDockerImage
except ImportError as e:
    raise ImportError(
        "CodeBuildDockerImage is required for GlyphMcpLambda. "
        "Ensure codebuild_docker_image.py is in the Pulumi project root."
    ) from e

# S3 bucket + prefix that hold the uploaded letterform corpora. Bucket is
# resolved at deploy time; the default matches the raw-image bucket the
# corpora were uploaded to.
_CORPUS_BUCKET = "raw-image-bucket-c779c32"
_CORPUS_PREFIX = "merchant_fonts"


class GlyphMcpLambda(ComponentResource):
    """Container Lambda exposing the Glyph Studio MCP server via Function URL.

    Read-only: it renders/measures/compiles baked-in fonts and reads the
    letterform corpora from S3. It never writes back to the repo.

    Exports:
    - lambda_function: The Lambda function resource
    - lambda_arn: ARN for invoking the Lambda
    - function_url: The Function URL endpoint for MCP access
    """

    def __init__(
        self,
        name: str,
        *,
        corpus_bucket: str = _CORPUS_BUCKET,
        corpus_prefix: str = _CORPUS_PREFIX,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(f"{__name__}-{name}", name, None, opts)
        stack = pulumi.get_stack()

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

        # Basic Lambda execution (CloudWatch Logs)
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

        # S3 read for the letterform corpora — scoped to raw-image buckets.
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
                                "arn:aws:s3:::raw-image-bucket-*",
                                "arn:aws:s3:::raw-image-bucket-*/*",
                            ],
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
            "timeout": 300,  # 5 min — compile/compare are well under this
            "memory_size": 1536,  # numpy + PIL rasterization headroom
            "tags": {"environment": stack},
            "environment": {
                "GLYPH_FONTS_DIR": "/var/task/fonts",
                "GLYPH_CORPUS_BUCKET": corpus_bucket,
                "GLYPH_CORPUS_PREFIX": corpus_prefix,
            },
        }

        docker_image = CodeBuildDockerImage(
            f"{name}-img",
            dockerfile_path="infra/glyph_mcp_lambda/lambdas/Dockerfile",
            build_context_path=".",
            # receipt_agent is copied by the default rsync (BitmapFont shim);
            # the glyphstudio package + baked fonts are non-package trees, so
            # they ride along as extra literal context paths.
            extra_context_paths=[
                "tools/glyph-studio/py",
                "tools/glyph-studio/fonts",
            ],
            lambda_function_name=f"{name}-{stack}-mcp-server",
            lambda_config=lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_role]),
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

        self.register_outputs(
            {
                "lambda_arn": self.lambda_arn,
                "lambda_name": self.lambda_function.name,
                "lambda_role_name": self.lambda_role_name,
                "function_url": self.function_url,
            }
        )
