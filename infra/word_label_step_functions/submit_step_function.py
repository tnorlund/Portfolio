import json
import os

import pulumi
import pulumi_aws as aws
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

"""
submit_batch.py

This module handles the preparation, formatting, submission, and tracking of
embedding batch jobs to OpenAI's Batch API. It includes functionality to:

- Fetch ReceiptWordLabel and ReceiptWord entities from DynamoDB
- Join and structure the data into OpenAI-compatible embedding requests
- Write these requests to an NDJSON file
- Upload the NDJSON file to S3 and OpenAI
- Submit the batch embedding job to OpenAI
- Track job metadata and store summaries in DynamoDB

This script supports agentic document labeling and validation pipelines
by facilitating scalable embedding of labeled receipt tokens.
"""


from dynamo_db import dynamodb_table
from lambda_layer import dynamo_layer, label_layer

config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")
pinecone_api_key = config.require_secret("PINECONE_API_KEY")
pinecone_index_name = config.require("PINECONE_INDEX_NAME")
pinecone_host = config.require("PINECONE_HOST")
stack = pulumi.get_stack()


class WordLabelStepFunctions(ComponentResource):
    def __init__(self, name: str, opts: ResourceOptions = None):
        super().__init__(
            f"{__name__}-{name}", "aws:stepfunctions:StateMachine", opts
        )

        # Define IAM role for Lambda
        submit_lambda_role = Role(
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

        # Create S3 bucket for NDJSON batch files
        batch_bucket = aws.s3.Bucket(
            f"{name}-embedding-batch-bucket",
            acl="private",
            force_destroy=True,
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        # Define the poll lambda
        poll_embedding_batch_lambda = Function(
            f"{name}-poll-embedding-batch-lambda",
            role=submit_lambda_role.arn,
            runtime="python3.12",
            handler="poll_single_batch.poll_handler",
            timeout=900,
            memory_size=512,
            code=AssetArchive(
                {
                    "poll_single_batch.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "poll_single_batch.py",
                        )
                    )
                }
            ),
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMO_TABLE_NAME": dynamodb_table.name,
                    "OPENAI_API_KEY": openai_api_key,
                    "PINECONE_API_KEY": pinecone_api_key,
                    "PINECONE_INDEX_NAME": pinecone_index_name,
                    "PINECONE_HOST": pinecone_host,
                    "S3_BUCKET": batch_bucket.bucket,
                    # Fix OpenTelemetry context initialization in Lambda
                    "OTEL_PYTHON_CONTEXT": "contextvars_context",  # Explicitly set context implementation
                    # Disable ChromaDB telemetry to avoid issues
                    "CHROMA_TELEMETRY": "false",
                    "ANONYMIZED_TELEMETRY": "false",
                }
            ),
            layers=[label_layer.arn],  # receipt-label includes receipt-dynamo
            tags={"environment": stack},
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["layers"],
            ),
        )

        # Define IAM role for poll Step Function
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

        # Define the list pending batches lambda
        list_pending_batches_lambda = Function(
            f"{name}-list-pending-batches-lambda",
            role=submit_lambda_role.arn,
            runtime="python3.12",
            handler="list_pending_batches_for_polling.list_handler",
            timeout=900,
            memory_size=512,
            code=AssetArchive(
                {
                    "list_pending_batches_for_polling.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "list_pending_batches_for_polling.py",
                        )
                    )
                }
            ),
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMO_TABLE_NAME": dynamodb_table.name,
                    "OPENAI_API_KEY": openai_api_key,
                    "PINECONE_API_KEY": pinecone_api_key,
                    "PINECONE_INDEX_NAME": pinecone_index_name,
                    "PINECONE_HOST": pinecone_host,
                    "S3_BUCKET": batch_bucket.bucket,
                    # Fix OpenTelemetry context initialization in Lambda
                    "OTEL_PYTHON_CONTEXT": "contextvars_context",  # Explicitly set context implementation
                    # Disable ChromaDB telemetry to avoid issues
                    "CHROMA_TELEMETRY": "false",
                    "ANONYMIZED_TELEMETRY": "false",
                }
            ),
            layers=[label_layer.arn],  # receipt-label includes receipt-dynamo
            tags={"environment": stack},
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["layers"],
            ),
        )
        RolePolicy(
            f"{name}-poll-sfn-lambda-invoke-policy",
            role=poll_sfn_role.id,
            policy=Output.all(
                list_pending_batches_lambda.arn,
                poll_embedding_batch_lambda.arn,
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

        # Define the poll embedding batch Step Function
        StateMachine(
            f"{name}-poll-embedding-batch-sm",
            role_arn=poll_sfn_role.arn,
            definition=Output.all(
                poll_embedding_batch_lambda.arn,
                list_pending_batches_lambda.arn,
            ).apply(
                lambda arns: json.dumps(
                    {
                        "StartAt": "ListPendingBatches",
                        "States": {
                            "ListPendingBatches": {
                                "Type": "Task",
                                "Resource": arns[1],
                                "Next": "PollEmbeddingBatch",
                            },
                            "PollEmbeddingBatch": {
                                "Type": "Map",
                                "ItemsPath": "$.body",
                                "MaxConcurrency": 10,
                                "Parameters": {
                                    "batch_id.$": "$$.Map.Item.Value.batch_id",
                                    "openai_batch_id.$": "$$.Map.Item.Value.openai_batch_id",
                                },
                                "Iterator": {
                                    "StartAt": "PollEmbeddingBatchTask",
                                    "States": {
                                        "PollEmbeddingBatchTask": {
                                            "Type": "Task",
                                            "Resource": arns[0],
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

        # Define the prepare lambda
        prepare_batch_lambda = Function(
            f"{name}-prepare-embedding-batch-lambda",
            role=submit_lambda_role.arn,
            runtime="python3.12",
            handler="prepare_embedding_batch_handler.submit_handler",
            timeout=900,
            memory_size=512,
            architectures=["arm64"],
            code=AssetArchive(
                {
                    "prepare_embedding_batch_handler.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "prepare_embedding_batch_handler.py",
                        )
                    )
                }
            ),
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMO_TABLE_NAME": dynamodb_table.name,
                    "OPENAI_API_KEY": openai_api_key,
                    "PINECONE_API_KEY": pinecone_api_key,
                    "PINECONE_INDEX_NAME": pinecone_index_name,
                    "PINECONE_HOST": pinecone_host,
                    "S3_BUCKET": batch_bucket.bucket,
                    # Fix OpenTelemetry context initialization in Lambda
                    "OTEL_PYTHON_CONTEXT": "contextvars_context",  # Explicitly set context implementation
                    # Disable ChromaDB telemetry to avoid issues
                    "CHROMA_TELEMETRY": "false",
                    "ANONYMIZED_TELEMETRY": "false",
                }
            ),
            layers=[label_layer.arn],  # receipt-label includes receipt-dynamo
            tags={"environment": stack},
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["layers"],
            ),
        )

        # Define the submit lambda
        submit_batch_lambda = Function(
            f"{name}-submit-embedding-batch-lambda",
            role=submit_lambda_role.arn,
            runtime="python3.12",
            handler="submit_embedding_batch_handler.submit_handler",
            timeout=900,
            memory_size=512,
            architectures=["arm64"],
            code=AssetArchive(
                {
                    "submit_embedding_batch_handler.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "submit_embedding_batch_handler.py",
                        )
                    )
                }
            ),
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMO_TABLE_NAME": dynamodb_table.name,
                    "OPENAI_API_KEY": openai_api_key,
                    "PINECONE_API_KEY": pinecone_api_key,
                    "PINECONE_INDEX_NAME": pinecone_index_name,
                    "PINECONE_HOST": pinecone_host,
                    "S3_BUCKET": batch_bucket.bucket,
                    # Fix OpenTelemetry context initialization in Lambda
                    "OTEL_PYTHON_CONTEXT": "contextvars_context",  # Explicitly set context implementation
                    # Disable ChromaDB telemetry to avoid issues
                    "CHROMA_TELEMETRY": "false",
                    "ANONYMIZED_TELEMETRY": "false",
                }
            ),
            layers=[label_layer.arn],  # receipt-label includes receipt-dynamo
            tags={"environment": stack},
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["layers"],
            ),
        )

        # Define IAM role for Step Function
        submit_sfn_role = Role(
            f"{name}-sfn-role",
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
            f"{name}-sfn-lambda-invoke-policy",
            role=submit_sfn_role.id,
            policy=Output.all(
                prepare_batch_lambda.arn,
                submit_batch_lambda.arn,
                list_pending_batches_lambda.arn,
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

        StateMachine(
            f"{name}-submit-embedding-batch-sm",
            role_arn=submit_sfn_role.arn,
            definition=Output.all(
                prepare_batch_lambda.arn, submit_batch_lambda.arn
            ).apply(
                lambda arns: json.dumps(
                    {
                        "StartAt": "PrepareBatch",
                        "States": {
                            "PrepareBatch": {
                                "Type": "Task",
                                "Resource": arns[0],
                                "Next": "ForEachBatch",
                            },
                            "ForEachBatch": {
                                "Type": "Map",
                                "ItemsPath": "$.batches",
                                "MaxConcurrency": 10,
                                "Iterator": {
                                    "StartAt": "SubmitBatch",
                                    "States": {
                                        "SubmitBatch": {
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
