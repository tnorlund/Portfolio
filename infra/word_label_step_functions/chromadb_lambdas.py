"""
ChromaDB containerized Lambdas for word label step functions.

This module creates containerized Lambda functions for:
1. Polling OpenAI batch results and creating ChromaDB deltas
2. Compacting multiple deltas into final ChromaDB storage

Features:
- Uses Pulumi Docker provider for reliable builds
- ARM64 architecture for consistency with Lambda layers
- Proper handling of Output[str] for base images
"""

import json
from pathlib import Path

import pulumi
from pulumi import ComponentResource, Output, ResourceOptions
from pulumi_aws import get_caller_identity, config
from pulumi_aws.ecr import (
    Repository,
    RepositoryImageScanningConfigurationArgs,
    get_authorization_token,
)
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment
from pulumi_aws.lambda_ import (
    Function,
    FunctionEnvironmentArgs,
    FunctionEphemeralStorageArgs,
)
from pulumi_docker import DockerBuildArgs, Image, RegistryArgs

from dynamo_db import dynamodb_table


class ChromaDBLambdas(ComponentResource):
    """Component for ChromaDB containerized Lambda functions with optimized builds."""

    def __init__(
        self,
        name: str,
        chromadb_bucket_name: Output[str],
        chromadb_queue_url: Output[str],
        chromadb_queue_arn: Output[str],
        openai_api_key: Output[str],
        stack: str,
        base_image_name: Output[str] = None,
        opts: ResourceOptions = None,
    ):
        super().__init__(
            "custom:chromadb:ChromaDBLambdas", 
            name,
            None,  # props - ComponentResource doesn't need props
            opts
        )

        # Get AWS account details
        account_id = get_caller_identity().account_id
        region = config.region

        # Create ECR repository for polling Lambda
        self.polling_repo = Repository(
            f"chromadb-poll-ecr-{stack}",
            name=f"chromadb-poll-{stack}",
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
            f"chromadb-compact-ecr-{stack}",
            name=f"chromadb-compact-{stack}",
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
            f"chromadb-line-poll-ecr-{stack}",
            name=f"chromadb-line-poll-{stack}",
            image_scanning_configuration=(
                RepositoryImageScanningConfigurationArgs(
                    scan_on_push=True,
                )
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )

        # Get ECR authorization token
        ecr_auth_token = get_authorization_token()

        # Build context path
        build_context_path = Path(__file__).parent.parent.parent
        
        # Build polling image using Pulumi Docker provider
        # Pulumi will handle caching, change detection, and Output[str] dependencies
        build_args = {"PYTHON_VERSION": "3.12"}
        if base_image_name:
            build_args["BASE_IMAGE"] = base_image_name
            
        self.polling_image = Image(
            f"chromadb-poll-img-{stack}",
            build=DockerBuildArgs(
                context=str(build_context_path),
                dockerfile=str(
                    Path(__file__).parent / "chromadb_polling_lambda" / "Dockerfile"
                ),
                platform="linux/arm64",
                args=build_args,
            ),
            image_name=self.polling_repo.repository_url.apply(
                lambda url: f"{url}:latest"
            ),
            registry=RegistryArgs(
                server=self.polling_repo.repository_url.apply(
                    lambda url: url.split("/")[0]
                ),
                username="AWS",
                password=ecr_auth_token.password,
            ),
            skip_push=False,
            opts=ResourceOptions(parent=self),
        )

        # Build compaction image using Pulumi Docker provider
        build_args = {"PYTHON_VERSION": "3.12"}
        if base_image_name:
            build_args["BASE_IMAGE"] = base_image_name
            
        self.compaction_image = Image(
            f"chromadb-compact-img-{stack}",
            build=DockerBuildArgs(
                context=str(build_context_path),
                dockerfile=str(
                    Path(__file__).parent / "chromadb_compaction_lambda" / "Dockerfile"
                ),
                platform="linux/arm64",
                args=build_args,
            ),
            image_name=self.compaction_repo.repository_url.apply(
                lambda url: f"{url}:latest"
            ),
            registry=RegistryArgs(
                server=self.compaction_repo.repository_url.apply(
                    lambda url: url.split("/")[0]
                ),
                username="AWS",
                password=ecr_auth_token.password,
            ),
            skip_push=False,
            opts=ResourceOptions(parent=self),
        )

        # Build line polling image using Pulumi Docker provider
        build_args = {"PYTHON_VERSION": "3.12"}
        if base_image_name:
            build_args["BASE_IMAGE"] = base_image_name
            
        self.line_polling_image = Image(
            f"chromadb-line-poll-img-{stack}",
            build=DockerBuildArgs(
                context=str(build_context_path),
                dockerfile=str(
                    Path(__file__).parent / "chromadb_line_polling_lambda" / "Dockerfile"
                ),
                platform="linux/arm64",
                args=build_args,
            ),
            image_name=self.line_polling_repo.repository_url.apply(
                lambda url: f"{url}:latest"
            ),
            registry=RegistryArgs(
                server=self.line_polling_repo.repository_url.apply(
                    lambda url: url.split("/")[0]
                ),
                username="AWS",
                password=ecr_auth_token.password,
            ),
            skip_push=False,
            opts=ResourceOptions(parent=self),
        )

        # Create IAM role for polling Lambda (shorter names to avoid 64 char limit)
        self.polling_role = Role(
            f"chromadb-poll-{stack}",
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
            f"chromadb-compact-{stack}",
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

        # Create polling Lambda function
        self.polling_lambda = Function(
            f"chromadb-poll-fn-{stack}",
            name=f"chromadb-poll-{stack}",
            package_type="Image",
            image_uri=self.polling_image.image_name,
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
            opts=ResourceOptions(parent=self),
        )

        # Create compaction Lambda function
        self.compaction_lambda = Function(
            f"chromadb-compact-fn-{stack}",
            name=f"chromadb-compact-{stack}",
            package_type="Image",
            image_uri=self.compaction_image.image_name,
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
            opts=ResourceOptions(parent=self),
        )

        # Create IAM role for line polling Lambda
        self.line_polling_role = Role(
            f"chromadb-line-poll-{stack}",
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
            f"chromadb-line-poll-fn-{stack}",
            name=f"chromadb-line-poll-{stack}",
            package_type="Image",
            image_uri=self.line_polling_image.image_name,
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
                },
            ),
            ephemeral_storage=FunctionEphemeralStorageArgs(
                size=3072,  # 3GB for temporary ChromaDB storage
            ),
            opts=ResourceOptions(parent=self),
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
            }
        )
