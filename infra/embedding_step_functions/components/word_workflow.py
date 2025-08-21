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
            ).apply(self._create_ingest_definition),
            opts=ResourceOptions(parent=self),
        )

    def _create_ingest_definition(self, arns: list) -> str:
        """Create ingestion workflow definition."""
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
                                                "Runtime.ExitError",
                                            ],
                                            "IntervalSeconds": 2,
                                            "MaxAttempts": 3,
                                            "BackoffRate": 2.0,
                                            "JitterStrategy": "FULL",
                                        },
                                        {
                                            "ErrorEquals": [
                                                "Lambda.TooManyRequestsException"
                                            ],
                                            "IntervalSeconds": 5,
                                            "MaxAttempts": 3,
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
                        "Next": "CheckForWordChunks",
                        "Retry": [
                            {
                                "ErrorEquals": [
                                    "Lambda.ServiceException",
                                    "Lambda.AWSLambdaException",
                                ],
                                "IntervalSeconds": 1,
                                "MaxAttempts": 2,
                                "BackoffRate": 1.5,
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
                                "Next": "ProcessWordChunksInParallel",
                            }
                        ],
                        "Default": "NoWordChunksToProcess",
                    },
                    "ProcessWordChunksInParallel": {
                        "Type": "Map",
                        "Comment": "Process word chunks in parallel",
                        "ItemsPath": "$.chunked_data.chunks",
                        "MaxConcurrency": 15,
                        "Parameters": {"chunk.$": "$$.Map.Item.Value"},
                        "Iterator": {
                            "StartAt": "ProcessSingleWordChunk",
                            "States": {
                                "ProcessSingleWordChunk": {
                                    "Type": "Task",
                                    "Resource": arns[2],
                                    "Comment": "Process a single word chunk",
                                    "Parameters": {
                                        "operation": "process_chunk_hierarchical",
                                        "batch_id.$": "$.chunk.batch_id",
                                        "chunk_index.$": (
                                            "$.chunk.chunk_index"
                                        ),
                                        "delta_results.$": (
                                            "$.chunk.delta_results"
                                        ),
                                        "database": "words",
                                    },
                                    "End": True,
                                    "Retry": [
                                        {
                                            "ErrorEquals": [
                                                "Lambda.ServiceException",
                                                "Lambda.AWSLambdaException",
                                                "Runtime.ExitError",
                                            ],
                                            "IntervalSeconds": 2,
                                            "MaxAttempts": 3,
                                            "BackoffRate": 2.0,
                                            "JitterStrategy": "FULL",
                                        },
                                        {
                                            "ErrorEquals": [
                                                "Lambda."
                                                "TooManyRequestsException"
                                            ],
                                            "IntervalSeconds": 5,
                                            "MaxAttempts": 3,
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
                            "group_size": 3,
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
                        "Type": "Pass",
                        "Comment": "Create chunk groups for parallel merging (simple grouping for now)",
                        "Parameters": {
                            "batch_id.$": "$.batch_id",
                            "groups.$": "States.ArrayPartition($.chunk_results, 3)",
                            "total_groups.$": "States.ArrayLength(States.ArrayPartition($.chunk_results, 3))",
                        },
                        "ResultPath": "$.chunk_groups",
                        "Next": "MergeChunkGroupsInParallel",
                    },
                    "MergeChunkGroupsInParallel": {
                        "Type": "Map",
                        "Comment": "Second parallel merge stage using chunk_results as input",
                        "ItemsPath": "$.chunk_groups.groups",
                        "MaxConcurrency": 6,
                        "Parameters": {
                            "chunk_group.$": "$$.Map.Item.Value",
                            "batch_id.$": "$.batch_id",
                            "group_index.$": "$$.Map.Item.Index",
                        },
                        "Iterator": {
                            "StartAt": "ExtractDeltaResults",
                            "States": {
                                "ExtractDeltaResults": {
                                    "Type": "Pass",
                                    "Comment": "Extract and combine original delta_results from chunk group",
                                    "Parameters": {
                                        "operation": "process_chunk_combined",
                                        "batch_id.$": "States.Format('{}-group-{}', $.batch_id, $.group_index)",
                                        "chunk_index.$": "$.group_index",
                                        "delta_results.$": "$.chunk_group",
                                        "database": "words",
                                    },
                                    "Next": "ProcessCombinedDeltas",
                                },
                                "ProcessCombinedDeltas": {
                                    "Type": "Task",
                                    "Resource": arns[2],
                                    "Comment": "Process combined delta results from chunk group",
                                    "Parameters": {
                                        "operation.$": "$.operation",
                                        "batch_id.$": "$.batch_id",
                                        "chunk_index.$": "$.chunk_index",
                                        "delta_results.$": "$.delta_results",
                                        "database.$": "$.database",
                                    },
                                    "End": True,
                                    "Retry": [
                                        {
                                            "ErrorEquals": [
                                                "Lambda.ServiceException",
                                                "Lambda.AWSLambdaException",
                                                "Runtime.ExitError",
                                            ],
                                            "IntervalSeconds": 2,
                                            "MaxAttempts": 3,
                                            "BackoffRate": 2.0,
                                            "JitterStrategy": "FULL",
                                        },
                                    ],
                                },
                            },
                        },
                        "ResultPath": "$.merged_groups",
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
                            "batch_id.$": "$.batch_id",
                            "operation": "final_merge",
                            "chunk_results.$": "$.merged_groups",
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
                            "total_chunks.$": ("$.chunked_data.total_chunks"),
                            "operation": "final_merge",
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
                            "operation.$": "$.operation",
                            "batch_id.$": "$.batch_id",
                            "chunk_results.$": "$.chunk_results",
                            "database": "words",
                        },
                        "End": True,
                        "Retry": [
                            {
                                "ErrorEquals": [
                                    "Lambda.ServiceException",
                                    "Lambda.AWSLambdaException",
                                    "Runtime.ExitError",
                                ],
                                "IntervalSeconds": 1,
                                "MaxAttempts": 3,
                                "BackoffRate": 1.5,
                                "JitterStrategy": "FULL",
                            },
                            {
                                "ErrorEquals": ["States.TaskFailed"],
                                "IntervalSeconds": 3,
                                "MaxAttempts": 2,
                                "BackoffRate": 2.0,
                            },
                        ],
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
