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
        opts: Optional[ResourceOptions] = None,
    ):
        """Initialize line embedding workflow component.

        Args:
            name: Component name
            lambda_functions: Dictionary of Lambda functions
            opts: Pulumi resource options
        """
        super().__init__(
            "custom:embedding:LineWorkflow",
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

        # Add permissions to invoke Lambda functions
        lambda_arns = [func.arn for func in self.lambda_functions.values()]

        RolePolicy(
            f"line-sf-lambda-invoke-{stack}",
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
                self.lambda_functions["embedding-split-chunks"].arn,
            ).apply(self._create_ingest_definition),
            opts=ResourceOptions(parent=self),
        )

    def _create_ingest_definition(self, arns: list) -> str:
        """Create ingestion workflow definition."""
        return json.dumps(
            {
                "Comment": "Poll and ingest line embeddings",
                "StartAt": "ListPendingBatches",
                "States": {
                    "ListPendingBatches": {
                        "Type": "Task",
                        "Resource": arns[0],
                        "Parameters": {"batch_type": "line"},
                        "ResultPath": "$.pending_batches",
                        "Next": "CheckPendingBatches",
                    },
                    "CheckPendingBatches": {
                        "Type": "Choice",
                        "Choices": [
                            {
                                "Variable": "$.pending_batches[0]",
                                "IsPresent": True,
                                "Next": "PollBatches",
                            },
                        ],
                        "Default": "NoPendingBatches",
                    },
                    "PollBatches": {
                        "Type": "Map",
                        "ItemsPath": "$.pending_batches",
                        "MaxConcurrency": 25,
                        "Parameters": {
                            "batch_id.$": "$$.Map.Item.Value.batch_id",
                            "openai_batch_id.$": (
                                "$$.Map.Item.Value.openai_batch_id"
                            ),
                            "skip_sqs_notification": True,
                        },
                        "Iterator": {
                            "StartAt": "PollBatch",
                            "States": {
                                "PollBatch": {
                                    "Type": "Task",
                                    "Resource": arns[1],
                                    "End": True,
                                },
                            },
                        },
                        "ResultPath": "$.poll_results",
                        "Next": "SplitIntoChunks",
                    },
                    "SplitIntoChunks": {
                        "Type": "Task",
                        "Resource": arns[3],
                        "Comment": "Split delta results into chunks",
                        "Parameters": {
                            "batch_id.$": "$$.Execution.Name",
                            "poll_results.$": "$.poll_results",
                        },
                        "ResultPath": "$.chunked_data",
                        "Next": "CheckForChunks",
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
                                "Next": "ProcessChunksInParallel",
                            }
                        ],
                        "Default": "NoChunksToProcess",
                    },
                    "ProcessChunksInParallel": {
                        "Type": "Map",
                        "Comment": "Process chunks in parallel",
                        "ItemsPath": "$.chunked_data.chunks",
                        "MaxConcurrency": 10,
                        "Parameters": {"chunk.$": "$$.Map.Item.Value"},
                        "Iterator": {
                            "StartAt": "ProcessSingleChunk",
                            "States": {
                                "ProcessSingleChunk": {
                                    "Type": "Task",
                                    "Resource": arns[2],
                                    "Comment": "Process a single chunk",
                                    "Parameters": {
                                        "operation.$": "$.chunk.operation",
                                        "batch_id.$": "$.chunk.batch_id",
                                        "chunk_index.$": (
                                            "$.chunk.chunk_index"
                                        ),
                                        "delta_results.$": (
                                            "$.chunk.delta_results"
                                        ),
                                        "database": "lines",
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
                        "Next": "PrepareFinalMerge",
                        "Catch": [
                            {
                                "ErrorEquals": ["States.ALL"],
                                "Next": "ChunkProcessingFailed",
                                "ResultPath": "$.error",
                            }
                        ],
                    },
                    "PrepareFinalMerge": {
                        "Type": "Pass",
                        "Comment": "Prepare data for final merge",
                        "Parameters": {
                            "batch_id.$": "$.chunked_data.batch_id",
                            "total_chunks.$": ("$.chunked_data.total_chunks"),
                            "operation": "final_merge",
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
                            "operation.$": "$.operation",
                            "batch_id.$": "$.batch_id",
                            "total_chunks.$": "$.total_chunks",
                            "database": "lines",
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
        )
