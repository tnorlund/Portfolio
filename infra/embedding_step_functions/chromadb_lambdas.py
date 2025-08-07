"""
ChromaDB containerized Lambdas for word label step functions.

This module creates containerized Lambda functions for:
1. Polling OpenAI batch results and creating ChromaDB deltas
2. Compacting multiple deltas into final ChromaDB storage

Features:
- Hash-based change detection to skip unnecessary builds
- Optimized Docker layer caching
- ARM64 architecture for consistency with Lambda layers
"""

import json
import os
from pathlib import Path

import pulumi
from pulumi import ComponentResource, Output, ResourceOptions
from pulumi_aws import get_caller_identity, config
from pulumi_aws.ecr import (
    Repository,
    RepositoryImageScanningConfigurationArgs,
    get_authorization_token_output,  # Use output version for docker-build
)
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment
from pulumi_aws.lambda_ import (
    Function,
    FunctionEnvironmentArgs,
    FunctionEphemeralStorageArgs,
)
# Migrate to docker-build provider for ECR caching support
import pulumi_docker_build as docker_build

from dynamo_db import dynamodb_table


class ChromaDBLambdas(ComponentResource):
    """Component for ChromaDB containerized Lambda functions with optimized builds."""

    def build_lambda_with_caching(
        self,
        name: str,
        context_path: Path,
        repository: Repository,
        build_args: dict,
        stack: str,
        ecr_auth_token,
        parent: ComponentResource,
        dockerfile_name: str = "Dockerfile",
    ) -> docker_build.Image:
        """Build a Lambda image with docker-build provider and ECR caching.
        
        This helper method provides:
        1. ECR cache_from and cache_to configuration
        2. Proper registry authentication
        3. Multi-platform support (ARM64)
        
        Args:
            dockerfile_name: Name of the Dockerfile relative to context_path
        """
        # Ensure context_path is absolute and exists
        context_path_str = str(context_path.resolve())
        dockerfile_path = context_path / dockerfile_name
        
        # Log for debugging
        pulumi.log.info(f"Building {name} with context: {context_path_str}")
        pulumi.log.info(f"Looking for Dockerfile at: {dockerfile_path}")
        
        return docker_build.Image(
            f"{name}-img-{stack}",
            context={
                "location": context_path_str,
            },
            dockerfile={
                "location": dockerfile_name,  # Relative to context
            },
            platforms=["linux/arm64"],
            build_args=build_args,
            # ECR caching configuration
            cache_from=[
                {
                    "registry": {
                        "ref": repository.repository_url.apply(
                            lambda url: f"{url}:cache"
                        ),
                    },
                },
            ],
            cache_to=[
                {
                    "registry": {
                        "imageManifest": True,
                        "ociMediaTypes": True,
                        "ref": repository.repository_url.apply(
                            lambda url: f"{url}:cache"
                        ),
                    },
                },
            ],
            # Registry configuration for pushing
            push=True,
            registries=[
                {
                    # Use just the ECR registry address, not the full repository URL
                    "address": repository.repository_url.apply(
                        lambda url: url.split("/")[0]  # Extract registry address
                    ),
                    "password": ecr_auth_token.password,
                    "username": ecr_auth_token.user_name,
                },
            ],
            # Tags for the image
            tags=[
                repository.repository_url.apply(lambda url: f"{url}:latest"),
            ],
            opts=ResourceOptions(parent=parent, depends_on=[repository]),
        )

    def get_base_image_for_build(
        self,
        base_image_output: Output[str],
        stack: str,
        service: str = "label",
    ) -> str:
        """Get the base image name for Docker builds.

        In development mode (use-static-base-image=true), returns a static image name
        for better caching. In production, uses the dynamic Pulumi output.

        Args:
            base_image_output: The Pulumi output for the base image
            stack: The current stack name
            service: The service type ("label" or "dynamo")

        Returns:
            The base image name to use for builds
        """
        # Check Pulumi config first, then environment variable as fallback
        pulumi_config = pulumi.Config("portfolio")
        use_static = pulumi_config.get_bool("use-static-base-image")

        # If not in config, check environment variable
        if use_static is None:
            use_static = os.environ.get(
                "USE_STATIC_BASE_IMAGE", ""
            ).lower() in ("true", "1", "yes")

        if use_static:
            # For local development - use a fixed tag
            account_id = get_caller_identity().account_id
            region = config.region or "us-east-1"
            return f"{account_id}.dkr.ecr.{region}.amazonaws.com/base-receipt-{service}-{stack}:stable"
        else:
            # For production - use dynamic Pulumi output
            return base_image_output

    def __init__(
        self,
        name: str,
        chromadb_bucket_name: Output[str],
        chromadb_queue_url: Output[str],
        chromadb_queue_arn: Output[str],
        openai_api_key: Output[str],
        s3_batch_bucket_name: Output[str],
        stack: str,
        base_image_name: Output[str],  # Add base image parameter
        base_image_resource=None,  # Add optional resource for dependency
        opts: ResourceOptions = None,
    ):
        super().__init__(
            "custom:chromadb:ChromaDBLambdas",
            name,
            None,  # props - ComponentResource doesn't need props
            opts,
        )

        # Get AWS account details
        account_id = get_caller_identity().account_id
        region = config.region

        # Create ECR repository for polling Lambda
        self.polling_repo = Repository(
            f"line-embedding-poll-ecr-{stack}",
            name=f"line-embedding-poll-{stack}",
            image_scanning_configuration=(
                RepositoryImageScanningConfigurationArgs(
                    scan_on_push=True,
                )
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )

        # Create ECR repository for compaction Lambda
        self.compaction_repo = Repository(
            f"line-embedding-compact-ecr-{stack}",
            name=f"line-embedding-compact-{stack}",
            image_scanning_configuration=(
                RepositoryImageScanningConfigurationArgs(
                    scan_on_push=True,
                )
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )

        # Create ECR repository for line polling Lambda
        self.line_polling_repo = Repository(
            f"line-embedding-line-poll-ecr-{stack}",
            name=f"line-embedding-line-poll-{stack}",
            image_scanning_configuration=(
                RepositoryImageScanningConfigurationArgs(
                    scan_on_push=True,
                )
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )

        # Get ECR authorization token (using output version for docker-build)
        ecr_auth_token = get_authorization_token_output()

        # Build context path - now we'll use handler-specific contexts
        # Old: entire repo (2.9GB), Now: just handler directories (~10KB each)
        base_lambda_path = Path(__file__).parent

        # Build polling image using Pulumi Docker provider
        # Pulumi will handle the Output[str] dependency automatically
        build_args = {
            "PYTHON_VERSION": "3.12",
            "BUILDKIT_INLINE_CACHE": "1",
        }
        if base_image_name:
            build_args["BASE_IMAGE"] = self.get_base_image_for_build(
                base_image_name, stack
            )

        self.polling_image = self.build_lambda_with_caching(
            name="chromadb-poll",
            context_path=base_lambda_path / "chromadb_word_polling_lambda",
            repository=self.polling_repo,
            build_args=build_args,
            stack=stack,
            ecr_auth_token=ecr_auth_token,
            parent=self,
        )

        # Build compaction image using Pulumi Docker provider
        build_args = {
            "PYTHON_VERSION": "3.12",
            "BUILDKIT_INLINE_CACHE": "1",
        }
        if base_image_name:
            build_args["BASE_IMAGE"] = self.get_base_image_for_build(
                base_image_name, stack
            )

        self.compaction_image = self.build_lambda_with_caching(
            name="chromadb-compact",
            context_path=base_lambda_path / "chromadb_compaction_lambda",
            repository=self.compaction_repo,
            build_args=build_args,
            stack=stack,
            ecr_auth_token=ecr_auth_token,
            parent=self,
        )

        # Build line polling image using Pulumi Docker provider
        build_args = {
            "PYTHON_VERSION": "3.12",
            "BUILDKIT_INLINE_CACHE": "1",
        }
        if base_image_name:
            build_args["BASE_IMAGE"] = self.get_base_image_for_build(
                base_image_name, stack
            )

        self.line_polling_image = self.build_lambda_with_caching(
            name="chromadb-line-poll",
            context_path=base_lambda_path / "chromadb_line_polling_lambda",
            repository=self.line_polling_repo,
            build_args=build_args,
            stack=stack,
            ecr_auth_token=ecr_auth_token,
            parent=self,
        )

        # Create ECR repository for find unembedded lines Lambda
        self.find_unembedded_repo = Repository(
            f"line-find-unembedded-ecr-{stack}",
            name=f"line-find-unembedded-{stack}",
            image_scanning_configuration=(
                RepositoryImageScanningConfigurationArgs(
                    scan_on_push=True,
                )
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )

        # Create ECR repository for submit to OpenAI Lambda
        self.submit_openai_repo = Repository(
            f"line-submit-openai-ecr-{stack}",
            name=f"line-submit-openai-{stack}",
            image_scanning_configuration=(
                RepositoryImageScanningConfigurationArgs(
                    scan_on_push=True,
                )
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )

        # Create ECR repository for list pending batches Lambda
        self.list_pending_repo = Repository(
            f"line-list-pending-ecr-{stack}",
            name=f"line-list-pending-{stack}",
            image_scanning_configuration=(
                RepositoryImageScanningConfigurationArgs(
                    scan_on_push=True,
                )
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )

        # Build find unembedded lines image using Pulumi Docker provider
        build_args = {
            "PYTHON_VERSION": "3.12",
            "BUILDKIT_INLINE_CACHE": "1",
        }
        if base_image_name:
            build_args["BASE_IMAGE"] = self.get_base_image_for_build(
                base_image_name, stack
            )

        self.find_unembedded_image = self.build_lambda_with_caching(
            name="find-unembedded",
            context_path=base_lambda_path / "find_unembedded_lines_lambda",
            repository=self.find_unembedded_repo,
            build_args=build_args,
            stack=stack,
            ecr_auth_token=ecr_auth_token,
            parent=self,
        )

        # Build submit to OpenAI image using Pulumi Docker provider
        build_args = {
            "PYTHON_VERSION": "3.12",
            "BUILDKIT_INLINE_CACHE": "1",
        }
        if base_image_name:
            build_args["BASE_IMAGE"] = self.get_base_image_for_build(
                base_image_name, stack
            )

        self.submit_openai_image = self.build_lambda_with_caching(
            name="submit-openai",
            context_path=base_lambda_path / "submit_to_openai_lambda",
            repository=self.submit_openai_repo,
            build_args=build_args,
            stack=stack,
            ecr_auth_token=ecr_auth_token,
            parent=self,
        )

        # Build list pending batches image using Pulumi Docker provider
        build_args = {
            "PYTHON_VERSION": "3.12",
            "BUILDKIT_INLINE_CACHE": "1",
        }
        if base_image_name:
            build_args["BASE_IMAGE"] = self.get_base_image_for_build(
                base_image_name, stack
            )

        self.list_pending_image = self.build_lambda_with_caching(
            name="list-pending",
            context_path=base_lambda_path / "list_pending_batches_lambda",
            repository=self.list_pending_repo,
            build_args=build_args,
            stack=stack,
            ecr_auth_token=ecr_auth_token,
            parent=self,
        )

        # Create IAM role for polling Lambda (shorter names to avoid 64 char limit)
        self.polling_role = Role(
            f"line-embed-word-poll-{stack}",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Action": "sts:AssumeRole",
                            "Effect": "Allow",
                            "Principal": {
                                "Service": "lambda.amazonaws.com",
                            },
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Create IAM role for compaction Lambda
        self.compaction_role = Role(
            f"line-embed-compact-{stack}",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Action": "sts:AssumeRole",
                            "Effect": "Allow",
                            "Principal": {
                                "Service": "lambda.amazonaws.com",
                            },
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Attach basic Lambda execution policies
        RolePolicyAttachment(
            f"chromadb-poll-basic-{stack}",
            role=self.polling_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
            ),
            opts=ResourceOptions(parent=self),
        )

        RolePolicyAttachment(
            f"chromadb-compact-basic-{stack}",
            role=self.compaction_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
            ),
            opts=ResourceOptions(parent=self),
        )

        # Add permissions for polling Lambda
        RolePolicy(
            f"chromadb-poll-perms-{stack}",
            role=self.polling_role.id,
            policy=Output.all(
                dynamodb_table.name,
                chromadb_bucket_name,
                chromadb_queue_arn,
                region,
                account_id,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:PutItem",
                                    "dynamodb:GetItem",
                                    "dynamodb:Query",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:BatchWriteItem",
                                    "dynamodb:BatchGetItem",
                                    "dynamodb:DescribeTable",
                                ],
                                "Resource": [
                                    f"arn:aws:dynamodb:{args[3]}:{args[4]}:table/{args[0]}",
                                    f"arn:aws:dynamodb:{args[3]}:{args[4]}:table/{args[0]}/index/*",
                                ],
                            },
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
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "sqs:SendMessage",
                                    "sqs:GetQueueAttributes",
                                ],
                                "Resource": args[2],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Add permissions for compaction Lambda
        RolePolicy(
            f"chromadb-compact-perms-{stack}",
            role=self.compaction_role.id,
            policy=Output.all(
                dynamodb_table.name,
                chromadb_bucket_name,
                region,
                account_id,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:PutItem",
                                    "dynamodb:GetItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:DeleteItem",
                                    "dynamodb:DescribeTable",
                                ],
                                "Resource": [
                                    f"arn:aws:dynamodb:{args[2]}:{args[3]}:table/{args[0]}",
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:PutObject",
                                    "s3:DeleteObject",
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

        # Create polling Lambda function (for word embeddings)
        self.polling_lambda = Function(
            f"line-embed-word-poll-fn-{stack}",
            name=f"line-embed-word-poll-{stack}",
            package_type="Image",
            # Use the first tag instead of ref for reliability
            image_uri=self.polling_image.tags[0],
            role=self.polling_role.arn,
            architectures=["arm64"],
            memory_size=3008,  # Increased for processing large batches
            timeout=900,  # 15 minutes
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                    "CHROMADB_BUCKET": chromadb_bucket_name,
                    "COMPACTION_QUEUE_URL": chromadb_queue_url,
                    "OPENAI_API_KEY": openai_api_key,
                    "CHROMA_PERSIST_DIRECTORY": "/tmp/chroma",
                },
            ),
            ephemeral_storage=FunctionEphemeralStorageArgs(
                size=3072,  # 3GB for temporary ChromaDB storage
            ),
            opts=ResourceOptions(parent=self, depends_on=[self.polling_image]),
        )

        # Create compaction Lambda function
        self.compaction_lambda = Function(
            f"line-embed-compact-fn-{stack}",
            name=f"line-embed-compact-{stack}",
            package_type="Image",
            # Use the first tag instead of ref for reliability
            image_uri=self.compaction_image.tags[0],
            role=self.compaction_role.arn,
            architectures=["arm64"],
            memory_size=4096,  # More memory for compaction
            timeout=900,  # 15 minutes
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                    "CHROMADB_BUCKET": chromadb_bucket_name,
                    "CHROMA_PERSIST_DIRECTORY": "/tmp/chroma",
                },
            ),
            ephemeral_storage=FunctionEphemeralStorageArgs(
                size=5120,  # 5GB for compaction operations
            ),
            opts=ResourceOptions(parent=self, depends_on=[self.compaction_image]),
        )

        # Create IAM role for line polling Lambda
        self.line_polling_role = Role(
            f"line-embed-line-poll-{stack}",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Action": "sts:AssumeRole",
                            "Effect": "Allow",
                            "Principal": {
                                "Service": "lambda.amazonaws.com",
                            },
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Attach basic Lambda execution policy
        RolePolicyAttachment(
            f"chromadb-line-poll-basic-{stack}",
            role=self.line_polling_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
            ),
            opts=ResourceOptions(parent=self),
        )

        # Add permissions for line polling Lambda
        RolePolicy(
            f"chromadb-line-poll-perms-{stack}",
            role=self.line_polling_role.id,
            policy=Output.all(
                dynamodb_table.name,
                chromadb_bucket_name,
                chromadb_queue_arn,
                region,
                account_id,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:PutItem",
                                    "dynamodb:GetItem",
                                    "dynamodb:Query",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:BatchWriteItem",
                                    "dynamodb:BatchGetItem",
                                    "dynamodb:DescribeTable",
                                ],
                                "Resource": [
                                    f"arn:aws:dynamodb:{args[3]}:{args[4]}:table/{args[0]}",
                                    f"arn:aws:dynamodb:{args[3]}:{args[4]}:table/{args[0]}/index/*",
                                ],
                            },
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
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "sqs:SendMessage",
                                    "sqs:GetQueueAttributes",
                                ],
                                "Resource": args[2],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Create line polling Lambda function
        self.line_polling_lambda = Function(
            f"line-embed-line-poll-fn-{stack}",
            name=f"line-embed-line-poll-{stack}",
            package_type="Image",
            # Use the first tag instead of ref for reliability
            image_uri=self.line_polling_image.tags[0],
            role=self.line_polling_role.arn,
            architectures=["arm64"],
            memory_size=3008,  # Same as word polling
            timeout=900,  # 15 minutes
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                    "CHROMADB_BUCKET": chromadb_bucket_name,
                    "COMPACTION_QUEUE_URL": chromadb_queue_url,
                    "OPENAI_API_KEY": openai_api_key,
                    "CHROMA_PERSIST_DIRECTORY": "/tmp/chroma",
                    "SKIP_TABLE_VALIDATION": "true",  # Skip environment validation for table name
                },
            ),
            ephemeral_storage=FunctionEphemeralStorageArgs(
                size=3072,  # 3GB for temporary ChromaDB storage
            ),
            opts=ResourceOptions(parent=self, depends_on=[self.line_polling_image]),
        )

        # Create IAM role for find unembedded lines Lambda
        self.find_unembedded_role = Role(
            f"line-find-unembedded-{stack}",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Action": "sts:AssumeRole",
                            "Effect": "Allow",
                            "Principal": {
                                "Service": "lambda.amazonaws.com",
                            },
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Attach basic Lambda execution policy
        RolePolicyAttachment(
            f"find-unembedded-basic-{stack}",
            role=self.find_unembedded_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
            ),
            opts=ResourceOptions(parent=self),
        )

        # Add permissions for find unembedded lines Lambda
        RolePolicy(
            f"find-unembedded-perms-{stack}",
            role=self.find_unembedded_role.id,
            policy=Output.all(
                dynamodb_table.name,
                s3_batch_bucket_name,
                region,
                account_id,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:Query",
                                    "dynamodb:GetItem",
                                    "dynamodb:BatchGetItem",
                                    "dynamodb:DescribeTable",
                                ],
                                "Resource": [
                                    f"arn:aws:dynamodb:{args[2]}:{args[3]}:table/{args[0]}",
                                    f"arn:aws:dynamodb:{args[2]}:{args[3]}:table/{args[0]}/index/*",
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": ["s3:PutObject"],
                                "Resource": f"arn:aws:s3:::{args[1]}/*",
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Create find unembedded lines Lambda function
        self.find_unembedded_lambda = Function(
            f"find-unembedded-fn-{stack}",
            name=f"find-unembedded-lines-{stack}",
            package_type="Image",
            # Use the first tag instead of ref for reliability
            image_uri=self.find_unembedded_image.tags[0],
            role=self.find_unembedded_role.arn,
            architectures=["arm64"],
            memory_size=1024,
            timeout=900,
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                    "S3_BUCKET": s3_batch_bucket_name,
                    "OPENAI_API_KEY": openai_api_key,
                },
            ),
            opts=ResourceOptions(parent=self, depends_on=[self.find_unembedded_image]),
        )

        # Create IAM role for submit to OpenAI Lambda
        self.submit_openai_role = Role(
            f"line-submit-openai-{stack}",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Action": "sts:AssumeRole",
                            "Effect": "Allow",
                            "Principal": {
                                "Service": "lambda.amazonaws.com",
                            },
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Attach basic Lambda execution policy
        RolePolicyAttachment(
            f"submit-openai-basic-{stack}",
            role=self.submit_openai_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
            ),
            opts=ResourceOptions(parent=self),
        )

        # Add permissions for submit to OpenAI Lambda
        RolePolicy(
            f"submit-openai-perms-{stack}",
            role=self.submit_openai_role.id,
            policy=Output.all(
                dynamodb_table.name,
                s3_batch_bucket_name,
                region,
                account_id,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:BatchWriteItem",
                                    "dynamodb:DescribeTable",
                                ],
                                "Resource": [
                                    f"arn:aws:dynamodb:{args[2]}:{args[3]}:table/{args[0]}",
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": ["s3:GetObject"],
                                "Resource": f"arn:aws:s3:::{args[1]}/*",
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Create submit to OpenAI Lambda function
        self.submit_openai_lambda = Function(
            f"submit-openai-fn-{stack}",
            name=f"submit-to-openai-{stack}",
            package_type="Image",
            # Use the first tag instead of ref for reliability
            image_uri=self.submit_openai_image.tags[0],
            role=self.submit_openai_role.arn,
            architectures=["arm64"],
            memory_size=1024,
            timeout=900,
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                    "OPENAI_API_KEY": openai_api_key,
                },
            ),
            opts=ResourceOptions(parent=self, depends_on=[self.submit_openai_image]),
        )

        # Create IAM role for list pending batches Lambda
        self.list_pending_role = Role(
            f"line-list-pending-{stack}",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Action": "sts:AssumeRole",
                            "Effect": "Allow",
                            "Principal": {
                                "Service": "lambda.amazonaws.com",
                            },
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Attach basic Lambda execution policy
        RolePolicyAttachment(
            f"list-pending-basic-{stack}",
            role=self.list_pending_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
            ),
            opts=ResourceOptions(parent=self),
        )

        # Add permissions for list pending batches Lambda
        RolePolicy(
            f"list-pending-perms-{stack}",
            role=self.list_pending_role.id,
            policy=Output.all(
                dynamodb_table.name,
                region,
                account_id,
            ).apply(
                lambda args: json.dumps(
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
                                    f"arn:aws:dynamodb:{args[1]}:{args[2]}:table/{args[0]}",
                                    f"arn:aws:dynamodb:{args[1]}:{args[2]}:table/{args[0]}/index/*",
                                ],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Create list pending batches Lambda function
        self.list_pending_lambda = Function(
            f"list-pending-fn-{stack}",
            name=f"list-pending-batches-{stack}",
            package_type="Image",
            # Use the first tag instead of ref for reliability
            image_uri=self.list_pending_image.tags[0],
            role=self.list_pending_role.arn,
            architectures=["arm64"],
            memory_size=512,
            timeout=900,
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                    "OPENAI_API_KEY": openai_api_key,
                },
            ),
            opts=ResourceOptions(parent=self, depends_on=[self.list_pending_image]),
        )

        # Register outputs
        self.register_outputs(
            {
                "polling_lambda_arn": self.polling_lambda.arn,
                "polling_lambda_name": self.polling_lambda.name,
                "compaction_lambda_arn": self.compaction_lambda.arn,
                "compaction_lambda_name": self.compaction_lambda.name,
                "line_polling_lambda_arn": self.line_polling_lambda.arn,
                "line_polling_lambda_name": self.line_polling_lambda.name,
                "find_unembedded_lambda_arn": self.find_unembedded_lambda.arn,
                "find_unembedded_lambda_name": self.find_unembedded_lambda.name,
                "submit_openai_lambda_arn": self.submit_openai_lambda.arn,
                "submit_openai_lambda_name": self.submit_openai_lambda.name,
                "list_pending_lambda_arn": self.list_pending_lambda.arn,
                "list_pending_lambda_name": self.list_pending_lambda.name,
            }
        )
