import json
import os

import pulumi
import pulumi_aws as aws
from dynamo_db import dynamodb_table
from infra.components.lambda_layer import dynamo_layer, label_layer
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


class ValidationPipeline(ComponentResource):
    def __init__(self, name: str, opts: ResourceOptions = None):
        super().__init__(
            f"{__name__}-{name}",
            "aws:stepfunctions:ValidationPipeline",
            {},
            opts,
        )

        stack = pulumi.get_stack()

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
                                    "arn:aws:dynamodb:*:*:table/"
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
            tags={"environment": stack},
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
                "MAX_BATCH_TIMEOUT": 60,
            },
        )

        # Define the POLL list lambda
        poll_completion_list_lambda = Function(
            f"{name}-poll-completion-list-lambda",
            role=submit_lambda_role.arn,
            runtime="python3.12",
            handler="lambda.poll_list_handler",
            timeout=900,
            memory_size=512,
            layers=[dynamo_layer.arn, label_layer.arn],
            code=code,
            environment=env_vars,
            opts=ResourceOptions(parent=self),
            tags={"environment": stack},
        )

        # Define the POLL download lambda
        poll_completion_download_lambda = Function(
            f"{name}-poll-completion-download-lambda",
            role=submit_lambda_role.arn,
            runtime="python3.12",
            handler="lambda.poll_download_handler",
            timeout=900,
            memory_size=512,
            layers=[dynamo_layer.arn, label_layer.arn],
            code=code,
            environment=env_vars,
            tags={"environment": stack},
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["layers"],
            ),
        )

        # Define the SUBMIT list lambda
        submit_completion_list_lambda = Function(
            f"{name}-submit-completion-list-lambda",
            role=submit_lambda_role.arn,
            runtime="python3.12",
            handler="lambda.submit_list_handler",
            timeout=900,
            memory_size=512,
            code=code,
            environment=env_vars,
            layers=[dynamo_layer.arn, label_layer.arn],
            tags={"environment": stack},
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["layers"],
            ),
        )

        # Define the SUBMIT format lambda
        submit_completion_format_lambda = Function(
            f"{name}-submit-completion-format-lambda",
            role=submit_lambda_role.arn,
            runtime="python3.12",
            handler="lambda.submit_format_handler",
            timeout=900,
            memory_size=512,
            code=code,
            environment=env_vars,
            layers=[dynamo_layer.arn, label_layer.arn],
            tags={"environment": stack},
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["layers"],
            ),
        )

        # Define the SUBMIT openai lambda
        submit_completion_openai_lambda = Function(
            f"{name}-submit-completion-openai-lambda",
            role=submit_lambda_role.arn,
            runtime="python3.12",
            handler="lambda.submit_openai_handler",
            timeout=900,
            memory_size=512,
            code=code,
            environment=env_vars,
            layers=[dynamo_layer.arn, label_layer.arn],
            tags={"environment": stack},
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

        # Define IAM role for POLL Step Function
        poll_sfn_role = Role(
            f"{name}-poll-sfn-role",
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
            f"{name}-poll-sfn-lambda-invoke-policy",
            role=poll_sfn_role.id,
            policy=Output.all(
                poll_completion_list_lambda.arn,
                poll_completion_download_lambda.arn,
                submit_completion_list_lambda.arn,
                submit_completion_format_lambda.arn,
                submit_completion_openai_lambda.arn,
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

        # Define the POLL completion batch Step Function
        StateMachine(
            f"{name}-poll-completion-batch-sm",
            role_arn=poll_sfn_role.arn,
            definition=Output.all(
                poll_completion_list_lambda.arn,
                poll_completion_download_lambda.arn,
            ).apply(
                lambda arns: json.dumps(
                    {
                        "StartAt": "ListPendingBatches",
                        "States": {
                            "ListPendingBatches": {
                                "Type": "Task",
                                "Resource": arns[0],
                                "Next": "PollCompletionDownload",
                            },
                            "PollCompletionDownload": {
                                "Type": "Map",
                                "ItemsPath": "$.batches",
                                "MaxConcurrency": 10,
                                "Iterator": {
                                    "StartAt": "PollCompletionDownloadTask",
                                    "States": {
                                        "PollCompletionDownloadTask": {
                                            "Type": "Task",
                                            "Resource": arns[1],
                                            "End": True,
                                        }
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

        # Define the SUBMIT completion batch Step Function
        StateMachine(
            f"{name}-submit-completion-batch-sm",
            role_arn=poll_sfn_role.arn,
            definition=Output.all(
                submit_completion_list_lambda.arn,
                submit_completion_format_lambda.arn,
                submit_completion_openai_lambda.arn,
            ).apply(
                lambda arns: json.dumps(
                    {
                        "StartAt": "SubmitCompletionList",
                        "States": {
                            "SubmitCompletionList": {
                                "Type": "Task",
                                "Resource": arns[0],
                                "Next": "FormatNDJSONs",
                            },
                            "FormatNDJSONs": {
                                "Type": "Map",
                                "ItemsPath": "$.batches",
                                "MaxConcurrency": 100,
                                "Iterator": {
                                    "StartAt": "FormatNDJSON",
                                    "States": {
                                        "FormatNDJSON": {
                                            "Type": "Task",
                                            "Resource": arns[1],
                                            "Next": "DoneFormatting",
                                        },
                                        "DoneFormatting": {"Type": "Succeed"},
                                    },
                                },
                                "Next": "BatchLarger",
                            },
                            "BatchLarger": {
                                "Type": "Task",
                                "Resource": arns[2],
                                "InputPath": "$",
                                "Parameters": {"s3_keys.$": "$[*].s3_key"},
                                "End": True,
                            },
                        },
                        # End the state machine after BatchLarger
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )
