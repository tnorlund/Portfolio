"""Line embedding workflow component."""

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


class LineEmbeddingWorkflow(ComponentResource):
    """Line embedding submission and ingestion workflows."""

    def __init__(
        self,
        name: str,
        lambda_functions,
        batch_bucket,
        opts: Optional[ResourceOptions] = None,
    ):
        """Initialize line embedding workflow component.

        Args:
            name: Component name
            lambda_functions: Dictionary of Lambda functions
            batch_bucket: S3 bucket for batch files and poll results
            opts: Pulumi resource options
        """
        super().__init__(
            "custom:embedding:LineWorkflow",
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
            f"line-sf-role-{stack}",
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
            f"line-sf-lambda-invoke-{stack}",
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
        """Create the line embedding submission workflow."""
        return StateMachine(
            f"line-submit-sf-{stack}",
            role_arn=self.sf_role.arn,
            tags={"environment": stack},
            definition=Output.all(
                self.lambda_functions["embedding-line-find"].arn,
                self.lambda_functions["embedding-line-submit"].arn,
            ).apply(self._create_submit_definition),
            opts=ResourceOptions(parent=self),
        )

    def _create_submit_definition(self, arns: list) -> str:
        """Create submission workflow definition."""
        return json.dumps(
            {
                "Comment": "Find and submit line embeddings",
                "StartAt": "FindUnembedded",
                "States": {
                    "FindUnembedded": {
                        "Type": "Task",
                        "Resource": arns[0],
                        "Next": "SubmitBatches",
                    },
                    "SubmitBatches": {
                        "Type": "Map",
                        "ItemsPath": "$.batches",
                        "MaxConcurrency": 10,
                        "Iterator": {
                            "StartAt": "SubmitToOpenAI",
                            "States": {
                                "SubmitToOpenAI": {
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
        """Create the line embedding ingestion workflow."""
        return StateMachine(
            f"line-ingest-sf-{stack}",
            role_arn=self.sf_role.arn,
            tags={"environment": stack},
            definition=Output.all(
                self.lambda_functions["embedding-list-pending"].arn,
                self.lambda_functions["embedding-line-poll"].arn,
                self.lambda_functions["embedding-vector-compact"].arn,
                self.lambda_functions["embedding-normalize-poll-batches"].arn,
                self.lambda_functions["embedding-split-chunks"].arn,
                self.lambda_functions["embedding-create-chunk-groups"].arn,
                self.lambda_functions["embedding-mark-batches-complete"].arn,
                self.batch_bucket.bucket,
            ).apply(self._create_ingest_definition),
            opts=ResourceOptions(parent=self),
        )

    def _create_ingest_definition(self, arns_and_bucket: list) -> str:
        """Create ingestion workflow definition.

        arns_and_bucket[0] = embedding-list-pending
        arns_and_bucket[1] = embedding-line-poll
        arns_and_bucket[2] = embedding-vector-compact
        arns_and_bucket[3] = embedding-normalize-poll-batches
        arns_and_bucket[4] = embedding-split-chunks
        arns_and_bucket[5] = embedding-create-chunk-groups
        arns_and_bucket[6] = embedding-mark-batches-complete
        arns_and_bucket[7] = batch_bucket_name
        """
        arns = arns_and_bucket[:-1]
        batch_bucket_name = arns_and_bucket[-1]

        # Build state definition with proper bucket name interpolation
        state_definition = {
            "Comment": "Poll and ingest line embeddings",
            "StartAt": "ListPendingBatches",
            "States": {
                "ListPendingBatches": {
                    "Type": "Task",
                    "Resource": arns[0],
                "Parameters": {
                        "batch_type": "line",
                        "execution_id.$": "$$.Execution.Name",
                    },
                    "ResultPath": "$.list_result",
                    "Next": "CheckPendingBatches",
                },
                "CheckPendingBatches": {
                    "Type": "Choice",
                    "Comment": "Check if there are any pending batches",
                    "Choices": [
                        {
                            "Variable": "$.list_result.total_batches",
                            "NumericGreaterThan": 0,
                            "Next": "NormalizePendingBatches",
                        },
                    ],
                    "Default": "NoPendingBatches",
                },
                "NormalizePendingBatches": {
                    "Type": "Pass",
                    "Comment": "Normalize batches data structure for PollBatches Map state",
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
                    "Next": "PollBatches",
                },
                "PollBatches": {
                    "Type": "Map",
                    "Comment": "Poll batches in parallel - supports both inline and S3 manifest modes. Results are normalized and uploaded to S3 by NormalizePollBatchesData handler.",
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
                        "StartAt": "PollBatch",
                        "States": {
                            "PollBatch": {
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
                    "Next": "NormalizePollBatchesData",
                },
                "NormalizePollBatchesData": {
                    "Type": "Task",
                    "Resource": arns[3],
                    "Comment": "Normalize poll_results structure and upload to S3 if payload is too large",
                    "Parameters": {
                        "batch_id.$": "$$.Execution.Name",
                        "poll_results.$": "$.poll_results",
                    },
                    "ResultPath": "$.poll_results_data",
                    "Next": "SplitIntoChunks",
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
                            "Next": "CompactionFailed",
                            "ResultPath": "$.error",
                        }
                    ],
                },
                "SplitIntoChunks": {
                    "Type": "Task",
                    "Resource": arns[4],
                    "Comment": "Split delta results into chunks",
                    "Parameters": {
                        "batch_id.$": "$$.Execution.Name",
                        "poll_results.$": "$.poll_results_data.poll_results",
                        "poll_results_s3_key.$": "$.poll_results_data.poll_results_s3_key",
                        "poll_results_s3_bucket.$": "$.poll_results_data.poll_results_s3_bucket",
                    },
                    "ResultPath": "$.chunked_data",
                    "Next": "CheckChunksSource",
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
                            "Next": "CompactionFailed",
                            "ResultPath": "$.error",
                        }
                    ],
                },
                "CheckChunksSource": {
                        "Type": "Choice",
                        "Comment": "Check if chunks are in S3 or inline",
                        "Choices": [
                            {
                                "Variable": "$.chunked_data.use_s3",
                                "BooleanEquals": True,
                                "Next": "LoadChunksFromS3",
                            }
                        ],
                        "Default": "CheckForChunks",
                    },
                    "LoadChunksFromS3": {
                        "Type": "Task",
                        "Resource": arns[4],
                        "Comment": "Load chunks from S3 when payload is too large",
                        "Parameters": {
                            "operation": "load_chunks_from_s3",
                            "chunks_s3_key.$": "$.chunked_data.chunks_s3_key",
                            "chunks_s3_bucket.$": "$.chunked_data.chunks_s3_bucket",
                            "batch_id.$": "$.chunked_data.batch_id",
                            "poll_results_s3_key.$": "$.chunked_data.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.chunked_data.poll_results_s3_bucket",
                        },
                        "ResultPath": "$.chunked_data",
                        "Next": "CheckForChunks",
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
                                "Next": "CompactionFailed",
                                "ResultPath": "$.error",
                            }
                        ],
                    },
                    "CheckForChunks": {
                        "Type": "Choice",
                        "Comment": "Check if there are chunks",
                        "Choices": [
                            {
                                "Variable": "$.chunked_data.chunks[0]",
                                "IsPresent": True,
                                "Next": "NormalizeChunkData",
                            }
                        ],
                        "Default": "NoChunksToProcess",
                    },
                    "NormalizeChunkData": {
                        "Type": "Pass",
                        "Comment": "Normalize chunked_data to ensure chunks_s3_key and chunks_s3_bucket always exist",
                        "Parameters": {
                            "chunks.$": "$.chunked_data.chunks",
                            "batch_id.$": "$.chunked_data.batch_id",
                            "total_chunks.$": "$.chunked_data.total_chunks",
                            "use_s3.$": "$.chunked_data.use_s3",
                            "chunks_s3_key.$": "$.chunked_data.chunks_s3_key",
                            "chunks_s3_bucket.$": "$.chunked_data.chunks_s3_bucket",
                        },
                        "ResultPath": "$.chunked_data",
                        "Next": "ProcessChunksInParallel",
                    },
                    "ProcessChunksInParallel": {
                        "Type": "Map",
                        "Comment": "Process chunks in parallel",
                        "ItemsPath": "$.chunked_data.chunks",
                        "MaxConcurrency": 20,
                        "Parameters": {
                            "chunk.$": "$$.Map.Item.Value",
                            "chunks_s3_key.$": "$.chunked_data.chunks_s3_key",
                            "chunks_s3_bucket.$": "$.chunked_data.chunks_s3_bucket",
                            "use_s3.$": "$.chunked_data.use_s3",
                        },
                        "Iterator": {
                            "StartAt": "ProcessSingleChunk",
                            "States": {
                                "ProcessSingleChunk": {
                                    "Type": "Task",
                                    "Resource": arns[2],
                                    "Comment": "Process a single chunk",
                                    "Parameters": {
                                        "operation": "process_chunk",
                                        "batch_id.$": "$.chunk.batch_id",
                                        "chunk_index.$": (
                                            "$.chunk.chunk_index"
                                        ),
                                        "delta_results.$": (
                                            "$.chunk.delta_results"
                                        ),
                                        "chunks_s3_key.$": "$.chunks_s3_key",
                                        "chunks_s3_bucket.$": "$.chunks_s3_bucket",
                                        "database": "lines",
                                    },
                                    # Note: When use_s3=True, delta_results will be null/missing,
                                    # and the processing Lambda will download from S3 using chunks_s3_key
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
                        "Next": "GroupChunksForMerge",
                        "Catch": [
                            {
                                "ErrorEquals": ["States.ALL"],
                                "Next": "ChunkProcessingFailed",
                                "ResultPath": "$.error",
                            }
                        ],
                    },
                    "GroupChunksForMerge": {
                        "Type": "Pass",
                        "Comment": "Group processed chunks for hierarchical merging",
                        "Parameters": {
                            "batch_id.$": "$.chunked_data.batch_id",
                            "total_chunks.$": "$.chunked_data.total_chunks",
                            "chunk_results.$": "$.chunk_results",
                            "group_size": 10,
                            "poll_results.$": "$.poll_results",
                            # Use poll_results_data as source of truth for poll_results_s3_key/bucket
                            # SplitIntoChunks should include these in chunked_data, but poll_results_data is guaranteed to have them
                            "poll_results_s3_key.$": "$.poll_results_data.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.poll_results_data.poll_results_s3_bucket",
                        },
                        "Next": "CheckChunkGroupCount",
                    },
                    "CheckChunkGroupCount": {
                        "Type": "Choice",
                        "Comment": "Determine if hierarchical merge is beneficial",
                        "Choices": [
                            {
                                "Variable": "$.total_chunks",
                                "NumericGreaterThan": 4,
                                "Next": "CreateChunkGroups",
                            }
                        ],
                        "Default": "PrepareFinalMerge",
                    },
                    "CreateChunkGroups": {
                        "Type": "Task",
                        "Resource": arns[5],
                        "Comment": "Create chunk groups for parallel merging",
                        "Parameters": {
                            "batch_id.$": "$.batch_id",
                            "chunk_results.$": "$.chunk_results",
                            "group_size": 10,
                            "poll_results.$": "$.poll_results",
                            "poll_results_s3_key.$": "$.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.poll_results_s3_bucket",
                        },
                        "ResultPath": "$.chunk_groups",
                        "Next": "CheckChunkGroupsSource",
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
                                "Next": "GroupCreationFailed",
                                "ResultPath": "$.error",
                            }
                        ],
                    },
                    "CheckChunkGroupsSource": {
                        "Type": "Choice",
                        "Comment": "Check if groups are in S3 or inline",
                        "Choices": [
                            {
                                "Variable": "$.chunk_groups.use_s3",
                                "BooleanEquals": True,
                                "Next": "LoadChunkGroupsFromS3",
                            }
                        ],
                        "Default": "MergeChunkGroupsInParallel",
                    },
                    "LoadChunkGroupsFromS3": {
                        "Type": "Task",
                        "Resource": arns[5],
                        "Comment": "Load chunk groups from S3 when payload is too large",
                        "Parameters": {
                            "operation": "load_groups_from_s3",
                            "groups_s3_key.$": "$.chunk_groups.groups_s3_key",
                            "groups_s3_bucket.$": "$.chunk_groups.groups_s3_bucket",
                            "batch_id.$": "$.chunk_groups.batch_id",
                            "poll_results_s3_key.$": "$.chunk_groups.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.chunk_groups.poll_results_s3_bucket",
                        },
                        "ResultPath": "$.chunk_groups",
                        "Next": "MergeChunkGroupsInParallel",
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
                                "Next": "GroupCreationFailed",
                                "ResultPath": "$.error",
                            }
                        ],
                    },
                    "GroupCreationFailed": {
                        "Type": "Fail",
                        "Error": "GroupCreationFailed",
                        "Cause": "Failed to create chunk groups",
                    },
                    "MergeChunkGroupsInParallel": {
                        "Type": "Map",
                        "Comment": "Merge chunk groups in parallel for faster processing",
                        "ItemsPath": "$.chunk_groups.groups",
                        "MaxConcurrency": 6,
                        "Parameters": {
                            "chunk_group.$": "$$.Map.Item.Value.chunk_group",
                            "batch_id.$": "$.chunk_groups.batch_id",
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
                                        "database": "lines",
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
                        "Next": "PrepareHierarchicalFinalMerge",
                        "Catch": [
                            {
                                "ErrorEquals": ["States.ALL"],
                                "Next": "GroupMergeFailed",
                                "ResultPath": "$.error",
                            }
                        ],
                    },
                    "PrepareHierarchicalFinalMerge": {
                        "Type": "Pass",
                        "Comment": "Prepare data for final merge of pre-merged groups",
                        "Parameters": {
                            "batch_id.$": "$.chunk_groups.batch_id",
                            "operation": "final_merge",
                            "chunk_results.$": "$.merged_groups",
                            "poll_results.$": "$.chunk_groups.poll_results",
                            "poll_results_s3_key.$": "$.chunk_groups.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.chunk_groups.poll_results_s3_bucket",
                        },
                        "Next": "FinalMerge",
                    },
                    "GroupMergeFailed": {
                        "Type": "Fail",
                        "Error": "GroupMergeFailed",
                        "Cause": "Failed to merge chunk groups in parallel",
                    },
                    "PrepareFinalMerge": {
                        "Type": "Pass",
                        "Comment": "Prepare data for final merge",
                        "Parameters": {
                            "batch_id.$": "$.chunked_data.batch_id",
                            "chunk_results.$": "$.chunk_results",
                            "operation": "final_merge",
                            "poll_results.$": "$.poll_results",
                            "poll_results_s3_key.$": "$.chunked_data.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.chunked_data.poll_results_s3_bucket",
                        },
                        "Next": "FinalMerge",
                    },
                    "NoChunksToProcess": {
                        "Type": "Succeed",
                        "Comment": "No chunks to process",
                    },
                    "FinalMerge": {
                        "Type": "Task",
                        "Resource": arns[2],
                        "Comment": "Final merge of all chunks",
                        "Parameters": {
                            "operation": "final_merge",
                            "batch_id.$": "$.batch_id",
                            "chunk_results.$": "$.chunk_results",
                            "database": "lines",
                            "poll_results_s3_key.$": "$.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.poll_results_s3_bucket",
                        },
                        "ResultPath": "$.final_merge_result",
                        "OutputPath": "$",
                        "Next": "MarkBatchesComplete",
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
                                "IntervalSeconds": 10,
                                "MaxAttempts": 3,
                                "BackoffRate": 2.0,
                            },
                        ],
                    },
                    "MarkBatchesComplete": {
                        "Type": "Task",
                        "Resource": arns[6],
                        "Comment": "Mark batch summaries as COMPLETED after successful compaction",
                        "Parameters": {
                            "poll_results.$": "$.poll_results",
                            "poll_results_s3_key.$": "$.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.poll_results_s3_bucket",
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
                                "Next": "MarkCompleteFailed",
                                "ResultPath": "$.mark_complete_error",
                            }
                        ],
                    },
                    "MarkCompleteFailed": {
                        "Type": "Fail",
                        "Error": "MarkCompleteFailed",
                        "Cause": "Failed to mark batches as complete (compaction succeeded but marking failed)",
                    },
                    "ChunkProcessingFailed": {
                        "Type": "Fail",
                        "Error": "ChunkProcessingFailed",
                        "Cause": "Failed to process delta chunk",
                    },
                    "CompactionFailed": {
                        "Type": "Fail",
                        "Error": "CompactionFailed",
                        "Cause": "Failed to compact ChromaDB deltas",
                    },
                    "NoPendingBatches": {
                        "Type": "Succeed",
                        "Comment": "No pending batches to process",
                    },
                },
            }

        return json.dumps(state_definition)
