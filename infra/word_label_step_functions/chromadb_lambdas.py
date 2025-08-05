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

import hashlib
import json
from pathlib import Path
from typing import List, Optional

import pulumi
from pulumi import ComponentResource, Output, ResourceOptions
from pulumi_aws import get_caller_identity, config, s3
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
from pulumi_command import local
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
        force_rebuild: bool = False,
        opts: ResourceOptions = None,
    ):
        super().__init__(
            "custom:chromadb:ChromaDBLambdas", 
            name,
            None,  # props - ComponentResource doesn't need props
            opts
        )
        
        self.force_rebuild = force_rebuild  # Allow forcing rebuilds if needed

        # Get AWS account details
        account_id = get_caller_identity().account_id
        region = config.region
        
        # Create S3 bucket for build hashes and artifacts
        self.build_cache_bucket = s3.Bucket(
            f"{name}-build-cache",
            force_destroy=True,
            opts=ResourceOptions(parent=self),
        )

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
        
        # Create polling Lambda image with hash-based skip
        polling_hash = self._calculate_docker_context_hash(
            build_context_path,
            Path(__file__).parent / "chromadb_polling_lambda" / "Dockerfile",
            include_patterns=[
                "receipt_label/**/*.py",
                "receipt_dynamo/**/*.py",
                "infra/word_label_step_functions/chromadb_polling_lambda/**",
            ],
        )
        
        # Check if we need to rebuild polling image
        self.polling_build_check = local.Command(
            f"chromadb-poll-check-{stack}",
            create=Output.all(
                self.build_cache_bucket.bucket,
                self.polling_repo.repository_url,
            ).apply(
                lambda args: self._create_build_check_script(
                    args[0], "chromadb-poll", polling_hash, args[1], self.force_rebuild
                )
            ),
            opts=ResourceOptions(parent=self),
        )
        
        # Build polling image only if needed
        self.polling_image = self._create_conditional_image(
            f"chromadb-poll-img-{stack}",
            build_context_path,
            Path(__file__).parent / "chromadb_polling_lambda" / "Dockerfile",
            self.polling_repo,
            ecr_auth_token,
            polling_hash,
            build_args={"PYTHON_VERSION": "3.12"},
            depends_on=[self.polling_build_check],
        )

        # Create compaction Lambda image with hash-based skip
        compaction_hash = self._calculate_docker_context_hash(
            build_context_path,
            Path(__file__).parent / "chromadb_compaction_lambda" / "Dockerfile",
            include_patterns=[
                "infra/word_label_step_functions/chromadb_compaction_lambda/**",
            ],
        )
        
        # Check if we need to rebuild compaction image
        self.compaction_build_check = local.Command(
            f"chromadb-compact-check-{stack}",
            create=Output.all(
                self.build_cache_bucket.bucket,
                self.compaction_repo.repository_url,
            ).apply(
                lambda args: self._create_build_check_script(
                    args[0], "chromadb-compact", compaction_hash, args[1], self.force_rebuild
                )
            ),
            opts=ResourceOptions(parent=self),
        )
        
        # Build compaction image only if needed
        self.compaction_image = self._create_conditional_image(
            f"chromadb-compact-img-{stack}",
            build_context_path,
            Path(__file__).parent / "chromadb_compaction_lambda" / "Dockerfile",
            self.compaction_repo,
            ecr_auth_token,
            compaction_hash,
            build_args={"PYTHON_VERSION": "3.12"},
            depends_on=[self.compaction_build_check],
        )

        # Create line polling Lambda image with hash-based skip
        line_polling_hash = self._calculate_docker_context_hash(
            build_context_path,
            Path(__file__).parent / "chromadb_line_polling_lambda" / "Dockerfile",
            include_patterns=[
                "receipt_label/**/*.py",
                "receipt_dynamo/**/*.py",
                "infra/word_label_step_functions/chromadb_line_polling_lambda/**",
            ],
        )
        
        # Check if we need to rebuild line polling image
        self.line_polling_build_check = local.Command(
            f"chromadb-line-poll-check-{stack}",
            create=Output.all(
                self.build_cache_bucket.bucket,
                self.line_polling_repo.repository_url,
            ).apply(
                lambda args: self._create_build_check_script(
                    args[0], "chromadb-line-poll", line_polling_hash, args[1], self.force_rebuild
                )
            ),
            opts=ResourceOptions(parent=self),
        )
        
        # Build line polling image only if needed
        self.line_polling_image = self._create_conditional_image(
            f"chromadb-line-poll-img-{stack}",
            build_context_path,
            Path(__file__).parent / "chromadb_line_polling_lambda" / "Dockerfile",
            self.line_polling_repo,
            ecr_auth_token,
            line_polling_hash,
            build_args={"PYTHON_VERSION": "3.12"},
            depends_on=[self.line_polling_build_check],
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
            image_uri=self.polling_image,
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
            image_uri=self.compaction_image,
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
            image_uri=self.line_polling_image,
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
    
    def _calculate_docker_context_hash(
        self,
        context_path: Path,
        dockerfile_path: Path,
        include_patterns: List[str],
    ) -> str:
        """Calculate hash of Docker build context to detect changes."""
        hash_obj = hashlib.sha256()
        
        # Hash the Dockerfile
        if dockerfile_path.exists():
            with open(dockerfile_path, "rb") as f:
                hash_obj.update(f.read())
        
        # Hash all relevant files in the context
        files_to_hash = []
        for pattern in include_patterns:
            pattern_path = context_path / pattern
            if "**" in pattern:
                # Handle glob patterns
                base_path = pattern_path.parent
                glob_pattern = pattern_path.name
                if base_path.exists():
                    files_to_hash.extend(base_path.glob(glob_pattern))
            elif pattern_path.exists():
                if pattern_path.is_file():
                    files_to_hash.append(pattern_path)
                else:
                    files_to_hash.extend(pattern_path.rglob("*"))
        
        # Sort for consistent ordering
        for file_path in sorted(set(f for f in files_to_hash if f.is_file())):
            # Hash file content
            with open(file_path, "rb") as f:
                hash_obj.update(f.read())
            # Hash relative path
            rel_path = file_path.relative_to(context_path)
            hash_obj.update(str(rel_path).encode())
        
        return hash_obj.hexdigest()
    
    def _create_build_check_script(
        self,
        bucket_name: str,
        image_name: str,
        context_hash: str,
        ecr_repo_url: str,
        force_rebuild: bool,
    ) -> str:
        """Create script that checks if Docker build is needed."""
        return f"""#!/bin/bash
set -e

BUCKET="{bucket_name}"
IMAGE_NAME="{image_name}"
HASH="{context_hash}"
ECR_REPO="{ecr_repo_url}"
FORCE_REBUILD="{str(force_rebuild).lower()}"

echo "🔍 Checking if Docker build needed for $IMAGE_NAME..."

if [ "$FORCE_REBUILD" = "true" ]; then
    echo "🔨 Force rebuild enabled"
    echo "NEEDS_BUILD" > /tmp/${{IMAGE_NAME}}_build_status.txt
    exit 0
fi

# Check stored hash
STORED_HASH=$(aws s3 cp s3://$BUCKET/docker-hashes/$IMAGE_NAME/hash.txt - 2>/dev/null || echo '')

if [ "$STORED_HASH" = "$HASH" ]; then
    echo "✅ No changes detected (hash: ${{HASH:0:12}}...)"
    
    # Verify image exists in ECR
    REPO_NAME=$(echo $ECR_REPO | awk -F'/' '{{print $NF}}')
    if aws ecr describe-images --repository-name "$REPO_NAME" --image-ids imageTag=latest &>/dev/null; then
        echo "✅ Image exists in ECR. Skipping build."
        echo "SKIP_BUILD" > /tmp/${{IMAGE_NAME}}_build_status.txt
        exit 0
    else
        echo "⚠️  Image missing from ECR. Will rebuild."
    fi
else
    echo "📝 Changes detected. Will build new image."
    if [ -n "$STORED_HASH" ]; then
        echo "   Old hash: ${{STORED_HASH:0:12}}..."
    fi
    echo "   New hash: ${{HASH:0:12}}..."
fi

echo "NEEDS_BUILD" > /tmp/${{IMAGE_NAME}}_build_status.txt
"""
    
    def _create_conditional_image(
        self,
        name: str,
        context_path: Path,
        dockerfile_path: Path,
        ecr_repository: Repository,
        ecr_auth_token,
        context_hash: str,
        build_args: dict = None,
        depends_on: List = None,
    ) -> Output[str]:
        """Create Docker image only if needed based on hash check."""
        
        # Store build command as instance variable for dependency chaining
        build_command = local.Command(
            f"{name}-build",
            create=Output.all(
                ecr_repository.repository_url,
                self.build_cache_bucket.bucket,
            ).apply(
                lambda args: f"""#!/bin/bash
set -e

# Enable verbose output for debugging
set -x

# Add error trap
trap 'echo "Error occurred at line $LINENO for $IMAGE_NAME build"; exit 1' ERR

ECR_REPO="{args[0]}"
BUCKET="{args[1]}"
HASH="{context_hash}"
IMAGE_NAME="{name}"

echo "Starting build for $IMAGE_NAME"
echo "ECR Repository: $ECR_REPO"
echo "Dockerfile: {dockerfile_path}"
echo "Context: {context_path}"

# Add a small random delay to reduce likelihood of simultaneous builds
DELAY=$((RANDOM % 5))
echo "Starting build in $DELAY seconds to avoid conflicts..."
sleep $DELAY

# Check build status
BUILD_STATUS=$(cat /tmp/${{IMAGE_NAME}}_build_status.txt 2>/dev/null || echo "NEEDS_BUILD")

if [ "$BUILD_STATUS" = "SKIP_BUILD" ]; then
    echo "⚡ Skipping build - no changes detected"
    echo "$ECR_REPO:latest"
    exit 0
fi

echo "🏗️  Building Docker image $IMAGE_NAME..."

# Log in to ECR
ECR_REGISTRY=$(echo "$ECR_REPO" | cut -d'/' -f1)
echo "🔐 Logging into ECR registry: $ECR_REGISTRY"

# Handle macOS keychain credential error gracefully
if ! aws ecr get-login-password | docker login --username AWS --password-stdin "$ECR_REGISTRY" 2>/dev/null; then
    echo "⚠️  Docker login failed, trying to clear existing credentials..."
    docker logout "$ECR_REGISTRY" >/dev/null 2>&1 || true
    # Try login again
    aws ecr get-login-password | docker login --username AWS --password-stdin "$ECR_REGISTRY"
fi

# Use a unique local tag to avoid conflicts
LOCAL_TAG="${{IMAGE_NAME}}-${{RANDOM}}"

# Build the image
docker build \
    -t "$LOCAL_TAG:latest" \
    -f "{dockerfile_path}" \
    {' '.join(f'--build-arg {k}={v}' for k, v in (build_args or {}).items())} \
    --platform linux/arm64 \
    "{context_path}"

# Tag for ECR
docker tag "$LOCAL_TAG:latest" "$ECR_REPO:latest"

# Push to ECR
echo "📤 Pushing to ECR..."
docker push "$ECR_REPO:latest"

# Verify the image was pushed successfully
echo "🔍 Verifying image exists in ECR..."
if aws ecr describe-images --repository-name $(echo "$ECR_REPO" | cut -d'/' -f2) --image-ids imageTag=latest >/dev/null 2>&1; then
    echo "✅ Image successfully verified in ECR"
else
    echo "❌ Failed to verify image in ECR"
    exit 1
fi

# Clean up local image
docker rmi "$LOCAL_TAG:latest" || true

# Save hash to S3
echo "$HASH" | aws s3 cp - "s3://$BUCKET/docker-hashes/${{IMAGE_NAME}}/hash.txt"

echo "✅ Docker image built and pushed successfully"
echo "$ECR_REPO:latest"
"""
            ),
            opts=ResourceOptions(parent=self, depends_on=depends_on or []),
        )
        
        # Return the image URI
        return Output.concat(ecr_repository.repository_url, ":latest")
