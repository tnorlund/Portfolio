"""
Pulumi infrastructure for the Label Refresh on Text Change Lambda.

This Lambda subscribes to the ReceiptsTable DynamoDB stream. For every
MODIFY event on a ReceiptWord whose `text` attribute changed, it
re-evaluates each label on that word against the Chroma `words`
collection (same logic as `validate_word_similarity` in the MCP
server) and writes back updated `validation_status` values.

This closes the loop after a regional re-OCR: when the Mac OCR
worker updates a word's text, this Lambda automatically refreshes
the word's labels so they don't go stale.

Architecture:
- DynamoDB stream → EventSourceMapping → this Lambda
- Lambda has read-only Chroma Cloud access + read/write DynamoDB
- Filter expression keeps the Lambda invocation footprint small
- DRY_RUN env var (default false) allows safe rollout
"""

import json
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Config, Output, ResourceOptions
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment

try:
    from codebuild_docker_image import CodeBuildDockerImage
except ImportError as e:
    raise ImportError(
        "CodeBuildDockerImage is required for LabelRefreshLambda. "
        "Ensure codebuild_docker_image.py is in the Pulumi project root."
    ) from e


# Load secrets / config once at module import
_config = Config("portfolio")
_chroma_cloud_api_key = _config.require_secret("CHROMA_CLOUD_API_KEY")
_chroma_cloud_tenant = _config.get("CHROMA_CLOUD_TENANT") or ""
_chroma_cloud_database = _config.get("CHROMA_CLOUD_DATABASE") or ""


class LabelRefreshLambda(ComponentResource):
    """
    Container Lambda that re-evaluates word labels when a word's text
    changes in DynamoDB.

    Exports:
    - lambda_function: The Lambda function resource
    - lambda_arn: ARN of the Lambda
    - lambda_role_name: IAM role name
    - event_source_mapping: The stream EventSourceMapping resource
    """

    def __init__(
        self,
        name: str,
        *,
        dynamodb_table_name: pulumi.Input[str],
        dynamodb_table_arn: pulumi.Input[str],
        dynamodb_stream_arn: pulumi.Input[str],
        dry_run: bool = False,
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

        RolePolicyAttachment(
            f"{name}-lambda-basic-exec",
            role=lambda_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/"
                "AWSLambdaBasicExecutionRole"
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # ECR for container Lambda
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

        # DynamoDB: read receipt details, update labels
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
                                # Note: receipt_dynamo's `_update_entity`
                                # uses `put_item` under the hood with a
                                # condition expression, so PutItem must be
                                # allowed even though we only ever update
                                # existing rows.
                                "Action": [
                                    "dynamodb:DescribeTable",
                                    "dynamodb:GetItem",
                                    "dynamodb:PutItem",
                                    "dynamodb:Query",
                                    "dynamodb:BatchGetItem",
                                ],
                                "Resource": [arn, f"{arn}/index/*"],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # DynamoDB streams permission for the EventSourceMapping
        stream_policy = RolePolicy(
            f"{name}-lambda-stream-policy",
            role=lambda_role.id,
            policy=Output.from_input(dynamodb_stream_arn).apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:DescribeStream",
                                    "dynamodb:GetRecords",
                                    "dynamodb:GetShardIterator",
                                    "dynamodb:ListStreams",
                                ],
                                "Resource": arn,
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
            "timeout": 300,  # 5 min — generous for batches of 10 records
            "memory_size": 1024,
            "tags": {"environment": stack},
            "environment": {
                "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                "CHROMA_CLOUD_API_KEY": _chroma_cloud_api_key,
                "CHROMA_CLOUD_TENANT": _chroma_cloud_tenant,
                "CHROMA_CLOUD_DATABASE": _chroma_cloud_database,
                "DRY_RUN": "true" if dry_run else "false",
            },
        }

        docker_image = CodeBuildDockerImage(
            f"{name}-img",
            dockerfile_path="infra/label_refresh_lambda/lambdas/Dockerfile",
            build_context_path=".",
            source_paths=[
                "receipt_dynamo",
                "receipt_chroma",
                "infra/label_refresh_lambda/lambdas",
            ],
            lambda_function_name=f"{name}-{stack}-label-refresh",
            lambda_config=lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(
                parent=self,
                depends_on=[lambda_role, dynamodb_policy, stream_policy],
            ),
        )

        self.lambda_function = docker_image.lambda_function
        self.lambda_arn = self.lambda_function.arn
        self.lambda_role_name = lambda_role.name

        # ============================================================
        # DynamoDB Stream EventSourceMapping
        # ============================================================
        # Filter: only fire on MODIFY events whose SK begins with
        # RECEIPT# (ReceiptWord rows — also matches Receipt, ReceiptLine,
        # ReceiptLetter, etc., which the handler filters out via its
        # _WORD_SK_RE regex). Source of truth for the SK shape is
        # receipt_dynamo/entities/receipt_word.py:91-109:
        #   SK = "RECEIPT#{rid:05d}#LINE#{lid:05d}#WORD#{wid:05d}"
        # The AWS-side filter keeps invocation volume manageable; the
        # handler does the precise SK regex + actual text-change check.
        self.event_source_mapping = aws.lambda_.EventSourceMapping(
            f"{name}-stream-mapping",
            event_source_arn=dynamodb_stream_arn,
            function_name=self.lambda_function.arn,
            starting_position="LATEST",
            batch_size=10,
            maximum_batching_window_in_seconds=5,
            filter_criteria=aws.lambda_.EventSourceMappingFilterCriteriaArgs(
                filters=[
                    aws.lambda_.EventSourceMappingFilterCriteriaFilterArgs(
                        pattern=json.dumps(
                            {
                                "eventName": ["MODIFY"],
                                "dynamodb": {
                                    "Keys": {
                                        "SK": {"S": [{"prefix": "RECEIPT#"}]}
                                    }
                                },
                            }
                        ),
                    )
                ]
            ),
            opts=ResourceOptions(parent=self),
        )

        self.register_outputs(
            {
                "lambda_arn": self.lambda_arn,
                "lambda_name": self.lambda_function.name,
                "lambda_role_name": self.lambda_role_name,
                "event_source_mapping_uuid": self.event_source_mapping.uuid,
            }
        )


def create_label_refresh_lambda(
    dynamodb_table_name: pulumi.Input[str],
    dynamodb_table_arn: pulumi.Input[str],
    dynamodb_stream_arn: pulumi.Input[str],
    dry_run: bool = False,
) -> LabelRefreshLambda:
    """
    Factory function to create the Label Refresh Lambda.

    Args:
        dynamodb_table_name: Name of the DynamoDB ReceiptsTable
        dynamodb_table_arn: ARN of the DynamoDB ReceiptsTable
        dynamodb_stream_arn: ARN of the stream attached to the table
        dry_run: If True, the Lambda logs proposed updates but skips writes

    Returns:
        LabelRefreshLambda component
    """
    return LabelRefreshLambda(
        "label-refresh",
        dynamodb_table_name=dynamodb_table_name,
        dynamodb_table_arn=dynamodb_table_arn,
        dynamodb_stream_arn=dynamodb_stream_arn,
        dry_run=dry_run,
    )
