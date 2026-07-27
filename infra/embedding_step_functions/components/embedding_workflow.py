"""Shared Pulumi component and ASL builders for embedding workflows."""

from __future__ import annotations

import json
from typing import Any, Optional

import pulumi
from pulumi import ComponentResource, Output, ResourceOptions
from pulumi_aws.iam import Role, RolePolicy
from pulumi_aws.sfn import StateMachine

stack = pulumi.get_stack()


def _lambda_retry(*, include_task_failed: bool = False) -> list[dict]:
    retries = [
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
            "ErrorEquals": ["Lambda.TooManyRequestsException"],
            "IntervalSeconds": 10,
            "MaxAttempts": 5,
            "BackoffRate": 2.0,
        },
    ]
    if include_task_failed:
        retries.append(
            {
                "ErrorEquals": ["States.TaskFailed"],
                "IntervalSeconds": 60,
                "MaxAttempts": 6,
                "BackoffRate": 1.5,
                "JitterStrategy": "FULL",
            }
        )
    return retries


def build_submit_definition(
    entity_type: str, find_arn: str, submit_arn: str
) -> str:
    """Build a bounded discovery/submit workflow for lines or words."""
    return json.dumps(
        {
            "Comment": f"Claim and submit a bounded page of {entity_type}",
            "StartAt": "FindAndClaimUnembedded",
            "States": {
                "FindAndClaimUnembedded": {
                    "Type": "Task",
                    "Resource": find_arn,
                    "Next": "SubmitBatches",
                    "Retry": _lambda_retry(),
                },
                "SubmitBatches": {
                    "Type": "Map",
                    "ItemsPath": "$.batches",
                    "MaxConcurrency": 10,
                    "ResultPath": None,
                    "Iterator": {
                        "StartAt": "SubmitToOpenAI",
                        "States": {
                            "SubmitToOpenAI": {
                                "Type": "Task",
                                "Resource": submit_arn,
                                "Retry": _lambda_retry(),
                                "End": True,
                            }
                        },
                    },
                    "End": True,
                },
            },
        }
    )


def build_ingest_definition(
    entity_type: str,
    list_arn: str,
    poll_arn: str,
    compact_arn: str,
    normalize_arn: str,
    mark_complete_arn: str,
) -> str:
    """Build the common poll, compact, and finalization workflow."""
    return json.dumps(
        {
            "Comment": f"Poll and publish {entity_type} embeddings",
            "StartAt": "ListActiveBatches",
            "States": {
                "ListActiveBatches": {
                    "Type": "Task",
                    "Resource": list_arn,
                    "Parameters": {
                        "batch_type": entity_type[:-1],
                        "execution_id.$": "$$.Execution.Name",
                        "max_batches": 250,
                    },
                    "ResultPath": "$.list_result",
                    "Next": "HasActiveBatches",
                    "Retry": _lambda_retry(),
                },
                "HasActiveBatches": {
                    "Type": "Choice",
                    "Choices": [
                        {
                            "Variable": "$.list_result.total_batches",
                            "NumericGreaterThan": 0,
                            "Next": "NormalizeActiveBatches",
                        }
                    ],
                    "Default": "NoActiveBatches",
                },
                "NormalizeActiveBatches": {
                    "Type": "Pass",
                    "Parameters": {
                        "batch_indices.$": "$.list_result.batch_indices",
                        "pending_batches.$": "$.list_result.pending_batches",
                        "manifest_s3_key.$": "$.list_result.manifest_s3_key",
                        "manifest_s3_bucket.$": "$.list_result.manifest_s3_bucket",
                    },
                    "ResultPath": "$.poll_batches_data",
                    "Next": "PollBatches",
                },
                "PollBatches": {
                    "Type": "Map",
                    "ItemsPath": "$.poll_batches_data.batch_indices",
                    "MaxConcurrency": 50,
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
                                "Resource": poll_arn,
                                "Retry": _lambda_retry(
                                    include_task_failed=True
                                ),
                                "End": True,
                            }
                        },
                    },
                    "ResultPath": "$.poll_results",
                    "Next": "PrepareChunks",
                },
                "PrepareChunks": {
                    "Type": "Task",
                    "Resource": normalize_arn,
                    "Parameters": {
                        "batch_id.$": "$$.Execution.Name",
                        "poll_results.$": "$.poll_results",
                        "database": entity_type,
                    },
                    "ResultPath": "$.chunked_data",
                    "Next": "HasChunks",
                    "Retry": _lambda_retry(),
                    "Catch": [
                        {
                            "ErrorEquals": ["States.ALL"],
                            "ResultPath": "$.error",
                            "Next": "CompactionFailed",
                        }
                    ],
                },
                "HasChunks": {
                    "Type": "Choice",
                    "Choices": [
                        {
                            "Variable": "$.chunked_data.has_chunks",
                            "BooleanEquals": True,
                            "Next": "ProcessChunks",
                        }
                    ],
                    "Default": "NoChunksToProcess",
                },
                "ProcessChunks": {
                    "Type": "Map",
                    "ItemsPath": "$.chunked_data.chunks",
                    "MaxConcurrency": 10,
                    "Parameters": {"chunk_item.$": "$$.Map.Item.Value"},
                    "Iterator": {
                        "StartAt": "ProcessChunk",
                        "States": {
                            "ProcessChunk": {
                                "Type": "Task",
                                "Resource": compact_arn,
                                "Parameters": {
                                    "operation": "process_chunk",
                                    "batch_id.$": "$.chunk_item.batch_id",
                                    "chunk_index.$": "$.chunk_item.chunk_index",
                                    "chunks_s3_key.$": "$.chunk_item.chunks_s3_key",
                                    "chunks_s3_bucket.$": "$.chunk_item.chunks_s3_bucket",
                                    "database": entity_type,
                                },
                                "Retry": _lambda_retry(),
                                "End": True,
                            }
                        },
                    },
                    "ResultPath": "$.chunk_results",
                    "Next": "FinalMerge",
                    "Catch": [
                        {
                            "ErrorEquals": ["States.ALL"],
                            "ResultPath": "$.error",
                            "Next": "ChunkProcessingFailed",
                        }
                    ],
                },
                "FinalMerge": {
                    "Type": "Task",
                    "Resource": compact_arn,
                    "Parameters": {
                        "operation": "final_merge_all",
                        "batch_id.$": "$.chunked_data.batch_id",
                        "chunk_results.$": "$.chunk_results",
                        "database": entity_type,
                        "poll_results_s3_key.$": "$.chunked_data.poll_results_s3_key",
                        "poll_results_s3_bucket.$": "$.chunked_data.poll_results_s3_bucket",
                    },
                    "ResultPath": "$.final_merge_result",
                    "Next": "PrepareFinalization",
                    "Retry": _lambda_retry()
                    + [
                        {
                            "ErrorEquals": ["States.TaskFailed"],
                            "IntervalSeconds": 30,
                            "MaxAttempts": 40,
                            "BackoffRate": 1.0,
                        }
                    ],
                    "Catch": [
                        {
                            "ErrorEquals": ["States.ALL"],
                            "ResultPath": "$.error",
                            "Next": "CompactionFailed",
                        }
                    ],
                },
                "PrepareFinalization": {
                    "Type": "Pass",
                    "Parameters": {
                        "poll_results_s3_key.$": "$.final_merge_result.poll_results_s3_key",
                        "poll_results_s3_bucket.$": "$.final_merge_result.poll_results_s3_bucket",
                    },
                    "Next": "FinalizeBatches",
                },
                "NoChunksToProcess": {
                    "Type": "Pass",
                    "Parameters": {
                        "poll_results_s3_key.$": "$.chunked_data.poll_results_s3_key",
                        "poll_results_s3_bucket.$": "$.chunked_data.poll_results_s3_bucket",
                    },
                    "Next": "FinalizeBatches",
                },
                "FinalizeBatches": {
                    "Type": "Task",
                    "Resource": mark_complete_arn,
                    "Parameters": {
                        "poll_results_s3_key.$": "$.poll_results_s3_key",
                        "poll_results_s3_bucket.$": "$.poll_results_s3_bucket",
                    },
                    "ResultPath": "$.finalization_result",
                    "Retry": _lambda_retry(),
                    "Catch": [
                        {
                            "ErrorEquals": ["States.ALL"],
                            "ResultPath": "$.error",
                            "Next": "FinalizationFailed",
                        }
                    ],
                    "End": True,
                },
                "FinalizationFailed": {
                    "Type": "Fail",
                    "Error": "FinalizationFailed",
                    "Cause": "Snapshot published but Dynamo finalization failed",
                },
                "ChunkProcessingFailed": {
                    "Type": "Fail",
                    "Error": "ChunkProcessingFailed",
                    "Cause": "Failed to process embedding delta chunk",
                },
                "CompactionFailed": {
                    "Type": "Fail",
                    "Error": "CompactionFailed",
                    "Cause": "Failed to publish the canonical snapshot",
                },
                "NoActiveBatches": {"Type": "Succeed"},
            },
        }
    )


class EmbeddingWorkflow(ComponentResource):
    """Parameterized line/word submission and ingestion resources."""

    def __init__(
        self,
        name: str,
        *,
        entity_type: str,
        component_type: str,
        lambda_functions: dict[str, Any],
        opts: Optional[ResourceOptions] = None,
    ) -> None:
        super().__init__(component_type, name, None, opts)
        if entity_type not in {"lines", "words"}:
            raise ValueError("entity_type must be lines or words")
        self.entity_type = entity_type
        self.lambda_functions = lambda_functions
        singular = entity_type[:-1]

        self.sf_role = Role(
            f"{singular}-sf-role-{stack}",
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

        used_names = [
            f"embedding-find-{entity_type}",
            f"embedding-submit-{entity_type}",
            "embedding-list-pending",
            f"embedding-poll-{entity_type}",
            "embedding-compact",
            "embedding-normalize-batches",
            "embedding-mark-complete",
        ]
        used_arns = [lambda_functions[key].arn for key in used_names]
        RolePolicy(
            f"{singular}-sf-lambda-invoke-{stack}",
            role=self.sf_role.id,
            policy=Output.all(*used_arns).apply(
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
            opts=ResourceOptions(parent=self),
        )

        self.submit_sf = StateMachine(
            f"{singular}-submit-sf-{stack}",
            role_arn=self.sf_role.arn,
            type="STANDARD",
            tags={"environment": stack},
            definition=Output.all(
                lambda_functions[f"embedding-find-{entity_type}"].arn,
                lambda_functions[f"embedding-submit-{entity_type}"].arn,
            ).apply(
                lambda arns: build_submit_definition(
                    entity_type, arns[0], arns[1]
                )
            ),
            opts=ResourceOptions(parent=self),
        )
        self.ingest_sf = StateMachine(
            f"{singular}-ingest-sf-{stack}",
            role_arn=self.sf_role.arn,
            type="STANDARD",
            tags={"environment": stack},
            definition=Output.all(
                lambda_functions["embedding-list-pending"].arn,
                lambda_functions[f"embedding-poll-{entity_type}"].arn,
                lambda_functions["embedding-compact"].arn,
                lambda_functions["embedding-normalize-batches"].arn,
                lambda_functions["embedding-mark-complete"].arn,
            ).apply(lambda arns: build_ingest_definition(entity_type, *arns)),
            opts=ResourceOptions(parent=self),
        )
        self.register_outputs(
            {
                "submit_sf_arn": self.submit_sf.arn,
                "ingest_sf_arn": self.ingest_sf.arn,
            }
        )
