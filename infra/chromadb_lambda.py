"""
ChromaDB Lambda deployment using container images.

This module creates a Lambda function that runs ChromaDB in a container,
bypassing the 50MB layer size limit.
"""

import json
from pathlib import Path

import pulumi
from pulumi_docker import (
    DockerBuildArgs,
    Image,
    RegistryArgs,
)
from pulumi_aws import get_caller_identity, config
from pulumi_aws.ecr import (
    Repository,
    RepositoryImageScanningConfigurationArgs,
    get_authorization_token,
)
from pulumi_aws.iam import (
    Role,
    RolePolicy,
    RolePolicyAttachment,
)
from pulumi_aws.lambda_ import (
    Function,
    FunctionEnvironmentArgs,
    FunctionEphemeralStorageArgs,
)

# Import the DynamoDB table name from the dynamo_db module
from dynamo_db import dynamodb_table
from pulumi_aws.s3 import Bucket

# Get AWS account details
account_id = get_caller_identity().account_id
region = config.region

# Create ECR repository for ChromaDB images
chroma_repo = Repository(
    "chromadb-lambda-repo",
    name="chromadb-lambda",
    image_scanning_configuration=(
        RepositoryImageScanningConfigurationArgs(
            scan_on_push=True,
        )
    ),
    force_delete=True,
)

# Get ECR authorization token
ecr_auth_token = get_authorization_token()

# Create S3 bucket for ChromaDB persistence
chroma_s3_bucket = Bucket(
    "chromadb-storage",
    bucket=f"chromadb-storage-{pulumi.get_stack()}-{account_id}",
    force_destroy=True,  # Be careful with this in production
    versioning={
        "enabled": True,  # Enable versioning for safety
    },
)

# Create Docker image for ChromaDB Lambda
chroma_image = Image(
    "chromadb-lambda-image",
    build=DockerBuildArgs(
        context=str(Path(__file__).parent / "chromadb_lambda"),
        dockerfile="Dockerfile",
        platform="linux/arm64",  # Match your Lambda architecture
        args={
            "PYTHON_VERSION": "3.12",
        },
    ),
    image_name=pulumi.Output.concat(chroma_repo.repository_url, ":latest"),
    registry=RegistryArgs(
        server=chroma_repo.repository_url,
        username=ecr_auth_token.user_name,
        password=ecr_auth_token.password,
    ),
)

# Create IAM role for ChromaDB Lambda
chroma_lambda_role = Role(
    "chromadb-lambda-role",
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
)

# Attach basic Lambda execution policy
RolePolicyAttachment(
    "chromadb-lambda-basic-policy",
    role=chroma_lambda_role.name,
    policy_arn=(
        "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
    ),
)

# Add DynamoDB and S3 access for ChromaDB
chroma_policy = RolePolicy(
    "chromadb-lambda-policy",
    role=chroma_lambda_role.id,
    policy=pulumi.Output.all(
        dynamodb_table.name,
        chroma_s3_bucket.id
    ).apply(lambda args: json.dumps(
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
                    ],
                    "Resource": [
                        f"arn:aws:dynamodb:{region}:{account_id}:table/{args[0]}",
                        f"arn:aws:dynamodb:{region}:{account_id}:table/{args[0]}/index/*",
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
    )),
)

# Create Lambda function from container image
chroma_lambda = Function(
    "chromadb-lambda",
    name=f"chromadb-lambda-{pulumi.get_stack()}",
    package_type="Image",
    image_uri=chroma_image.image_name,
    role=chroma_lambda_role.arn,
    architectures=["arm64"],
    memory_size=2048,  # ChromaDB needs memory
    timeout=300,  # 5 minutes
    environment=FunctionEnvironmentArgs(
        variables={
            "CHROMA_PERSIST_DIRECTORY": "/tmp/chroma",
            "CHROMA_S3_BUCKET": chroma_s3_bucket.id,
            "DYNAMODB_TABLE_NAME": dynamodb_table.name,
        },
    ),
    ephemeral_storage=FunctionEphemeralStorageArgs(
        size=2048,  # 2GB ephemeral storage for vector index
    ),
)

# Export the Lambda function details
pulumi.export("chromadb_lambda_arn", chroma_lambda.arn)
pulumi.export("chromadb_lambda_name", chroma_lambda.name)
pulumi.export("chromadb_s3_bucket", chroma_s3_bucket.id)
