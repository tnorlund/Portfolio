"""
Pulumi infrastructure for Merge Receipt Lambda.

This component creates a container-based Lambda that merges multiple receipt
fragments into a single receipt with proper warping, re-runs embeddings,
waits for compaction to complete, then deletes the originals.

The Lambda can be invoked directly with:
{
    "image_id": "uuid-string",
    "receipt_ids": [2, 3],
    "dry_run": false
}

Architecture:
- Container Lambda with all receipt_* packages
- Reuses geometry and record-building utilities from combine_receipts
- Uses enhanced compactor for cleanup of deleted receipts
"""

import json
from typing import Optional

import pulumi
from pulumi import ComponentResource, Config, Output, ResourceOptions
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment

try:
    from codebuild_docker_image import CodeBuildDockerImage
except ImportError as e:
    raise ImportError(
        "CodeBuildDockerImage is required for MergeReceiptLambda. "
        "Ensure codebuild_docker_image.py is in the Pulumi project root."
    ) from e

config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")


class MergeReceiptLambda(ComponentResource):
    """
    Container Lambda for merging receipt fragments into a single receipt.

    This Lambda:
    1. Receives (image_id, receipt_ids)
    2. Reads receipt details from DynamoDB via GSI4
    3. Transforms all words to image space and calculates new bounding rect
    4. Downloads original image and creates warped receipt image
    5. Uploads warped image to S3 (raw + CDN variants)
    6. Creates new Receipt/Line/Word/Letter entities in warped space
    7. Migrates ReceiptWordLabels and ReceiptPlace
    8. Creates embeddings and waits for compaction
    9. Deletes original receipts (compactor cleans up children)

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
        raw_bucket_name: pulumi.Input[str],
        site_bucket_name: pulumi.Input[str],
        image_bucket_name: pulumi.Input[str],
        chromadb_bucket_name: pulumi.Input[str],
        chromadb_bucket_arn: pulumi.Input[str],
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
                                    "dynamodb:DeleteItem",
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

        # S3 access policy (raw, site, image, chromadb buckets)
        # The image bucket holds the original uploaded photos; the Lambda needs
        # s3:GetObject on it to download the source image for warping.
        s3_policy = RolePolicy(
            f"{name}-lambda-s3-policy",
            role=lambda_role.id,
            policy=Output.all(
                Output.from_input(raw_bucket_name),
                Output.from_input(site_bucket_name),
                Output.from_input(image_bucket_name),
                Output.from_input(chromadb_bucket_arn),
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:PutObject",
                                    "s3:ListBucket",
                                ],
                                "Resource": [
                                    f"arn:aws:s3:::{args[0]}",
                                    f"arn:aws:s3:::{args[0]}/*",
                                    f"arn:aws:s3:::{args[1]}",
                                    f"arn:aws:s3:::{args[1]}/*",
                                    f"arn:aws:s3:::{args[2]}",
                                    f"arn:aws:s3:::{args[2]}/*",
                                    args[3],
                                    f"{args[3]}/*",
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
            "timeout": 600,  # 10 minutes - image processing + embeddings + compaction wait
            "memory_size": 2048,  # Image processing needs more memory
            "ephemeral_storage": 10240,  # 10 GB /tmp for ChromaDB snapshots + deltas
            "tags": {"environment": stack},
            "environment": {
                "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                "RAW_BUCKET": raw_bucket_name,
                "SITE_BUCKET": site_bucket_name,
                "CHROMADB_BUCKET": chromadb_bucket_name,
                "OPENAI_API_KEY": openai_api_key,
            },
        }

        docker_image = CodeBuildDockerImage(
            f"{name}-img",
            dockerfile_path="infra/merge_receipt_lambda/lambdas/Dockerfile",
            build_context_path=".",
            source_paths=[
                "receipt_dynamo",
                "receipt_dynamo_stream",
                "receipt_chroma",
                "receipt_upload",
                "receipt_agent",
                "receipt_places",
            ],
            lambda_function_name=f"{name}-{stack}-merge-receipt",
            lambda_config=lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(
                parent=self, depends_on=[lambda_role, dynamodb_policy, s3_policy]
            ),
        )

        self.lambda_function = docker_image.lambda_function
        self.lambda_arn = self.lambda_function.arn
        self.lambda_role_name = lambda_role.name

        self.register_outputs(
            {
                "lambda_arn": self.lambda_arn,
                "lambda_name": self.lambda_function.name,
                "lambda_role_name": self.lambda_role_name,
            }
        )


def create_merge_receipt_lambda(
    dynamodb_table_name: pulumi.Input[str],
    dynamodb_table_arn: pulumi.Input[str],
    raw_bucket_name: pulumi.Input[str],
    site_bucket_name: pulumi.Input[str],
    image_bucket_name: pulumi.Input[str],
    chromadb_bucket_name: pulumi.Input[str],
    chromadb_bucket_arn: pulumi.Input[str],
) -> MergeReceiptLambda:
    """
    Factory function to create the Merge Receipt Lambda.

    Args:
        dynamodb_table_name: Name of the DynamoDB table
        dynamodb_table_arn: ARN of the DynamoDB table
        raw_bucket_name: Name of the raw images S3 bucket
        site_bucket_name: Name of the CDN site S3 bucket
        image_bucket_name: Name of the upload-images bucket (original photos)
        chromadb_bucket_name: Name of the ChromaDB S3 bucket
        chromadb_bucket_arn: ARN of the ChromaDB S3 bucket

    Returns:
        MergeReceiptLambda component with lambda_arn output
    """
    return MergeReceiptLambda(
        "merge-receipt",
        dynamodb_table_name=dynamodb_table_name,
        dynamodb_table_arn=dynamodb_table_arn,
        raw_bucket_name=raw_bucket_name,
        site_bucket_name=site_bucket_name,
        image_bucket_name=image_bucket_name,
        chromadb_bucket_name=chromadb_bucket_name,
        chromadb_bucket_arn=chromadb_bucket_arn,
    )
