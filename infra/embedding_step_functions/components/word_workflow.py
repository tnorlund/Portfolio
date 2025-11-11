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
        opts: Optional[ResourceOptions] = None,
    ):
        """Initialize word embedding workflow component.

        Args:
            name: Component name
            lambda_functions: Dictionary of Lambda functions
            opts: Pulumi resource options
        """
        super().__init__(
            "custom:embedding:WordWorkflow",
            name,
            None,
            opts,
        )

        self.lambda_functions = lambda_functions

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

        # Add permissions to invoke Lambda functions
        lambda_arns = [func.arn for func in self.lambda_functions.values()]

        RolePolicy(
            f"word-sf-lambda-invoke-{stack}",
            role=self.sf_role.id,
            policy=Output.all(*lambda_arns).apply(
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
                self.lambda_functions["embedding-split-chunks"].arn,
                self.lambda_functions["embedding-create-chunk-groups"].arn,
                self.lambda_functions["embedding-mark-batches-complete"].arn,
            ).apply(self._create_ingest_definition),
            opts=ResourceOptions(parent=self),
        )

    def _create_ingest_definition(self, arns: list) -> str:
        """Create ingestion workflow definition.

        arns[0] = embedding-list-pending
        arns[1] = embedding-word-poll
        arns[2] = embedding-vector-compact
        arns[3] = embedding-split-chunks
        arns[4] = embedding-create-chunk-groups
        arns[5] = embedding-mark-batches-complete
        """
        return json.dumps(
            {
                "Comment": "Poll and ingest word embeddings",
                "StartAt": "ListPendingWordBatches",
                "States": {
                    "ListPendingWordBatches": {
                        "Type": "Task",
                        "Resource": arns[0],
                        "Parameters": {"batch_type": "word"},
                        "ResultPath": "$.pending_batches",
                        "Next": "CheckPendingWordBatches",
                    },
                    "CheckPendingWordBatches": {
                        "Type": "Choice",
                        "Choices": [
                            {
                                "Variable": "$.pending_batches[0]",
                                "IsPresent": True,
                                "Next": "PollWordBatches",
                            },
                        ],
                        "Default": "NoWordBatchesPending",
                    },
                    "PollWordBatches": {
                        "Type": "Map",
                        "ItemsPath": "$.pending_batches",
                        "MaxConcurrency": 50,
                        "Parameters": {
                            "batch_id.$": "$$.Map.Item.Value.batch_id",
                            "openai_batch_id.$": (
                                "$$.Map.Item.Value.openai_batch_id"
                            ),
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
                        "Next": "SplitWordIntoChunks",
                    },
                    "SplitWordIntoChunks": {
                        "Type": "Task",
                        "Resource": arns[3],
                        "Comment": "Split word delta results",
                        "Parameters": {
                            "batch_id.$": "$$.Execution.Name",
                            "poll_results.$": "$.poll_results",
                        },
                        "ResultPath": "$.chunked_data",
                        "Next": "CheckWordChunksSource",
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
                    "CheckWordChunksSource": {
                        "Type": "Choice",
                        "Comment": "Check if chunks are in S3 or inline",
                        "Choices": [
                            {
                                "Variable": "$.chunked_data.use_s3",
                                "BooleanEquals": True,
                                "Next": "LoadWordChunksFromS3",
                            }
                        ],
                        "Default": "CheckForWordChunks",
                    },
                    "LoadWordChunksFromS3": {
                        "Type": "Task",
                        "Resource": arns[3],
                        "Comment": "Load chunks from S3 when payload is too large",
                        "Parameters": {
                            "operation": "load_chunks_from_s3",
                            "chunks_s3_key.$": "$.chunked_data.chunks_s3_key",
                            "chunks_s3_bucket.$": "$.chunked_data.chunks_s3_bucket",
                            "batch_id.$": "$.chunked_data.batch_id",
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
                        "Comment": "Check if there are chunks",
                        "Choices": [
                            {
                                "Variable": "$.chunked_data.chunks[0]",
                                "IsPresent": True,
                                "Next": "NormalizeWordChunkData",
                            }
                        ],
                        "Default": "NoWordChunksToProcess",
                    },
                    "NormalizeWordChunkData": {
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
                        "Next": "ProcessWordChunksInParallel",
                    },
                    "ProcessWordChunksInParallel": {
                        "Type": "Map",
                        "Comment": "Process word chunks in parallel",
                        "ItemsPath": "$.chunked_data.chunks",
                        "MaxConcurrency": 5,
                        "Parameters": {
                            "chunk.$": "$$.Map.Item.Value",
                            "chunks_s3_key.$": "$.chunked_data.chunks_s3_key",
                            "chunks_s3_bucket.$": "$.chunked_data.chunks_s3_bucket",
                            "use_s3.$": "$.chunked_data.use_s3",
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
                                        "chunk_index.$": (
                                            "$.chunk.chunk_index"
                                        ),
                                        "delta_results.$": (
                                            "$.chunk.delta_results"
                                        ),
                                        "chunks_s3_key.$": "$.chunks_s3_key",
                                        "chunks_s3_bucket.$": "$.chunks_s3_bucket",
                                        "database": "words",
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
                                "Next": "WordChunkProcessingFailed",
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
                        "Default": "PrepareWordFinalMerge",
                    },
                    "CreateChunkGroups": {
                        "Type": "Task",
                        "Resource": arns[4],
                        "Comment": "Create chunk groups for parallel merging",
                        "Parameters": {
                            "batch_id.$": "$.batch_id",
                            "chunk_results.$": "$.chunk_results",
                            "group_size": 10,
                            "poll_results.$": "$.poll_results",
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
                                "Next": "WordGroupCreationFailed",
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
                        "Resource": arns[4],
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
                                "Next": "WordGroupCreationFailed",
                                "ResultPath": "$.error",
                            }
                        ],
                    },
                    "WordGroupCreationFailed": {
                        "Type": "Fail",
                        "Error": "WordGroupCreationFailed",
                        "Cause": "Failed to create word chunk groups",
                    },
                    "MergeChunkGroupsInParallel": {
                        "Type": "Map",
                        "Comment": "Second parallel merge stage using chunk_results as input",
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
                            "batch_id.$": "$.chunk_groups.batch_id",
                            "operation": "final_merge",
                            "chunk_results.$": "$.merged_groups",
                            "poll_results.$": "$.chunk_groups.poll_results",
                            "poll_results_s3_key.$": "$.chunk_groups.poll_results_s3_key",
                            "poll_results_s3_bucket.$": "$.chunk_groups.poll_results_s3_bucket",
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
                        "Comment": "Prepare data for final merge",
                        "Parameters": {
                            "batch_id.$": "$.chunked_data.batch_id",
                            "chunk_results.$": "$.chunk_results",
                            "operation": "final_merge",
                            "poll_results.$": "$.poll_results",
                        },
                        "Next": "WordFinalMerge",
                    },
                    "NoWordChunksToProcess": {
                        "Type": "Succeed",
                        "Comment": "No word chunks to process",
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
                        },
                        "ResultPath": "$.final_merge_result",
                        "OutputPath": "$",
                        "Next": "MarkWordBatchesComplete",
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
                    "MarkWordBatchesComplete": {
                        "Type": "Task",
                        "Resource": arns[5],
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
