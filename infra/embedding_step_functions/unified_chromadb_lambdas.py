"""
Unified ChromaDB containerized Lambdas for word label step functions.

This module creates a single container image used by all Lambda functions,
with handler selection controlled by environment variables.

Benefits:
- Single Docker build instead of 6 separate builds
- Shared layers reduce ECR storage and pull times  
- Easier dependency management
- Faster deployments
"""

import json
import os
from pathlib import Path
from typing import Dict, Optional

import pulumi
from pulumi import ComponentResource, Output, ResourceOptions
from pulumi_aws import get_caller_identity, config
from pulumi_aws.ecr import (
    Repository,
    RepositoryImageScanningConfigurationArgs,
    get_authorization_token_output,
)
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment
from pulumi_aws.lambda_ import (
    Function,
    FunctionEnvironmentArgs,
    FunctionEphemeralStorageArgs,
)
import pulumi_docker_build as docker_build

from dynamo_db import dynamodb_table


class UnifiedChromaDBLambdas(ComponentResource):
    """Component for unified ChromaDB containerized Lambda functions.
    
    Creates a single container image that can handle all embedding
    step function tasks based on environment configuration.
    """

    def __init__(
        self,
        name: str,
        chromadb_bucket_name: Output[str],
        chromadb_queue_url: Output[str],
        chromadb_queue_arn: Output[str],
        openai_api_key: Output[str],
        s3_batch_bucket_name: Output[str],
        stack: str,
        base_image_name: Output[str],
        base_image_resource=None,
        opts: ResourceOptions = None,
    ):
        super().__init__(
            "custom:chromadb:UnifiedChromaDBLambdas",
            name,
            None,
            opts,
        )

        # Get AWS account details
        account_id = get_caller_identity().account_id
        region = config.region

        # Create single ECR repository for unified image
        self.unified_repo = Repository(
            f"unified-embedding-ecr-{stack}",
            name=f"unified-embedding-{stack}",
            image_scanning_configuration=(
                RepositoryImageScanningConfigurationArgs(
                    scan_on_push=True,
                )
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )

        # Get ECR authorization token
        ecr_auth_token = get_authorization_token_output()

        # Build context path - use repository root
        build_context_path = Path(__file__).parent.parent.parent

        # Build unified image
        build_args = {
            "PYTHON_VERSION": "3.12",
            "BUILDKIT_INLINE_CACHE": "1",
        }
        if base_image_name:
            build_args["BASE_IMAGE"] = self._get_base_image_for_build(
                base_image_name, stack
            )

        self.unified_image = docker_build.Image(
            f"unified-embedding-img-{stack}",
            context={
                "location": str(build_context_path.resolve()),
            },
            dockerfile={
                "location": str(
                    (build_context_path / "infra/embedding_step_functions/unified_lambda/Dockerfile").resolve()
                ),
            },
            platforms=["linux/arm64"],
            build_args=build_args,
            # ECR caching configuration
            cache_from=[
                {
                    "registry": {
                        "ref": self.unified_repo.repository_url.apply(
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
                        "ref": self.unified_repo.repository_url.apply(
                            lambda url: f"{url}:cache"
                        ),
                    },
                },
            ],
            push=True,
            registries=[
                {
                    "address": self.unified_repo.repository_url.apply(
                        lambda url: url.split("/")[0]
                    ),
                    "password": ecr_auth_token.password,
                    "username": ecr_auth_token.user_name,
                },
            ],
            tags=[
                self.unified_repo.repository_url.apply(lambda url: f"{url}:latest"),
            ],
            opts=ResourceOptions(parent=self, depends_on=[self.unified_repo]),
        )

        # Create Lambda functions with different configurations
        self._create_lambda_functions(
            stack=stack,
            region=region,
            account_id=account_id,
            chromadb_bucket_name=chromadb_bucket_name,
            chromadb_queue_url=chromadb_queue_url,
            chromadb_queue_arn=chromadb_queue_arn,
            openai_api_key=openai_api_key,
            s3_batch_bucket_name=s3_batch_bucket_name,
        )

        # Register outputs
        self.register_outputs(
            {
                "unified_image_uri": self.unified_image.tags[0],
                "polling_lambda_arn": self.polling_lambda.arn,
                "compaction_lambda_arn": self.compaction_lambda.arn,
                "line_polling_lambda_arn": self.line_polling_lambda.arn,
                "find_unembedded_lambda_arn": self.find_unembedded_lambda.arn,
                "submit_openai_lambda_arn": self.submit_openai_lambda.arn,
                "list_pending_lambda_arn": self.list_pending_lambda.arn,
            }
        )

    def _get_base_image_for_build(
        self,
        base_image_output: Output[str],
        stack: str,
        service: str = "label",
    ) -> str:
        """Get the base image name for Docker builds."""
        pulumi_config = pulumi.Config("portfolio")
        use_static = pulumi_config.get_bool("use-static-base-image")

        if use_static is None:
            use_static = os.environ.get(
                "USE_STATIC_BASE_IMAGE", ""
            ).lower() in ("true", "1", "yes")

        if use_static:
            account_id = get_caller_identity().account_id
            region = config.region or "us-east-1"
            return f"{account_id}.dkr.ecr.{region}.amazonaws.com/base-receipt-{service}-{stack}:stable"
        else:
            return base_image_output

    def _create_lambda_functions(
        self,
        stack: str,
        region: str,
        account_id: str,
        chromadb_bucket_name: Output[str],
        chromadb_queue_url: Output[str],
        chromadb_queue_arn: Output[str],
        openai_api_key: Output[str],
        s3_batch_bucket_name: Output[str],
    ):
        """Create all Lambda functions using the unified image."""
        
        # Lambda configurations
        lambda_configs = [
            {
                "name": "word-poll",
                "handler_type": "word_polling",
                "memory": 3008,
                "timeout": 900,
                "ephemeral_storage": 3072,
                "env_vars": {
                    "HANDLER_TYPE": "word_polling",
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                    "CHROMADB_BUCKET": chromadb_bucket_name,
                    "COMPACTION_QUEUE_URL": chromadb_queue_url,
                    "OPENAI_API_KEY": openai_api_key,
                    "CHROMA_PERSIST_DIRECTORY": "/tmp/chroma",
                },
            },
            {
                "name": "compact",
                "handler_type": "compaction",
                "memory": 4096,
                "timeout": 900,
                "ephemeral_storage": 5120,
                "env_vars": {
                    "HANDLER_TYPE": "compaction",
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                    "CHROMADB_BUCKET": chromadb_bucket_name,
                    "CHROMA_PERSIST_DIRECTORY": "/tmp/chroma",
                    "CHUNK_SIZE": "10",
                    "HEARTBEAT_INTERVAL_SECONDS": "60",
                    "LOCK_DURATION_MINUTES": "5",
                    "DELETE_PROCESSED_DELTAS": "false",
                    "DELETE_INTERMEDIATE_CHUNKS": "true",
                },
            },
            {
                "name": "line-poll",
                "handler_type": "line_polling",
                "memory": 3008,
                "timeout": 900,
                "ephemeral_storage": 3072,
                "env_vars": {
                    "HANDLER_TYPE": "line_polling",
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                    "CHROMADB_BUCKET": chromadb_bucket_name,
                    "COMPACTION_QUEUE_URL": chromadb_queue_url,
                    "OPENAI_API_KEY": openai_api_key,
                    "CHROMA_PERSIST_DIRECTORY": "/tmp/chroma",
                    "SKIP_TABLE_VALIDATION": "true",
                },
            },
            {
                "name": "find-unembedded",
                "handler_type": "find_unembedded",
                "memory": 1024,
                "timeout": 900,
                "ephemeral_storage": 512,  # Default
                "env_vars": {
                    "HANDLER_TYPE": "find_unembedded",
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                    "S3_BUCKET": s3_batch_bucket_name,
                    "OPENAI_API_KEY": openai_api_key,
                },
            },
            {
                "name": "submit-openai",
                "handler_type": "submit_openai",
                "memory": 1024,
                "timeout": 900,
                "ephemeral_storage": 512,
                "env_vars": {
                    "HANDLER_TYPE": "submit_openai",
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                    "OPENAI_API_KEY": openai_api_key,
                },
            },
            {
                "name": "list-pending",
                "handler_type": "list_pending",
                "memory": 512,
                "timeout": 900,
                "ephemeral_storage": 512,
                "env_vars": {
                    "HANDLER_TYPE": "list_pending",
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                    "OPENAI_API_KEY": openai_api_key,
                },
            },
        ]

        # Create IAM role for all Lambda functions (can be shared or separate)
        self.lambda_role = self._create_lambda_role(
            stack, region, account_id, 
            chromadb_bucket_name, chromadb_queue_arn, s3_batch_bucket_name
        )

        # Create Lambda functions
        for config in lambda_configs:
            self._create_lambda_function(stack, config, self.lambda_role)

    def _create_lambda_role(
        self,
        stack: str,
        region: str,
        account_id: str,
        chromadb_bucket_name: Output[str],
        chromadb_queue_arn: Output[str],
        s3_batch_bucket_name: Output[str],
    ) -> Role:
        """Create IAM role with permissions for all Lambda functions."""
        
        role = Role(
            f"unified-embedding-role-{stack}",
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
            f"unified-basic-{stack}",
            role=role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self),
        )

        # Add comprehensive permissions for all handlers
        RolePolicy(
            f"unified-perms-{stack}",
            role=role.id,
            policy=Output.all(
                dynamodb_table.name,
                chromadb_bucket_name,
                chromadb_queue_arn,
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
                                    "dynamodb:GetItem",
                                    "dynamodb:Query",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:DeleteItem",
                                    "dynamodb:BatchWriteItem",
                                    "dynamodb:BatchGetItem",
                                    "dynamodb:DescribeTable",
                                ],
                                "Resource": [
                                    f"arn:aws:dynamodb:{args[4]}:{args[5]}:table/{args[0]}",
                                    f"arn:aws:dynamodb:{args[4]}:{args[5]}:table/{args[0]}/index/*",
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
                                    f"arn:aws:s3:::{args[3]}",
                                    f"arn:aws:s3:::{args[3]}/*",
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

        return role

    def _create_lambda_function(
        self,
        stack: str,
        config: Dict,
        role: Role,
    ):
        """Create a Lambda function with the specified configuration."""
        
        name = config["name"]
        
        # Create the function
        lambda_func = Function(
            f"{name}-fn-{stack}",
            name=f"{name}-{stack}",
            package_type="Image",
            image_uri=self.unified_image.tags[0],
            role=role.arn,
            architectures=["arm64"],
            memory_size=config["memory"],
            timeout=config["timeout"],
            environment=FunctionEnvironmentArgs(
                variables=config["env_vars"],
            ),
            ephemeral_storage=FunctionEphemeralStorageArgs(
                size=config["ephemeral_storage"],
            ) if config["ephemeral_storage"] > 512 else None,
            opts=ResourceOptions(parent=self, depends_on=[self.unified_image]),
        )

        # Store function reference with correct attribute name
        # Map handler types to the expected attribute names
        attr_name_map = {
            "word_polling": "polling_lambda",
            "line_polling": "line_polling_lambda", 
            "compaction": "compaction_lambda",
            "find_unembedded": "find_unembedded_lambda",
            "submit_openai": "submit_openai_lambda",
            "list_pending": "list_pending_lambda",
        }
        attr_name = attr_name_map.get(config['handler_type'], f"{config['handler_type']}_lambda")
        setattr(self, attr_name, lambda_func)