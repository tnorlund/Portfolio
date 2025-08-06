"""
Line embedding step functions with ChromaDB integration.

This module creates step functions for processing line embeddings using:
- Containerized Lambda functions with ChromaDB support
- S3 delta storage and compaction pipeline
- DynamoDB for state management
"""

import json
import os
import pulumi

from dynamo_db import dynamodb_table
from lambda_layer import dynamo_layer, label_layer
from pulumi import (
    AssetArchive,
    ComponentResource,
    Config,
    FileAsset,
    Output,
    ResourceOptions,
)
from pulumi_aws.s3 import Bucket
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment
from pulumi_aws.lambda_ import Function, FunctionEnvironmentArgs
from pulumi_aws.sfn import StateMachine

# Import ChromaDB infrastructure components
from chromadb_compaction import ChromaDBBuckets, ChromaDBQueues
from .chromadb_lambdas import ChromaDBLambdas
from base_images import BaseImages

config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")
# Keep Pinecone config for backward compatibility during migration
pinecone_api_key = config.get_secret("PINECONE_API_KEY") or ""
pinecone_index_name = config.get("PINECONE_INDEX_NAME") or ""
pinecone_host = config.get("PINECONE_HOST") or ""

stack = pulumi.get_stack()


class LineEmbeddingStepFunction(ComponentResource):
    """ChromaDB-based line embedding step function."""

    def __init__(self, name: str, base_image_name: Output[str] = None, opts: ResourceOptions = None):
        super().__init__(
            f"{__name__}-{name}",
            "aws:stepfunctions:LineEmbeddingStepFunction",
            {},
            opts,
        )

        # Create ChromaDB infrastructure (S3 buckets and SQS queues)
        self.chromadb_buckets = ChromaDBBuckets(
            f"{name}-chromadb-buckets",
            opts=ResourceOptions(parent=self),
        )

        self.chromadb_queues = ChromaDBQueues(
            f"{name}-chromadb-queues",
            opts=ResourceOptions(parent=self),
        )

        # Create S3 bucket for NDJSON batch files before ChromaDBLambdas
        self.batch_bucket = Bucket(
            f"{name}-completion-batch-bucket",
            force_destroy=True,
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )
        
        # Create ChromaDB containerized Lambdas (includes all Lambda functions)
        self.chromadb_lambdas = ChromaDBLambdas(
            f"{name}-chromadb",
            chromadb_bucket_name=self.chromadb_buckets.bucket_name,
            chromadb_queue_url=self.chromadb_queues.delta_queue_url,
            chromadb_queue_arn=self.chromadb_queues.delta_queue_arn,
            openai_api_key=openai_api_key,
            s3_batch_bucket_name=self.batch_bucket.bucket,
            stack=stack,
            base_image_name=base_image_name,
            opts=ResourceOptions(parent=self),
        )

        # Create IAM role for Step Function
        self.step_function_role = Role(
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

        # Step Function policy for invoking Lambdas
        RolePolicy(
            f"{name}-sfn-policy",
            role=self.step_function_role.id,
            policy=Output.all(
                self.chromadb_lambdas.find_unembedded_lambda.arn,
                self.chromadb_lambdas.submit_openai_lambda.arn,
                self.chromadb_lambdas.list_pending_lambda.arn,
                self.chromadb_lambdas.line_polling_lambda.arn,
                self.chromadb_lambdas.compaction_lambda.arn,
            ).apply(
                lambda arns: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["lambda:InvokeFunction"],
                                "Resource": arns,
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Create the Poll and Store Line Embeddings Step Function
        self.poll_and_store_embeddings_sm = StateMachine(
            f"{name}-poll-store-embeddings-sm",
            role_arn=self.step_function_role.arn,
            definition=Output.all(
                self.chromadb_lambdas.list_pending_lambda.arn,
                self.chromadb_lambdas.line_polling_lambda.arn,
                self.chromadb_lambdas.compaction_lambda.arn,
            ).apply(
                lambda arns: json.dumps(
                    {
                        "Comment": "Poll OpenAI for completed line embedding batches and store in ChromaDB",
                        "StartAt": "ListPendingBatches",
                        "States": {
                            "ListPendingBatches": {
                                "Type": "Task",
                                "Resource": arns[0],
                                "Comment": "Find all OpenAI batches with status=PENDING",
                                "Next": "PollLineEmbeddingBatch",
                            },
                            "PollLineEmbeddingBatch": {
                                "Type": "Map",
                                "ItemsPath": "$.body",
                                "MaxConcurrency": 10,
                                "Parameters": {
                                    "batch_id.$": "$.batch_id",
                                    "openai_batch_id.$": "$.openai_batch_id",
                                    "skip_sqs_notification": True,
                                },
                                "Iterator": {
                                    "StartAt": "PollLineEmbeddingBatchTask",
                                    "States": {
                                        "PollLineEmbeddingBatchTask": {
                                            "Type": "Task",
                                            "Resource": arns[1],
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

        # Create the Line Embedding Batch Creation Step Function
        self.create_embedding_batches_sm = StateMachine(
            f"{name}-create-embedding-batches-sm",
            role_arn=self.step_function_role.arn,
            definition=Output.all(
                self.chromadb_lambdas.find_unembedded_lambda.arn,
                self.chromadb_lambdas.submit_openai_lambda.arn,
            ).apply(
                lambda arns: json.dumps(
                    {
                        "Comment": "Find receipt lines without embeddings and submit them to OpenAI Batch API",
                        "StartAt": "ListLinesThatNeedEmbedding",
                        "States": {
                            "ListLinesThatNeedEmbedding": {
                                "Type": "Task",
                                "Resource": arns[0],
                                "Comment": "Query DynamoDB for lines with embedding_status=NONE",
                                "Next": "SubmitEmbedding",
                            },
                            "SubmitEmbedding": {
                                "Type": "Map",
                                "ItemsPath": "$.batches",
                                "MaxConcurrency": 10,
                                "Iterator": {
                                    "StartAt": "SubmitEmbeddingTask",
                                    "States": {
                                        "SubmitEmbeddingTask": {
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

        # Register outputs
        self.register_outputs(
            {
                "chromadb_bucket_name": self.chromadb_buckets.bucket_name,
                "chromadb_queue_url": self.chromadb_queues.delta_queue_url,
                "chromadb_line_polling_lambda_arn": (
                    self.chromadb_lambdas.line_polling_lambda.arn
                ),
                "chromadb_compaction_lambda_arn": (
                    self.chromadb_lambdas.compaction_lambda.arn
                ),
                "poll_and_store_embeddings_sm_arn": self.poll_and_store_embeddings_sm.arn,
                "create_embedding_batches_sm_arn": self.create_embedding_batches_sm.arn,
                "list_pending_openai_batches_lambda_arn": (
                    self.chromadb_lambdas.list_pending_lambda.arn
                ),
                "find_lines_needing_embeddings_lambda_arn": self.chromadb_lambdas.find_unembedded_lambda.arn,
                "submit_lines_to_openai_lambda_arn": self.chromadb_lambdas.submit_openai_lambda.arn,
            }
        )


# Legacy submit and poll step functions for backward compatibility
# These can be removed once migration to ChromaDB is complete
class LegacyLineEmbeddingStepFunction(ComponentResource):
    """Legacy Pinecone-based line embedding step functions (deprecated)."""

    def __init__(self, name: str, opts: ResourceOptions = None):
        super().__init__(
            f"{__name__}-{name}-legacy",
            "aws:stepfunctions:LegacyLineEmbeddingStepFunction",
            {},
            opts,
        )

        # NOTE: Move existing Pinecone-based logic here if needed for transition
        # For now, this is a placeholder to maintain backward compatibility
        pass
