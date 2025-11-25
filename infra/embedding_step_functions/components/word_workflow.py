"""Word embedding workflow component."""

import json
from typing import Optional

from pulumi import (
    ComponentResource,
    Output,
    ResourceOptions,
)
from pulumi_aws.iam import Role, RolePolicy
from pulumi_aws.sfn import StateMachine

from .base import stack


class WordEmbeddingWorkflow(ComponentResource):
    """Word embedding submission and ingestion workflows."""

    def __init__(
        self,
        name: str,
        lambda_functions,
        batch_bucket,
        opts: Optional[ResourceOptions] = None,
    ):
        """Initialize word embedding workflow component.

        Args:
            name: Component name
            lambda_functions: Dictionary of Lambda functions
            batch_bucket: S3 bucket for batch files and poll results
            opts: Pulumi resource options
        """
        super().__init__(
            "custom:embedding:WordWorkflow",
            name,
            None,
            opts,
        )

        self.lambda_functions = lambda_functions
        self.batch_bucket = batch_bucket

        # Create IAM role for Step Functions
        self._create_step_function_role()

        # Create submission workflow
        self.submit_sf = self._create_submit_workflow()

        # Create ingestion workflow
        self.ingest_sf = self._create_ingest_workflow()

        # Register outputs
        self.register_outputs(
            {
                "submit_sf_arn": self.submit_sf.arn,
                "ingest_sf_arn": self.ingest_sf.arn,
            }
        )

    def _create_step_function_role(self):
        """Create IAM role for Step Functions."""
        self.sf_role = Role(
            f"word-sf-role-{stack}",
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
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        # Add permissions to invoke Lambda functions and write to S3 (for normalize handler to upload poll results)
        lambda_arns = [func.arn for func in self.lambda_functions.values()]

        RolePolicy(
            f"word-sf-lambda-invoke-{stack}",
            role=self.sf_role.id,
            policy=Output.all(*lambda_arns, self.batch_bucket.bucket).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["lambda:InvokeFunction"],
                                "Resource": args[:-1],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:PutObject",
                                    "s3:GetObject",
                                ],
                                "Resource": [
                                    f"arn:aws:s3:::{args[-1]}/*",
                                ],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

    def _create_submit_workflow(self) -> StateMachine:
        """Create the word embedding submission workflow."""
        return StateMachine(
            f"word-submit-sf-{stack}",
            role_arn=self.sf_role.arn,
            tags={"environment": stack},
            definition=Output.all(
                self.lambda_functions["embedding-find-words"].arn,
                self.lambda_functions["embedding-submit-words"].arn,
            ).apply(self._create_submit_definition),
            opts=ResourceOptions(parent=self),
        )

    def _create_submit_definition(self, arns: list) -> str:
        """Create submission workflow definition."""
        return json.dumps(
            {
                "Comment": "Find and submit word embeddings",
                "StartAt": "FindUnembeddedWords",
                "States": {
                    "FindUnembeddedWords": {
                        "Type": "Task",
                        "Resource": arns[0],
                        "Next": "SubmitWordBatches",
                    },
                    "SubmitWordBatches": {
                        "Type": "Map",
                        "ItemsPath": "$.batches",
                        "MaxConcurrency": 10,
                        "Iterator": {
                            "StartAt": "SubmitWordsToOpenAI",
                            "States": {
                                "SubmitWordsToOpenAI": {
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

    def _create_ingest_workflow(self) -> StateMachine:
        """Create the word embedding ingestion workflow."""
        return StateMachine(
            f"word-ingest-sf-{stack}",
            role_arn=self.sf_role.arn,
            tags={"environment": stack},
            definition=Output.all(
                self.lambda_functions["embedding-list-pending"].arn,
                self.lambda_functions["embedding-poll-words"].arn,
                self.lambda_functions["embedding-compact"].arn,
                self.lambda_functions["embedding-normalize-batches"].arn,
                self.lambda_functions["embedding-split-chunks"].arn,
                self.lambda_functions["embedding-prepare-chunk-groups"].arn,
                self.lambda_functions["embedding-mark-complete"].arn,
                self.lambda_functions["embedding-prepare-merge-pairs"].arn,
                self.batch_bucket.bucket,
            ).apply(self._create_ingest_definition),
            opts=ResourceOptions(parent=self),
        )

    def _create_ingest_definition(self, arns_and_bucket: list) -> str:
        """Create ingestion workflow definition.

        arns_and_bucket[0] = embedding-list-pending
        arns_and_bucket[1] = embedding-poll-words
        arns_and_bucket[2] = embedding-compact
        arns_and_bucket[3] = embedding-normalize-batches
        arns_and_bucket[4] = embedding-split-chunks
        arns_and_bucket[5] = embedding-prepare-chunk-groups
        arns_and_bucket[6] = embedding-mark-complete
        arns_and_bucket[7] = embedding-prepare-merge-pairs
        arns_and_bucket[8] = batch_bucket_name
        """
        arns = arns_and_bucket[:-1]
        batch_bucket_name = arns_and_bucket[-1]
        return json.dumps(
            {
                "Comment": "Poll and ingest word embeddings",
                "StartAt": "ListPendingWordBatches",
                "States": {
                    "ListPendingWordBatches": {
                        "Type": "Task",
                        "Resource": arns[0],
                        "Parameters": {
                            "batch_type": "word",
                            "execution_id.$": "$$.Execution.Name",
                        },
                        "ResultPath": "$.list_result",
                        "Next": "CheckPendingWordBatches",
                    },
                    "CheckPendingWordBatches": {
                        "Type": "Choice",
                        "Comment": "Check if there are any pending batches",
                        "Choices": [
                            {
                                "Variable": "$.list_result.total_batches",
                                "NumericGreaterThan": 0,
                                "Next": "NormalizePendingWordBatches",
                            },
                        ],
                        "Default": "NoWordBatchesPending",
                    },
                    "NormalizePendingWordBatches": {
                        "Type": "Pass",
                        "Comment": "Normalize batches data structure for PollWordBatches Map state",
                        "Parameters": {
                            "batch_indices.$": "$.list_result.batch_indices",
                            "pending_batches.$": "$.list_result.pending_batches",
                            "manifest_s3_key.$": "$.list_result.manifest_s3_key",
                            "manifest_s3_bucket.$": "$.list_result.manifest_s3_bucket",
                            "use_s3.$": "$.list_result.use_s3",
                            "execution_id.$": "$.list_result.execution_id",
                            "total_batches.$": "$.list_result.total_batches",
                        },
                        "ResultPath": "$.poll_batches_data",
                        "Next": "PollWordBatches",
                    },
                    "PollWordBatches": {
                        "Type": "Map",
                        "Comment": "Poll batches in parallel - supports both inline and S3 manifest modes. Results are normalized and uploaded to S3 by NormalizePollWordBatchesData handler.",
                        "ItemsPath": "$.poll_batches_data.batch_indices",
                        "MaxConcurrency": 100,
                        "Parameters": {
                            "batch_index.$": "$$.Map.Item.Value",
                            "manifest_s3_key.$": "$.poll_batches_data.manifest_s3_key",
                            "manifest_s3_bucket.$": "$.poll_batches_data.manifest_s3_bucket",
                            "pending_batches.$": "$.poll_batches_data.pending_batches",
                            "skip_sqs_notification": True,
                        },
                        "Iterator": {
                            "StartAt": "PollWordBatch",
                            "States": {
                                "PollWordBatch": {
                                    "Type": "Task",
                                    "Resource": arns[1],
                                    "End": True,
                                    "Retry": [
                                        {
                                            "ErrorEquals": [
                                                "Lambda.ServiceException",
                                                "Lambda.AWSLambdaException",
                                                "Lambda.ResourceConflictException",
                                                "Runtime.ExitError",
                                            ],
                                            "IntervalSeconds": 5,
                                            "MaxAttempts": 5,
                                            "BackoffRate": 2.0,
                                            "JitterStrategy": "FULL",
                                        },
                                        {
                                            "ErrorEquals": [
                                                "Lambda.TooManyRequestsException"
                                            ],
                                            "IntervalSeconds": 10,
                                            "MaxAttempts": 5,
                                            "BackoffRate": 2.0,
                                        },
                                    ],
                                },
                            },
                        },
                        "ResultPath": "$.poll_results",
                        "Next": "PrepareWordChunks",
                    },
                    "PrepareWordChunks": {
                        "Type": "Task",
                        "Resource": arns[3],
                        "Comment": "Combine poll results, create chunks, upload to S3. Replaces NormalizePollWordBatchesData + SplitWordIntoChunks + LoadWordChunksFromS3.",
                        "Parameters": {
                            "batch_id.$": "$$.Execution.Name",
                            "poll_results.$": "$.poll_results",
                            "database": "words",
                        },
                        "ResultPath": "$.chunked_data",
                        "Next": "CheckForWordChunks",
                        "Retry": [
                            {
                                "ErrorEquals": [
                                    "Lambda.ServiceException",
                                    "Lambda.AWSLambdaException",
                                    "Lambda.ResourceConflictException",
                                ],
                                "IntervalSeconds": 5,
                                "MaxAttempts": 5,
                                "BackoffRate": 2.0,
                                "JitterStrategy": "FULL",
                            }
                        ],
                        "Catch": [
                            {
                                "ErrorEquals": ["States.ALL"],
                                "Next": "WordCompactionFailed",
                                "ResultPath": "$.error",
                            }
                        ],
                    },
                    "CheckForWordChunks": {
                        "Type": "Choice",
                        "Comment": "Check if there are chunks to process",
                        "Choices": [
                            {
                                "Variable": "$.chunked_data.has_chunks",
                                "BooleanEquals": True,
                                "Next": "ProcessWordChunksInParallel",
                            }
                        ],
                        "Default": "NoWordChunksToProcess",
                    },
                    "ProcessWordChunksInParallel": {
                        "Type": "Map",
                        "Comment": "Process word chunks in parallel",
                        "ItemsPath": "$.chunked_data.chunks",
                        "MaxConcurrency": 20,
                        "Parameters": {
                            "chunk.$": "$$.Map.Item.Value",
                        },
                        "Iterator": {
                            "StartAt": "ProcessSingleWordChunk",
                            "States": {
                                "ProcessSingleWordChunk": {
                                    "Type": "Task",
                                    "Resource": arns[2],
                                    "Comment": "Process a single word chunk",
                                    "Parameters": {
                                        "operation": "process_chunk",
                                        "batch_id.$": "$.chunk.batch_id",
                                        "chunk_index.$": "$.chunk.chunk_index",
                                        # Chunks are always in S3, keys come from chunk object
                                        "chunks_s3_key.$": "$.chunk.chunks_s3_key",
                                        "chunks_s3_bucket.$": "$.chunk.chunks_s3_bucket",
                                        "database": "words",
                                    },
                                    "End": True,
                                    "Retry": [
                                        {
                                            "ErrorEquals": [
                                                "Lambda.ServiceException",
                                                "Lambda.AWSLambdaException",
                                                "Lambda.ResourceConflictException",
                                                "Runtime.ExitError",
                                            ],
                                            "IntervalSeconds": 5,
                                            "MaxAttempts": 5,
                                            "BackoffRate": 2.0,
                                            "JitterStrategy": "FULL",
                                        },
                                        {
                                            "ErrorEquals": [
                                                "Lambda."
                                                "TooManyRequestsException"
                                            ],
                                            "IntervalSeconds": 10,
                                            "MaxAttempts": 5,
                                            "BackoffRate": 2.0,
                                        },
                                    ],
                                },
                            },
                        },
                        "ResultPath": "$.chunk_results",
                        "Next": "PrepareMergePairs",
                        "Catch": [
                            {
                                "ErrorEquals": ["States.ALL"],
                                "Next": "WordChunkProcessingFailed",
                                "ResultPath": "$.error",
                            }
                        ],
                    },
                    # ============================================================
                    # PARALLEL REDUCE PATTERN
                    # Merges N intermediates down to 1 using parallel pair merging
                    # ============================================================
                    "PrepareMergePairs": {
                        "Type": "Task",
                        "Resource": arns[7],
                        "Comment": "Prepare pairs for parallel reduce - groups intermediates into pairs",
                        "Parameters": {
                            "intermediates.$": "$.chunk_results",
                            "batch_id.$": "$.chunked_data.batch_id",
                            "database": "words",
                            "round": 0,
                            "poll_results_s3_key.$": "$.chunked_data.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.chunked_data.poll_results_s3_bucket",
                        },
                        "ResultPath": "$.reduce_state",
                        "Next": "CheckReduceComplete",
                        "Retry": [
                            {
                                "ErrorEquals": [
                                    "Lambda.ServiceException",
                                    "Lambda.AWSLambdaException",
                                    "Lambda.ResourceConflictException",
                                ],
                                "IntervalSeconds": 5,
                                "MaxAttempts": 5,
                                "BackoffRate": 2.0,
                                "JitterStrategy": "FULL",
                            }
                        ],
                        "Catch": [
                            {
                                "ErrorEquals": ["States.ALL"],
                                "Next": "WordReduceFailed",
                                "ResultPath": "$.error",
                            }
                        ],
                    },
                    "CheckReduceComplete": {
                        "Type": "Choice",
                        "Comment": "Check if reduction to single intermediate is complete",
                        "Choices": [
                            {
                                "Variable": "$.reduce_state.done",
                                "BooleanEquals": True,
                                "Next": "WordFinalMergeSingle",
                            }
                        ],
                        "Default": "MergePairsInParallel",
                    },
                    "MergePairsInParallel": {
                        "Type": "Map",
                        "Comment": "Parallel merge of pairs - O(log N) rounds",
                        "ItemsPath": "$.reduce_state.pairs",
                        "MaxConcurrency": 10,
                        "Parameters": {
                            "operation": "merge_pair",
                            "pair_data.$": "$$.Map.Item.Value",
                        },
                        "Iterator": {
                            "StartAt": "MergeSinglePair",
                            "States": {
                                "MergeSinglePair": {
                                    "Type": "Task",
                                    "Resource": arns[2],
                                    "End": True,
                                    "Retry": [
                                        {
                                            "ErrorEquals": [
                                                "Lambda.ServiceException",
                                                "Lambda.AWSLambdaException",
                                                "Lambda.ResourceConflictException",
                                                "Runtime.ExitError",
                                            ],
                                            "IntervalSeconds": 5,
                                            "MaxAttempts": 5,
                                            "BackoffRate": 2.0,
                                            "JitterStrategy": "FULL",
                                        },
                                    ],
                                },
                            },
                        },
                        "ResultPath": "$.merged_results",
                        "Next": "PrepareNextReduceRound",
                        "Catch": [
                            {
                                "ErrorEquals": ["States.ALL"],
                                "Next": "WordReduceFailed",
                                "ResultPath": "$.error",
                            }
                        ],
                    },
                    "PrepareNextReduceRound": {
                        "Type": "Task",
                        "Resource": arns[7],
                        "Comment": "Prepare next round of pair merging",
                        "Parameters": {
                            "intermediates.$": "$.merged_results",
                            "batch_id.$": "$.reduce_state.batch_id",
                            "database.$": "$.reduce_state.database",
                            "round.$": "$.reduce_state.round",
                            "poll_results_s3_key.$": "$.reduce_state.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.reduce_state.poll_results_s3_bucket",
                        },
                        "ResultPath": "$.reduce_state",
                        "Next": "CheckReduceComplete",
                        "Retry": [
                            {
                                "ErrorEquals": [
                                    "Lambda.ServiceException",
                                    "Lambda.AWSLambdaException",
                                    "Lambda.ResourceConflictException",
                                ],
                                "IntervalSeconds": 5,
                                "MaxAttempts": 5,
                                "BackoffRate": 2.0,
                                "JitterStrategy": "FULL",
                            }
                        ],
                        "Catch": [
                            {
                                "ErrorEquals": ["States.ALL"],
                                "Next": "WordReduceFailed",
                                "ResultPath": "$.error",
                            }
                        ],
                    },
                    "WordReduceFailed": {
                        "Type": "Fail",
                        "Error": "WordReduceFailed",
                        "Cause": "Failed during parallel reduce of word intermediates",
                    },
                    "NoWordChunksToProcess": {
                        "Type": "Pass",
                        "Comment": "No word chunks to process - prepare data for marking batches complete",
                        "Parameters": {
                            "poll_results_s3_key.$": "$.chunked_data.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.chunked_data.poll_results_s3_bucket",
                            "poll_results_s3_key_fallback.$": "$.chunked_data.poll_results_s3_key",
                            "poll_results_s3_bucket_fallback.$": "$.chunked_data.poll_results_s3_bucket",
                            "poll_results_s3_key_poll_data": None,
                            "poll_results_s3_bucket_poll_data": None,
                        },
                        "Next": "MarkWordBatchesComplete",
                    },
                    "WordFinalMergeSingle": {
                        "Type": "Task",
                        "Resource": arns[2],
                        "Comment": "Final merge of single intermediate to S3 snapshot",
                        "Parameters": {
                            "operation": "final_merge_single",
                            "batch_id.$": "$.reduce_state.batch_id",
                            "single_intermediate.$": "$.reduce_state.single_intermediate",
                            "database": "words",
                            "poll_results_s3_key.$": "$.reduce_state.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.reduce_state.poll_results_s3_bucket",
                        },
                        "ResultPath": "$.final_merge_result",
                        "OutputPath": "$",
                        "Next": "PrepareMarkWordBatchesComplete",
                        "Retry": [
                            {
                                "ErrorEquals": [
                                    "Lambda.ServiceException",
                                    "Lambda.AWSLambdaException",
                                    "Lambda.ResourceConflictException",
                                    "Runtime.ExitError",
                                ],
                                "IntervalSeconds": 5,
                                "MaxAttempts": 5,
                                "BackoffRate": 2.0,
                                "JitterStrategy": "FULL",
                            },
                            {
                                "ErrorEquals": ["States.TaskFailed"],
                                "IntervalSeconds": 30,
                                "MaxAttempts": 40,
                                "BackoffRate": 1.0,
                            },
                        ],
                        "Catch": [
                            {
                                "ErrorEquals": ["States.ALL"],
                                "Next": "WordCompactionFailed",
                                "ResultPath": "$.error",
                            }
                        ],
                    },
                    "PrepareMarkWordBatchesComplete": {
                        "Type": "Pass",
                        "Comment": "Prepare data for MarkWordBatchesComplete - normalize poll_results_s3_key",
                        "Parameters": {
                            "final_merge_result.$": "$.final_merge_result",
                            "poll_results_s3_key.$": "$.final_merge_result.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.final_merge_result.poll_results_s3_bucket",
                            "poll_results_s3_key_fallback.$": "$.reduce_state.poll_results_s3_key",
                            "poll_results_s3_bucket_fallback.$": "$.reduce_state.poll_results_s3_bucket",
                            "poll_results_s3_key_poll_data": None,
                            "poll_results_s3_bucket_poll_data": None,
                        },
                        "Next": "MarkWordBatchesComplete",
                    },
                    "MarkWordBatchesComplete": {
                        "Type": "Task",
                        "Resource": arns[6],
                        "Comment": "Mark batch summaries as COMPLETED after successful compaction",
                        "Parameters": {
                            # poll_results is always None after NormalizePollWordBatchesData (it's in S3)
                            # Handler will load from S3 using poll_results_s3_key when poll_results is null/empty
                            # Priority: primary > fallback > poll_data (source of truth)
                            "poll_results_s3_key.$": "$.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.poll_results_s3_bucket",
                            "poll_results_s3_key_fallback.$": "$.poll_results_s3_key_fallback",
                            "poll_results_s3_bucket_fallback.$": "$.poll_results_s3_bucket_fallback",
                            # poll_results_s3_key_poll_data is set by PrepareMarkWordBatchesComplete if available
                            "poll_results_s3_key_poll_data.$": "$.poll_results_s3_key_poll_data",
                            "poll_results_s3_bucket_poll_data.$": "$.poll_results_s3_bucket_poll_data",
                            "poll_results_s3_key_chunked": None,
                            "poll_results_s3_bucket_chunked": None,
                        },
                        "ResultPath": "$.mark_complete_result",
                        "End": True,
                        "Retry": [
                            {
                                "ErrorEquals": [
                                    "Lambda.ServiceException",
                                    "Lambda.AWSLambdaException",
                                    "Lambda.ResourceConflictException",
                                ],
                                "IntervalSeconds": 5,
                                "MaxAttempts": 3,
                                "BackoffRate": 2.0,
                                "JitterStrategy": "FULL",
                            }
                        ],
                        "Catch": [
                            {
                                "ErrorEquals": ["States.ALL"],
                                "Next": "MarkWordCompleteFailed",
                                "ResultPath": "$.mark_complete_error",
                            }
                        ],
                    },
                    "MarkWordCompleteFailed": {
                        "Type": "Fail",
                        "Error": "MarkWordCompleteFailed",
                        "Cause": "Failed to mark word batches as complete (compaction succeeded but marking failed)",
                    },
                    "WordChunkProcessingFailed": {
                        "Type": "Fail",
                        "Error": "WordChunkProcessingFailed",
                        "Cause": "Failed to process word delta chunk",
                    },
                    "WordCompactionFailed": {
                        "Type": "Fail",
                        "Error": "WordCompactionFailed",
                        "Cause": "Failed to compact word ChromaDB deltas",
                    },
                    "NoWordBatchesPending": {
                        "Type": "Succeed",
                        "Comment": "No pending word batches",
                    },
                },
            }
        )
