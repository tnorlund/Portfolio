"""Pulumi infrastructure for the receipt re-segmentation Lambda."""

import json
from typing import Optional

import pulumi
from pulumi import ComponentResource, Config, Output, ResourceOptions
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment

from codebuild_docker_image import CodeBuildDockerImage

config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")


# Pulumi type token for the component. Pinned to the value f"{__name__}-{name}"
# historically produced when this module is imported as
# ``resegment_receipt_lambda.infrastructure`` (the Pulumi program's import
# path), so existing stack URNs are unchanged. Deriving the token from
# ``__name__`` made every child URN depend on the Python import layout: a
# deploy that imported this module under a different path (e.g. as
# ``infra.resegment_receipt_lambda.infrastructure``) re-keyed the entire
# subtree and minted a second physical generation of pipeline/repo/builder.
_COMPONENT_TYPE_TOKEN = (
    "resegment_receipt_lambda.infrastructure-resegment-receipt"
)


class ResegmentReceiptLambda(ComponentResource):
    """Container Lambda for planning and applying receipt splits."""

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
        super().__init__(_COMPONENT_TYPE_TOKEN, name, None, opts)
        stack = pulumi.get_stack()

        role = Role(
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
        RolePolicyAttachment(
            f"{name}-lambda-basic-exec",
            role=role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/" "AWSLambdaBasicExecutionRole"
            ),
            opts=ResourceOptions(parent=role),
        )
        RolePolicy(
            f"{name}-lambda-ecr-policy",
            role=role.id,
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
            opts=ResourceOptions(parent=role),
        )

        dynamodb_policy = RolePolicy(
            f"{name}-lambda-dynamo-policy",
            role=role.id,
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
                                    "dynamodb:TransactWriteItems",
                                ],
                                "Resource": [arn, f"{arn}/index/*"],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=role),
        )

        s3_policy = RolePolicy(
            f"{name}-lambda-s3-policy",
            role=role.id,
            policy=Output.all(
                raw_bucket_name,
                site_bucket_name,
                image_bucket_name,
                chromadb_bucket_arn,
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
                                    "s3:DeleteObject",
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
            opts=ResourceOptions(parent=role),
        )

        docker_image = CodeBuildDockerImage(
            f"{name}-img",
            dockerfile_path=("infra/resegment_receipt_lambda/lambdas/Dockerfile"),
            build_context_path=".",
            source_paths=[
                "receipt_dynamo",
                "receipt_dynamo_stream",
                "receipt_chroma",
                "receipt_upload",
                "receipt_places",
            ],
            lambda_function_name=f"{name}-{stack}-resegment-receipt",
            lambda_config={
                "role_arn": role.arn,
                "timeout": 900,
                "memory_size": 3072,
                "ephemeral_storage": 10240,
                "tags": {"environment": stack},
                "environment": {
                    "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                    "RAW_BUCKET": raw_bucket_name,
                    "SITE_BUCKET": site_bucket_name,
                    "CHROMADB_BUCKET": chromadb_bucket_name,
                    "OPENAI_API_KEY": openai_api_key,
                },
            },
            platform="linux/arm64",
            opts=ResourceOptions(
                parent=self,
                depends_on=[role, dynamodb_policy, s3_policy],
            ),
        )

        self.lambda_function = docker_image.lambda_function
        self.lambda_arn = self.lambda_function.arn
        self.register_outputs(
            {
                "lambda_arn": self.lambda_arn,
                "lambda_name": self.lambda_function.name,
            }
        )


def create_resegment_receipt_lambda(
    dynamodb_table_name: pulumi.Input[str],
    dynamodb_table_arn: pulumi.Input[str],
    raw_bucket_name: pulumi.Input[str],
    site_bucket_name: pulumi.Input[str],
    image_bucket_name: pulumi.Input[str],
    chromadb_bucket_name: pulumi.Input[str],
    chromadb_bucket_arn: pulumi.Input[str],
) -> ResegmentReceiptLambda:
    """Create the receipt re-segmentation component."""
    return ResegmentReceiptLambda(
        "resegment-receipt",
        dynamodb_table_name=dynamodb_table_name,
        dynamodb_table_arn=dynamodb_table_arn,
        raw_bucket_name=raw_bucket_name,
        site_bucket_name=site_bucket_name,
        image_bucket_name=image_bucket_name,
        chromadb_bucket_name=chromadb_bucket_name,
        chromadb_bucket_arn=chromadb_bucket_arn,
    )
