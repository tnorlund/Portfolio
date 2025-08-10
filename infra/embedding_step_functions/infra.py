"""
Line embedding step functions with ChromaDB integration.

This module creates step functions for processing line embeddings using:
- Containerized Lambda functions with ChromaDB support
- S3 delta storage and compaction pipeline
- DynamoDB for state management
"""

import json
from typing import Optional

import pulumi

from pulumi import (
    ComponentResource,
    Config,
    Output,
    ResourceOptions,
    Resource,
)
from pulumi_aws.s3 import Bucket
from pulumi_aws.iam import Role, RolePolicy
from pulumi_aws.sfn import StateMachine

# Import ChromaDB infrastructure components
from chromadb_compaction import ChromaDBBuckets, ChromaDBQueues
# Use the new unified Lambda implementation
from .unified_chromadb_lambdas import UnifiedChromaDBLambdas

# Note: This import is not actually used in this file
# from base_images.base_images_v3 import BaseImages

config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")
# Keep Pinecone config for backward compatibility during migration
pinecone_api_key = config.get_secret("PINECONE_API_KEY") or ""
pinecone_index_name = config.get("PINECONE_INDEX_NAME") or ""
pinecone_host = config.get("PINECONE_HOST") or ""

stack = pulumi.get_stack()


class LineEmbeddingStepFunction(ComponentResource):
    """ChromaDB-based line embedding step function."""

    def __init__(
        self,
        name: str,
        base_image_name: Optional[Output[str]] = None,
        base_image_resource: Optional[Resource] = None,
        opts: Optional[ResourceOptions] = None,
    ):
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
        # Add dependency on base image resource if provided
        lambda_opts = ResourceOptions(parent=self)
        if base_image_resource:
            lambda_opts = ResourceOptions(parent=self, depends_on=[base_image_resource])

        # Only pass base_image_name if it's provided
        lambda_args = {
            "chromadb_bucket_name": self.chromadb_buckets.bucket_name,
            "chromadb_queue_url": self.chromadb_queues.delta_queue_url,
            "chromadb_queue_arn": self.chromadb_queues.delta_queue_arn,
            "openai_api_key": openai_api_key,
            "s3_batch_bucket_name": self.batch_bucket.bucket,
            "stack": stack,
            "opts": lambda_opts,
        }

        if base_image_name is not None:
            lambda_args["base_image_name"] = base_image_name
            lambda_args["base_image_resource"] = base_image_resource

        self.chromadb_lambdas = UnifiedChromaDBLambdas(f"{name}-chromadb", **lambda_args)

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

        # Create the Poll and Store Line Embeddings Step Function with Chunked Compaction
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
                        "Comment": (
                            "Poll OpenAI for completed line embedding batches and "
                            "store in ChromaDB using chunked compaction"
                        ),
                        "StartAt": "ListPendingBatches",
                        "States": {
                            "ListPendingBatches": {
                                "Type": "Task",
                                "Resource": arns[0],
                                "Comment": ("Find all OpenAI batches with " "status=PENDING"),
                                "Next": "PollLineEmbeddingBatch",
                                "Retry": [
                                    {
                                        "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException"],
                                        "IntervalSeconds": 1,
                                        "MaxAttempts": 2,
                                        "BackoffRate": 1.5,
                                        "JitterStrategy": "FULL",
                                    },
                                    {
                                        "ErrorEquals": ["States.ALL"],
                                        "IntervalSeconds": 1,
                                        "MaxAttempts": 3,
                                        "BackoffRate": 2.0,
                                    }
                                ],
                            },
                            "PollLineEmbeddingBatch": {
                                "Type": "Map",
                                "ItemsPath": "$",
                                "MaxConcurrency": 10,
                                "Parameters": {
                                    "batch_id.$": "$$.Map.Item.Value.batch_id",
                                    "openai_batch_id.$": "$$.Map.Item.Value.openai_batch_id",
                                    "skip_sqs_notification": True,
                                },
                                "Iterator": {
                                    "StartAt": "PollLineEmbeddingBatchTask",
                                    "States": {
                                        "PollLineEmbeddingBatchTask": {
                                            "Type": "Task",
                                            "Resource": arns[1],
                                            "End": True,
                                            "Retry": [
                                                {
                                                    "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException"],
                                                    "IntervalSeconds": 1,
                                                    "MaxAttempts": 2,
                                                    "BackoffRate": 1.5,
                                                    "JitterStrategy": "FULL",
                                                },
                                                {
                                                    "ErrorEquals": ["RateLimitError", "OpenAIError"],
                                                    "IntervalSeconds": 5,
                                                    "MaxAttempts": 5,
                                                    "BackoffRate": 2.0,
                                                },
                                                {
                                                    "ErrorEquals": ["States.ALL"],
                                                    "IntervalSeconds": 2,
                                                    "MaxAttempts": 3,
                                                    "BackoffRate": 2.0,
                                                }
                                            ],
                                        }
                                    },
                                },
                                "ResultPath": "$.poll_results",
                                "Next": "PrepareChunkedCompaction",
                            },
                            "PrepareChunkedCompaction": {
                                "Type": "Pass",
                                "Comment": "Prepare data for chunked compaction",
                                "Parameters": {
                                    "batch_id.$": "$$.Execution.Name",
                                    "delta_results.$": "$.poll_results",
                                    "chunk_index": 0,
                                    "total_chunks_processed": 0,
                                    "operation": "process_chunk",
                                },
                                "Next": "CheckForDeltas",
                            },
                            "CheckForDeltas": {
                                "Type": "Choice",
                                "Comment": "Check if there are deltas to process",
                                "Choices": [
                                    {
                                        "Variable": "$.delta_results[0]",
                                        "IsPresent": True,
                                        "Next": "ProcessChunk",
                                    }
                                ],
                                "Default": "FinalMerge",
                            },
                            "ProcessChunk": {
                                "Type": "Task",
                                "Resource": arns[2],
                                "Comment": "Process a chunk of deltas (max 10)",
                                "Parameters": {
                                    "operation": "process_chunk",
                                    "batch_id.$": "$.batch_id",
                                    "chunk_index.$": "$.chunk_index",
                                    "delta_results.$": "$.delta_results",
                                },
                                "ResultPath": "$.chunk_result",
                                "Next": "CheckContinuation",
                                "Retry": [
                                    {
                                        # Fast retry for Lambda service errors to keep container warm
                                        "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException"],
                                        "IntervalSeconds": 1,
                                        "MaxAttempts": 2,
                                        "BackoffRate": 1.5,
                                        "JitterStrategy": "FULL",
                                    },
                                    {
                                        # Slower retry for rate limits
                                        "ErrorEquals": ["Lambda.TooManyRequestsException", "States.Timeout"],
                                        "IntervalSeconds": 2,
                                        "MaxAttempts": 3,
                                        "BackoffRate": 2.0,
                                    },
                                    {
                                        # Catch-all for other errors
                                        "ErrorEquals": ["States.ALL"],
                                        "IntervalSeconds": 1,
                                        "MaxAttempts": 3,
                                        "BackoffRate": 2.0,
                                    }
                                ],
                                "Catch": [
                                    {
                                        "ErrorEquals": ["States.ALL"],
                                        "Next": "ChunkProcessingFailed",
                                        "ResultPath": "$.error",
                                    }
                                ],
                            },
                            "CheckContinuation": {
                                "Type": "Choice",
                                "Comment": "Check if there are more chunks to process",
                                "Choices": [
                                    {
                                        "Variable": "$.chunk_result.has_more_chunks",
                                        "BooleanEquals": True,
                                        "Next": "PrepareNextChunk",
                                    }
                                ],
                                "Default": "FinalMerge",
                            },
                            "PrepareNextChunk": {
                                "Type": "Pass",
                                "Comment": "Prepare for next chunk iteration",
                                "Parameters": {
                                    "batch_id.$": "$.batch_id",
                                    "operation": "process_chunk",
                                    "chunk_index.$": "$.chunk_result.next_chunk_index",
                                    "delta_results.$": "$.chunk_result.remaining_deltas",
                                    "total_chunks_processed.$": (
                                        "States.MathAdd($.total_chunks_processed, 1)"
                                    ),
                                },
                                "Next": "ProcessChunk",
                            },
                            "FinalMerge": {
                                "Type": "Task",
                                "Resource": arns[2],
                                "Comment": "Final merge of all intermediate chunks",
                                "Parameters": {
                                    "operation": "final_merge",
                                    "batch_id.$": "$.batch_id",
                                    "total_chunks.$": "States.MathAdd($.total_chunks_processed, 1)",
                                },
                                "End": True,
                                "Retry": [
                                    {
                                        # Fast retry to keep container warm for final merge
                                        "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException"],
                                        "IntervalSeconds": 1,
                                        "MaxAttempts": 2,
                                        "BackoffRate": 1.5,
                                        "JitterStrategy": "FULL",
                                    },
                                    {
                                        # Longer retry for resource issues during compaction
                                        "ErrorEquals": ["States.ALL"],
                                        "IntervalSeconds": 3,
                                        "MaxAttempts": 3,
                                        "BackoffRate": 2.0,
                                    }
                                ],
                                "Catch": [
                                    {
                                        "ErrorEquals": ["States.ALL"],
                                        "Next": "FinalMergeFailed",
                                        "ResultPath": "$.error",
                                    }
                                ],
                            },
                            "ChunkProcessingFailed": {
                                "Type": "Fail",
                                "Comment": "Chunk processing failed",
                                "Cause": "Failed to process chunk during compaction",
                            },
                            "FinalMergeFailed": {
                                "Type": "Fail",
                                "Comment": "Final merge failed",
                                "Cause": (
                                    "Failed to merge intermediate chunks during " "final compaction"
                                ),
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
                        "Comment": (
                            "Find receipt lines without embeddings and submit "
                            "them to OpenAI Batch API"
                        ),
                        "StartAt": "ListLinesThatNeedEmbedding",
                        "States": {
                            "ListLinesThatNeedEmbedding": {
                                "Type": "Task",
                                "Resource": arns[0],
                                "Comment": (
                                    "Query DynamoDB for lines with " "embedding_status=NONE"
                                ),
                                "Next": "SubmitEmbedding",
                                "Retry": [
                                    {
                                        "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException"],
                                        "IntervalSeconds": 1,
                                        "MaxAttempts": 2,
                                        "BackoffRate": 1.5,
                                        "JitterStrategy": "FULL",
                                    },
                                    {
                                        "ErrorEquals": ["States.ALL"],
                                        "IntervalSeconds": 1,
                                        "MaxAttempts": 3,
                                        "BackoffRate": 2.0,
                                    }
                                ],
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
                                            "Retry": [
                                                {
                                                    "ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException"],
                                                    "IntervalSeconds": 1,
                                                    "MaxAttempts": 2,
                                                    "BackoffRate": 1.5,
                                                    "JitterStrategy": "FULL",
                                                },
                                                {
                                                    "ErrorEquals": ["RateLimitError", "OpenAIError"],
                                                    "IntervalSeconds": 5,
                                                    "MaxAttempts": 5,
                                                    "BackoffRate": 2.0,
                                                },
                                                {
                                                    "ErrorEquals": ["States.ALL"],
                                                    "IntervalSeconds": 2,
                                                    "MaxAttempts": 3,
                                                    "BackoffRate": 2.0,
                                                }
                                            ],
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
                "chromadb_line_polling_lambda_arn": (self.chromadb_lambdas.line_polling_lambda.arn),
                "chromadb_compaction_lambda_arn": (self.chromadb_lambdas.compaction_lambda.arn),
                "poll_and_store_embeddings_sm_arn": (self.poll_and_store_embeddings_sm.arn),
                "create_embedding_batches_sm_arn": (self.create_embedding_batches_sm.arn),
                "list_pending_openai_batches_lambda_arn": (
                    self.chromadb_lambdas.list_pending_lambda.arn
                ),
                "find_lines_needing_embeddings_lambda_arn": (
                    self.chromadb_lambdas.find_unembedded_lambda.arn
                ),
                "submit_lines_to_openai_lambda_arn": (
                    self.chromadb_lambdas.submit_openai_lambda.arn
                ),
            }
        )


# Legacy submit and poll step functions for backward compatibility
# These can be removed once migration to ChromaDB is complete
class LegacyLineEmbeddingStepFunction(ComponentResource):
    """Legacy Pinecone-based line embedding step functions (deprecated)."""

    def __init__(self, name: str, opts: Optional[ResourceOptions] = None):
        super().__init__(
            f"{__name__}-{name}-legacy",
            "aws:stepfunctions:LegacyLineEmbeddingStepFunction",
            {},
            opts,
        )

        # NOTE: Move existing Pinecone-based logic here if needed for transition
        # For now, this is a placeholder to maintain backward compatibility
