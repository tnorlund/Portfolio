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
                self.lambda_functions["embedding-word-find"].arn,
                self.lambda_functions["embedding-word-submit"].arn,
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
                self.lambda_functions["embedding-word-poll"].arn,
                self.lambda_functions["embedding-vector-compact"].arn,
                self.lambda_functions["embedding-normalize-poll-batches"].arn,
                self.lambda_functions["embedding-split-chunks"].arn,
                self.lambda_functions["embedding-prepare-chunk-groups"].arn,
                self.lambda_functions["embedding-mark-batches-complete"].arn,
                self.batch_bucket.bucket,
            ).apply(self._create_ingest_definition),
            opts=ResourceOptions(parent=self),
        )

    def _create_ingest_definition(self, arns_and_bucket: list) -> str:
        """Create ingestion workflow definition.

        arns_and_bucket[0] = embedding-list-pending
        arns_and_bucket[1] = embedding-word-poll
        arns_and_bucket[2] = embedding-vector-compact
        arns_and_bucket[3] = embedding-normalize-poll-batches
        arns_and_bucket[4] = embedding-split-chunks
        arns_and_bucket[5] = embedding-prepare-chunk-groups
        arns_and_bucket[6] = embedding-mark-batches-complete
        arns_and_bucket[7] = batch_bucket_name
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
                        "Next": "PrepareChunkGroups",
                        "Catch": [
                            {
                                "ErrorEquals": ["States.ALL"],
                                "Next": "WordChunkProcessingFailed",
                                "ResultPath": "$.error",
                            }
                        ],
                    },
                    "PrepareChunkGroups": {
                        "Type": "Task",
                        "Resource": arns[5],
                        "Comment": "Prepare chunk groups - decides hierarchical vs direct merge",
                        "Parameters": {
                            "chunked_data.$": "$.chunked_data",
                            "chunk_results.$": "$.chunk_results",
                            "group_size": 20,
                            "database": "words",
                        },
                        "ResultPath": "$.prepare_result",
                        "Next": "CheckHierarchicalMerge",
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
                                "Next": "WordGroupCreationFailed",
                                "ResultPath": "$.error",
                            }
                        ],
                    },
                    "CheckHierarchicalMerge": {
                        "Type": "Choice",
                        "Comment": "Branch based on hierarchical merge decision",
                        "Choices": [
                            {
                                "Variable": "$.prepare_result.use_hierarchical_merge",
                                "BooleanEquals": True,
                                "Next": "MergeChunkGroupsInParallel",
                            }
                        ],
                        "Default": "PrepareWordFinalMerge",
                    },
                    "WordGroupCreationFailed": {
                        "Type": "Fail",
                        "Error": "WordGroupCreationFailed",
                        "Cause": "Failed to prepare word chunk groups",
                    },
                    "MergeChunkGroupsInParallel": {
                        "Type": "Map",
                        "Comment": "Second parallel merge stage using chunk_results as input",
                        "ItemsPath": "$.prepare_result.chunk_groups.groups",
                        "MaxConcurrency": 6,
                        "Parameters": {
                            "chunk_group.$": "$$.Map.Item.Value.chunk_group",
                            "batch_id.$": "$.prepare_result.chunk_groups.batch_id",
                            "group_index.$": "$$.Map.Item.Value.group_index",
                            "groups_s3_key.$": "$$.Map.Item.Value.groups_s3_key",
                            "groups_s3_bucket.$": "$$.Map.Item.Value.groups_s3_bucket",
                        },
                        "Iterator": {
                            "StartAt": "MergeSingleChunkGroup",
                            "States": {
                                "MergeSingleChunkGroup": {
                                    "Type": "Task",
                                    "Resource": arns[2],
                                    "Comment": "Merge intermediate snapshots from chunk group",
                                    "Parameters": {
                                        "operation": "merge_chunk_group",
                                        "batch_id.$": "States.Format('{}-group-{}', $.batch_id, $.group_index)",
                                        "group_index.$": "$.group_index",
                                        "chunk_group.$": "$.chunk_group",
                                        "groups_s3_key.$": "$.groups_s3_key",
                                        "groups_s3_bucket.$": "$.groups_s3_bucket",
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
                                    ],
                                },
                            },
                        },
                        "ResultPath": "$.merged_groups",
                        "OutputPath": "$",
                        "Next": "PrepareWordHierarchicalFinalMerge",
                        "Catch": [
                            {
                                "ErrorEquals": ["States.ALL"],
                                "Next": "WordGroupMergeFailed",
                                "ResultPath": "$.error",
                            }
                        ],
                    },
                    "PrepareWordHierarchicalFinalMerge": {
                        "Type": "Pass",
                        "Comment": "Prepare data for final merge of pre-merged groups",
                        "Parameters": {
                            "batch_id.$": "$.prepare_result.batch_id",
                            "operation": "final_merge",
                            "chunk_results.$": "$.merged_groups",
                            # poll_results S3 keys come from PrepareChunkGroups
                            "poll_results_s3_key.$": "$.prepare_result.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.prepare_result.poll_results_s3_bucket",
                            # Fallback uses same source since PrepareChunkGroups passes through the S3 keys
                            "poll_results_s3_key_fallback.$": "$.prepare_result.poll_results_s3_key",
                            "poll_results_s3_bucket_fallback.$": "$.prepare_result.poll_results_s3_bucket",
                        },
                        "Next": "WordFinalMerge",
                    },
                    "WordGroupMergeFailed": {
                        "Type": "Fail",
                        "Error": "WordGroupMergeFailed",
                        "Cause": "Failed to merge word chunk groups in parallel",
                    },
                    "PrepareWordFinalMerge": {
                        "Type": "Pass",
                        "Comment": "Prepare data for direct final merge (skip hierarchical)",
                        "Parameters": {
                            "batch_id.$": "$.prepare_result.batch_id",
                            "chunk_results.$": "$.prepare_result.chunk_results",
                            "operation": "final_merge",
                            # poll_results S3 keys come from PrepareChunkGroups
                            "poll_results_s3_key.$": "$.prepare_result.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.prepare_result.poll_results_s3_bucket",
                            # Same source for fallback
                            "poll_results_s3_key_fallback.$": "$.prepare_result.poll_results_s3_key",
                            "poll_results_s3_bucket_fallback.$": "$.prepare_result.poll_results_s3_bucket",
                        },
                        "Next": "WordFinalMerge",
                    },
                    "NoWordChunksToProcess": {
                        "Type": "Pass",
                        "Comment": "No word chunks to process - prepare data for marking batches complete",
                        "Parameters": {
                            # poll_results S3 keys come from PrepareWordChunks (even when has_chunks=false)
                            "poll_results_s3_key.$": "$.chunked_data.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.chunked_data.poll_results_s3_bucket",
                            "poll_results_s3_key_fallback.$": "$.chunked_data.poll_results_s3_key",
                            "poll_results_s3_bucket_fallback.$": "$.chunked_data.poll_results_s3_bucket",
                            "poll_results_s3_key_poll_data": None,
                            "poll_results_s3_bucket_poll_data": None,
                        },
                        "Next": "MarkWordBatchesComplete",
                    },
                    "WordFinalMerge": {
                        "Type": "Task",
                        "Resource": arns[2],
                        "Comment": "Final merge of all word chunks",
                        "Parameters": {
                            "operation": "final_merge",
                            "batch_id.$": "$.batch_id",
                            "chunk_results.$": "$.chunk_results",
                            "database": "words",
                            "poll_results_s3_key.$": "$.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.poll_results_s3_bucket",
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
                                "IntervalSeconds": 30,  # 30 seconds - retry frequently since add_compaction_lock validates expired locks
                                "MaxAttempts": 40,  # Allow up to 20 minutes of retries (40 * 30s = 1200s) to cover 16 min lock duration + buffer
                                "BackoffRate": 1.0,  # No backoff - retry at fixed interval
                            },
                        ],
                    },
                    "PrepareMarkWordBatchesComplete": {
                        "Type": "Pass",
                        "Comment": "Prepare data for MarkWordBatchesComplete - normalize poll_results_s3_key from various possible locations",
                        "Parameters": {
                            # Preserve final_merge_result so it's available in execution output
                            # final_merge_result is small (~200-300 bytes) so we can keep it inline
                            "final_merge_result.$": "$.final_merge_result",
                            # poll_results is always None after NormalizePollWordBatchesData (it's in S3)
                            # MarkWordBatchesComplete handler will load from S3 using poll_results_s3_key
                            # Priority: final_merge_result > fallback (from PrepareWordHierarchicalFinalMerge) > root level
                            # Note: poll_results_data might not exist in hierarchical merge path, so we set poll_data to null
                            # The Lambda handler will check multiple locations including poll_results_s3_key_poll_data if passed
                            "poll_results_s3_key.$": "$.final_merge_result.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.final_merge_result.poll_results_s3_bucket",
                            # Use fallback from PrepareWordHierarchicalFinalMerge (more reliable than root level)
                            "poll_results_s3_key_fallback.$": "$.poll_results_s3_key_fallback",
                            "poll_results_s3_bucket_fallback.$": "$.poll_results_s3_bucket_fallback",
                            # Also check root level as secondary fallback
                            "poll_results_s3_key_root.$": "$.poll_results_s3_key",
                            "poll_results_s3_bucket_root.$": "$.poll_results_s3_bucket",
                            # Set to null since poll_results_data might not exist in hierarchical merge path
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
