"""submit_step_function.py

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


from dynamo_db import dynamodb_table
from lambda_layer import dynamo_layer, label_layer
from chromadb_compaction import ChromaDBBuckets, ChromaDBQueues
from word_label_step_functions.chromadb_lambdas import ChromaDBLambdas

config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")
# Keep Pinecone config for backward compatibility during migration
pinecone_api_key = config.get_secret("PINECONE_API_KEY") or ""
pinecone_index_name = config.get("PINECONE_INDEX_NAME") or ""
pinecone_host = config.get("PINECONE_HOST") or ""
stack = pulumi.get_stack()


class WordLabelStepFunctions(ComponentResource):
    def __init__(self, name: str, base_image_name: Output[str] = None, opts: ResourceOptions = None):
        super().__init__(
            "custom:stepfunctions:WordLabelStepFunctions", 
            name,
            None,  # props
            opts
        )

        # Create ChromaDB infrastructure components
        self.chromadb_buckets = ChromaDBBuckets(
            f"{name}-chromadb",
            stack=stack,
            opts=ResourceOptions(parent=self),
        )

        self.chromadb_queues = ChromaDBQueues(
            f"{name}-chromadb",
            stack=stack,
            opts=ResourceOptions(parent=self),
        )

        # Create ChromaDB containerized Lambdas
        self.chromadb_lambdas = ChromaDBLambdas(
            f"{name}-chromadb-lambdas",
            chromadb_bucket_name=self.chromadb_buckets.bucket_name,
            chromadb_queue_url=self.chromadb_queues.delta_queue_url,
            chromadb_queue_arn=self.chromadb_queues.delta_queue_arn,
            openai_api_key=openai_api_key,
            stack=stack,
            base_image_name=base_image_name,
            opts=ResourceOptions(parent=self),
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
            force_destroy=True,
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        # The poll lambda is now containerized - reference from ChromaDBLambdas
        poll_embedding_batch_lambda = self.chromadb_lambdas.polling_lambda

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
                    # ChromaDB configuration
                    "CHROMADB_BUCKET": self.chromadb_buckets.bucket_name,
                    "COMPACTION_QUEUE_URL": self.chromadb_queues.delta_queue_url,
                }
            ),
            architectures=["arm64"],  # Match layer architecture
            layers=[dynamo_layer.arn, label_layer.arn],
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
                self.chromadb_lambdas.compaction_lambda.arn,
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

        # Define the poll embedding batch Step Function with compaction
        StateMachine(
            f"{name}-poll-embedding-batch-sm",
            role_arn=poll_sfn_role.arn,
            definition=Output.all(
                poll_embedding_batch_lambda.arn,
                list_pending_batches_lambda.arn,
                self.chromadb_lambdas.compaction_lambda.arn,
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
                                    "skip_sqs_notification": True,  # Skip individual SQS notifications
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
                                "ResultPath": "$.poll_results",
                                "Next": "CompactAllDeltas",
                            },
                            "CompactAllDeltas": {
                                "Type": "Task",
                                "Resource": arns[2],
                                "Parameters": {
                                    "delta_results.$": "$.poll_results[*]"
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

        # Add S3 permissions for ChromaDB bucket
        RolePolicy(
            f"{name}-lambda-chromadb-s3-policy",
            role=submit_lambda_role.id,
            policy=Output.all(self.chromadb_buckets.bucket_name).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:PutObject",
                                    "s3:PutObjectAcl",
                                    "s3:GetObject",
                                    "s3:ListBucket",
                                ],
                                "Resource": [
                                    f"arn:aws:s3:::{args[0]}",
                                    f"arn:aws:s3:::{args[0]}/*",
                                ],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Add SQS permissions for ChromaDB queue
        RolePolicy(
            f"{name}-lambda-chromadb-sqs-policy",
            role=submit_lambda_role.id,
            policy=Output.all(self.chromadb_queues.delta_queue_arn).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "sqs:SendMessage",
                                    "sqs:GetQueueAttributes",
                                ],
                                "Resource": args[0],
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
                    # ChromaDB configuration
                    "CHROMADB_BUCKET": self.chromadb_buckets.bucket_name,
                    "COMPACTION_QUEUE_URL": self.chromadb_queues.delta_queue_url,
                }
            ),
            architectures=["arm64"],  # Match layer architecture
            layers=[dynamo_layer.arn, label_layer.arn],
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
                    # ChromaDB configuration
                    "CHROMADB_BUCKET": self.chromadb_buckets.bucket_name,
                    "COMPACTION_QUEUE_URL": self.chromadb_queues.delta_queue_url,
                }
            ),
            architectures=["arm64"],  # Match layer architecture
            layers=[dynamo_layer.arn, label_layer.arn],
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

        # Create Lambda for listing pending line embedding batches
        list_pending_line_batches_lambda = Function(
            f"{name}-list-pending-line-batches",
            role=submit_lambda_role.arn,
            runtime="python3.12",
            handler="list_pending_line_batches_for_polling.poll_handler",
            timeout=900,
            memory_size=512,
            code=AssetArchive(
                {
                    "list_pending_line_batches_for_polling.py": FileAsset(
                        os.path.join(os.path.dirname(__file__), "list_pending_line_batches_for_polling.py")
                    )
                }
            ),
            layers=[dynamo_layer.arn, label_layer.arn],
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Create Line Embedding ChromaDB Step Function
        from .line_embedding_chromadb_step_function import LineEmbeddingChromaDBStepFunction
        
        self.line_embedding_chromadb_sf = LineEmbeddingChromaDBStepFunction(
            f"{name}-line-chromadb",
            chromadb_lambdas=self.chromadb_lambdas,
            list_pending_batches_lambda=list_pending_line_batches_lambda,
            opts=ResourceOptions(parent=self),
        )

        # Export ChromaDB resources
        self.register_outputs(
            {
                "chromadb_bucket_name": self.chromadb_buckets.bucket_name,
                "chromadb_bucket_arn": self.chromadb_buckets.bucket_arn,
                "chromadb_delta_queue_url": self.chromadb_queues.delta_queue_url,
                "chromadb_delta_queue_arn": self.chromadb_queues.delta_queue_arn,
                "chromadb_polling_lambda_arn": self.chromadb_lambdas.polling_lambda.arn,
                "chromadb_compaction_lambda_arn": self.chromadb_lambdas.compaction_lambda.arn,
                "chromadb_line_polling_lambda_arn": self.chromadb_lambdas.line_polling_lambda.arn,
                "line_embedding_chromadb_sf_arn": self.line_embedding_chromadb_sf.state_machine.arn,
            }
        )
