"""
ChromaDB containerized Lambdas with optimized Docker builds.

This v2 module improves on the original by:
1. Using docker-build provider for proper caching support
2. Using scoped build contexts (just handler files, not entire repo)
3. Properly leveraging base images
4. Implementing ECR cache_from/cache_to
"""

import json
import os
from pathlib import Path

import pulumi
import pulumi_docker_build as docker_build
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

from dynamo_db import dynamodb_table


class ChromaDBLambdasV2(ComponentResource):
    """Optimized ChromaDB Lambda component with proper caching and scoped contexts."""

    def get_base_image_for_build(
        self,
        base_image_output: Output[str],
        stack: str,
        service: str = "label",
    ) -> str:
        """Get the base image name for Docker builds.
        
        In development mode (use-static-base-image=true), returns a static image name
        for better caching. In production, uses the dynamic Pulumi output.
        """
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

    def build_lambda_image(
        self,
        name: str,
        handler_path: Path,
        dockerfile_name: str,
        repository: Repository,
        base_image_name: Output[str],
        stack: str,
        ecr_auth_token,
        parent: ComponentResource,
    ) -> docker_build.Image:
        """Build a Lambda image with optimized context and caching.
        
        This method:
        1. Uses only the handler directory as context (not entire repo)
        2. Leverages base image properly
        3. Implements ECR caching
        """
        # Create a minimal Dockerfile that uses the base image
        dockerfile_content = f"""
# Use the base image with all dependencies pre-installed
ARG BASE_IMAGE
FROM ${{BASE_IMAGE}}

# Copy only the handler code (context is just the handler directory)
COPY handler.py ${{LAMBDA_TASK_ROOT}}/

# Command to run the Lambda handler
CMD ["{dockerfile_name}"]
"""
        
        # Write the optimized Dockerfile
        dockerfile_path = handler_path / "Dockerfile.optimized"
        dockerfile_path.write_text(dockerfile_content)
        
        # Build with scoped context and caching
        return docker_build.Image(
            f"{name}-img-{stack}",
            context=docker_build.ContextArgs(
                location=str(handler_path),  # Just the handler directory!
            ),
            dockerfile=docker_build.DockerfileArgs(
                location=str(dockerfile_path),
            ),
            platforms=["linux/arm64"],
            build_args={
                "BASE_IMAGE": self.get_base_image_for_build(base_image_name, stack),
            },
            # ECR caching configuration
            cache_from=[
                docker_build.CacheFromArgs(
                    registry=docker_build.CacheFromRegistryArgs(
                        ref=repository.repository_url.apply(
                            lambda url: f"{url}:cache"
                        ),
                    ),
                ),
            ],
            cache_to=[
                docker_build.CacheToArgs(
                    registry=docker_build.CacheToRegistryArgs(
                        image_manifest=True,
                        oci_media_types=True,
                        ref=repository.repository_url.apply(
                            lambda url: f"{url}:cache"
                        ),
                    ),
                ),
            ],
            # Registry configuration for pushing
            push=True,
            registries=[
                docker_build.RegistryArgs(
                    address=repository.repository_url,
                    password=ecr_auth_token.password,
                    username=ecr_auth_token.user_name,
                ),
            ],
            # Tags for the image
            tags=[
                repository.repository_url.apply(lambda url: f"{url}:latest"),
            ],
            opts=ResourceOptions(parent=parent, depends_on=[repository]),
        )

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
        base_image_resource=None,  # Add optional resource for dependency
        opts: ResourceOptions = None,
    ):
        super().__init__(
            "custom:chromadb:ChromaDBLambdasV2",
            name,
            None,
            opts,
        )

        # Get AWS account details
        account_id = get_caller_identity().account_id
        region = config.region
        
        # Get ECR authorization token
        ecr_auth_token = get_authorization_token_output()
        
        # Base path for Lambda handlers
        base_path = Path(__file__).parent

        # Create ECR repositories
        self.polling_repo = Repository(
            f"line-embedding-poll-ecr-{stack}",
            name=f"line-embedding-poll-{stack}",
            image_scanning_configuration=RepositoryImageScanningConfigurationArgs(
                scan_on_push=True,
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )
        
        self.compaction_repo = Repository(
            f"line-embedding-compact-ecr-{stack}",
            name=f"line-embedding-compact-{stack}",
            image_scanning_configuration=RepositoryImageScanningConfigurationArgs(
                scan_on_push=True,
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )
        
        self.line_polling_repo = Repository(
            f"line-embedding-line-poll-ecr-{stack}",
            name=f"line-embedding-line-poll-{stack}",
            image_scanning_configuration=RepositoryImageScanningConfigurationArgs(
                scan_on_push=True,
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )
        
        self.find_unembedded_repo = Repository(
            f"line-find-unembedded-ecr-{stack}",
            name=f"line-find-unembedded-{stack}",
            image_scanning_configuration=RepositoryImageScanningConfigurationArgs(
                scan_on_push=True,
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )
        
        self.submit_openai_repo = Repository(
            f"line-submit-openai-ecr-{stack}",
            name=f"line-submit-openai-{stack}",
            image_scanning_configuration=RepositoryImageScanningConfigurationArgs(
                scan_on_push=True,
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )
        
        self.list_pending_repo = Repository(
            f"line-list-pending-ecr-{stack}",
            name=f"line-list-pending-{stack}",
            image_scanning_configuration=RepositoryImageScanningConfigurationArgs(
                scan_on_push=True,
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )
        
        # Build options with dependency on base image if provided
        build_opts = ResourceOptions(parent=self)
        if base_image_resource:
            build_opts = ResourceOptions(parent=self, depends_on=[base_image_resource])

        # Build images with optimized contexts
        self.polling_image = self.build_lambda_image(
            name="chromadb-poll",
            handler_path=base_path / "chromadb_word_polling_lambda",
            dockerfile_name="handler.poll_handler",
            repository=self.polling_repo,
            base_image_name=base_image_name,
            stack=stack,
            ecr_auth_token=ecr_auth_token,
            parent=self,
        )
        
        self.compaction_image = self.build_lambda_image(
            name="chromadb-compact",
            handler_path=base_path / "chromadb_compaction_lambda",
            dockerfile_name="handler.compact_handler",
            repository=self.compaction_repo,
            base_image_name=base_image_name,
            stack=stack,
            ecr_auth_token=ecr_auth_token,
            parent=self,
        )
        
        self.line_polling_image = self.build_lambda_image(
            name="chromadb-line-poll",
            handler_path=base_path / "chromadb_line_polling_lambda",
            dockerfile_name="handler.poll_line_handler",
            repository=self.line_polling_repo,
            base_image_name=base_image_name,
            stack=stack,
            ecr_auth_token=ecr_auth_token,
            parent=self,
        )
        
        self.find_unembedded_image = self.build_lambda_image(
            name="find-unembedded",
            handler_path=base_path / "find_unembedded_lines_lambda",
            dockerfile_name="handler.find_handler",
            repository=self.find_unembedded_repo,
            base_image_name=base_image_name,
            stack=stack,
            ecr_auth_token=ecr_auth_token,
            parent=self,
        )
        
        self.submit_openai_image = self.build_lambda_image(
            name="submit-openai",
            handler_path=base_path / "submit_to_openai_lambda",
            dockerfile_name="handler.submit_handler",
            repository=self.submit_openai_repo,
            base_image_name=base_image_name,
            stack=stack,
            ecr_auth_token=ecr_auth_token,
            parent=self,
        )
        
        self.list_pending_image = self.build_lambda_image(
            name="list-pending",
            handler_path=base_path / "list_pending_batches_lambda",
            dockerfile_name="handler.list_handler",
            repository=self.list_pending_repo,
            base_image_name=base_image_name,
            stack=stack,
            ecr_auth_token=ecr_auth_token,
            parent=self,
        )
        
        # Create Lambda execution role
        lambda_role = Role(
            f"chromadb-lambda-role-{stack}",
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Action": "sts:AssumeRole",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Effect": "Allow",
                }],
            }),
            opts=ResourceOptions(parent=self),
        )
        
        # Attach basic Lambda execution policy
        RolePolicyAttachment(
            f"chromadb-lambda-basic-{stack}",
            role=lambda_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self),
        )
        
        # Create policy for DynamoDB and S3 access
        lambda_policy = RolePolicy(
            f"chromadb-lambda-policy-{stack}",
            role=lambda_role.id,
            policy=Output.all(
                dynamodb_table_arn=dynamodb_table.arn,
                chromadb_bucket_name=chromadb_bucket_name,
                s3_batch_bucket_name=s3_batch_bucket_name,
                chromadb_queue_arn=chromadb_queue_arn,
            ).apply(lambda args: json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:Query",
                            "dynamodb:Scan",
                            "dynamodb:GetItem",
                            "dynamodb:PutItem",
                            "dynamodb:UpdateItem",
                            "dynamodb:BatchGetItem",
                            "dynamodb:BatchWriteItem",
                        ],
                        "Resource": [
                            args["dynamodb_table_arn"],
                            f"{args['dynamodb_table_arn']}/index/*",
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
                            f"arn:aws:s3:::{args['chromadb_bucket_name']}",
                            f"arn:aws:s3:::{args['chromadb_bucket_name']}/*",
                            f"arn:aws:s3:::{args['s3_batch_bucket_name']}",
                            f"arn:aws:s3:::{args['s3_batch_bucket_name']}/*",
                        ],
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "sqs:SendMessage",
                            "sqs:ReceiveMessage",
                            "sqs:DeleteMessage",
                            "sqs:GetQueueAttributes",
                        ],
                        "Resource": args["chromadb_queue_arn"],
                    },
                ],
            })),
            opts=ResourceOptions(parent=self),
        )
        
        # Create Lambda functions using the optimized images
        self.polling_function = Function(
            f"chromadb-poll-lambda-{stack}",
            package_type="Image",
            image_uri=self.polling_image.ref,
            role=lambda_role.arn,
            timeout=300,
            memory_size=3008,
            architectures=["arm64"],
            ephemeral_storage=FunctionEphemeralStorageArgs(size=10240),
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                    "CHROMADB_BUCKET": chromadb_bucket_name,
                    "OPENAI_API_KEY": openai_api_key,
                    "S3_BATCH_BUCKET": s3_batch_bucket_name,
                },
            ),
            opts=ResourceOptions(parent=self, depends_on=[self.polling_image]),
        )
        
        # Export function ARNs
        self.polling_function_arn = self.polling_function.arn
        
        # Create other Lambda functions similarly...
        # (Keeping this example focused on the pattern)