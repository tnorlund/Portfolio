"""Pulumi infrastructure for validation by merchant Step Functions."""

import json
import os

import pulumi
import pulumi_aws as aws
from dynamo_db import dynamodb_table  # pylint: disable=import-error
from infra.components.lambda_layer import dynamo_layer  # pylint: disable=import-error
from infra.components.lambda_layer import label_layer
from pulumi import (
    AssetArchive,
    ComponentResource,
    Config,
    FileAsset,
    Output,
    ResourceOptions,
)
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment
from pulumi_aws.lambda_ import Function, FunctionEnvironmentArgs
from pulumi_aws.sfn import StateMachine

config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")
pinecone_api_key = config.require_secret("PINECONE_API_KEY")
pinecone_index_name = config.require("PINECONE_INDEX_NAME")
pinecone_host = config.require("PINECONE_HOST")

code = AssetArchive(
    {
        "lambda.py": FileAsset(
            os.path.join(os.path.dirname(__file__), "lambda.py")
        )
    }
)
stack = pulumi.get_stack()


class ValidationByMerchantStepFunction(ComponentResource):
    """
    AWS Step Functions infrastructure for validation by merchant.

    This component creates a Step Function that validates word labels
    grouped by merchant. It processes receipts in batches based on their
    canonical merchant name for efficient validation.

    The workflow:
    1. ListUniqueMerchants - Lists unique merchants and their receipts
    2. ValidateLabel - Validates labels for each batch in parallel

    Infrastructure includes:
    - Two Lambda functions (list and validate)
    - Step Function state machine for orchestration
    - S3 bucket for batch processing
    - IAM roles and policies
    """

    def __init__(self, name: str, opts: ResourceOptions | None = None):
        super().__init__(
            f"{__name__}-{name}",
            "aws:stepfunctions:ValidationByMerchantStepFunction",
            {},
            opts,
        )

        stack_name = pulumi.get_stack()

        submit_lambda_role = Role(
            f"{name}-submit-lambda-role",
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
            f"{name}-lambda-basic-execution",
            role=submit_lambda_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/"
                "AWSLambdaBasicExecutionRole"
            ),
        )

        # Custom inline policy for DynamoDB access
        RolePolicy(
            f"{name}-lambda-dynamo-policy",
            role=submit_lambda_role.id,
            policy=dynamodb_table.name.apply(
                lambda table_name: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:DescribeTable",
                                    "dynamodb:GetItem",
                                    "dynamodb:BatchGetItem",
                                    "dynamodb:Query",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:BatchWriteItem",
                                ],
                                "Resource": (
                                    f"arn:aws:dynamodb:*:*:table/"
                                    f"{table_name}*"
                                ),
                            }
                        ],
                    }
                )
            ),
        )

        # Create S3 bucket for NDJSON batch files
        batch_bucket = aws.s3.Bucket(
            f"{name}-completion-batch-bucket",
            force_destroy=True,
            tags={"environment": stack_name},
            opts=ResourceOptions(parent=self),
        )

        # Define the environment variables for the lambda
        env_vars = FunctionEnvironmentArgs(
            variables={
                "DYNAMO_TABLE_NAME": dynamodb_table.name,
                "OPENAI_API_KEY": openai_api_key,
                "PINECONE_API_KEY": pinecone_api_key,
                "PINECONE_INDEX_NAME": pinecone_index_name,
                "PINECONE_HOST": pinecone_host,
                "S3_BUCKET": batch_bucket.bucket,
                "MAX_BATCH_TIMEOUT": "60",
            },
        )

        # Define the list unique merchants lambda
        list_unique_merchants_lambda = Function(
            resource_name=f"{name}-list-unique-merchants",
            role=submit_lambda_role.arn,
            runtime="python3.12",
            handler="lambda.list_handler",
            timeout=900,
            memory_size=512,
            layers=[dynamo_layer.arn, label_layer.arn],
            code=code,
            environment=env_vars,
            tags={"environment": stack_name},
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["layers"],
            ),
        )

        # Define the validate label lambda
        validate_label_lambda = Function(
            resource_name=f"{name}-validate-label",
            role=submit_lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="lambda.validate_handler",
            timeout=900,
            memory_size=512,
            layers=[dynamo_layer.arn, label_layer.arn],
            code=code,
            environment=env_vars,
            tags={"environment": stack_name},
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["layers"],
            ),
        )

        RolePolicy(
            f"{name}-lambda-s3-write-policy",
            role=submit_lambda_role.id,
            policy=batch_bucket.bucket.apply(
                lambda bucket_name: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["s3:PutObject", "s3:GetObject"],
                                "Resource": f"arn:aws:s3:::{bucket_name}/*",
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Define IAM role for SUBMIT Step Function
        submit_sfn_role = Role(
            f"{name}-submit-sfn-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "states.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )
        RolePolicy(
            f"{name}-submit-sfn-lambda-invoke-policy",
            role=submit_sfn_role.id,
            policy=Output.all(
                list_unique_merchants_lambda.arn,
                validate_label_lambda.arn,
            ).apply(
                lambda arns: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": "lambda:InvokeFunction",
                                "Resource": arns,
                            }
                        ],
                    }
                )
            ),
        )

        # Define the Validate by Merchant step function
        StateMachine(
            f"{name}-validate-by-merchant-sfn",
            role_arn=submit_sfn_role.arn,
            definition=Output.all(
                list_unique_merchants_lambda.arn, validate_label_lambda.arn
            ).apply(
                lambda arns: json.dumps(
                    {
                        "StartAt": "ListUniqueMerchants",
                        "States": {
                            "ListUniqueMerchants": {
                                "Type": "Task",
                                "Resource": arns[0],
                                "Next": "ValidateLabel",
                            },
                            "ValidateLabel": {
                                "Type": "Map",
                                "ItemsPath": "$.batches",
                                "MaxConcurrency": 10,
                                "Iterator": {
                                    "StartAt": "ValidateLabelTask",
                                    "States": {
                                        "ValidateLabelTask": {
                                            "Type": "Task",
                                            "Resource": arns[1],
                                            "End": True,
                                        },
                                    },
                                },
                                "End": True,
                            },
                        },
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )
