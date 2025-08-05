"""
Step function for processing line embeddings with ChromaDB.

This creates a step function that:
1. Lists pending line embedding batches
2. Polls each batch in parallel using containerized Lambdas
3. Compacts all deltas into final ChromaDB collection
"""

import json
from typing import List

import pulumi
from pulumi import ComponentResource, Output, ResourceOptions
from pulumi_aws import get_caller_identity, get_region
from pulumi_aws.iam import Role, RolePolicy
from pulumi_aws.lambda_ import Function
from pulumi_aws.sfn import StateMachine

from dynamo_db import dynamodb_table
from .chromadb_lambdas import ChromaDBLambdas


class LineEmbeddingChromaDBStepFunction(ComponentResource):
    """Step function for processing line embeddings into ChromaDB."""

    def __init__(
        self,
        name: str,
        chromadb_lambdas: ChromaDBLambdas,
        list_pending_batches_lambda: Function,
        opts: ResourceOptions = None,
    ):
        super().__init__(
            "custom:chromadb:LineEmbeddingStepFunction",
            name,
            None,
            opts,
        )

        # Get AWS account details
        account_id = get_caller_identity().account_id
        region = get_region().name

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

        # Add policy to invoke Lambda functions
        RolePolicy(
            f"{name}-sfn-policy",
            role=self.step_function_role.id,
            policy=Output.all(
                list_pending_batches_lambda.arn,
                chromadb_lambdas.line_polling_lambda.arn,
                chromadb_lambdas.compaction_lambda.arn,
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
            opts=ResourceOptions(parent=self),
        )

        # Create the step function
        self.state_machine = StateMachine(
            f"{name}-sm",
            role_arn=self.step_function_role.arn,
            definition=Output.all(
                list_pending_batches_lambda.arn,
                chromadb_lambdas.line_polling_lambda.arn,
                chromadb_lambdas.compaction_lambda.arn,
            ).apply(
                lambda arns: json.dumps(
                    {
                        "StartAt": "ListPendingLineBatches",
                        "States": {
                            "ListPendingLineBatches": {
                                "Type": "Task",
                                "Resource": arns[0],
                                "Next": "PollLineEmbeddingBatch",
                            },
                            "PollLineEmbeddingBatch": {
                                "Type": "Map",
                                "ItemsPath": "$.body",
                                "MaxConcurrency": 10,
                                "Parameters": {
                                    "batch_id.$": "$$.Map.Item.Value.batch_id",
                                    "openai_batch_id.$": "$$.Map.Item.Value.openai_batch_id",
                                    "skip_sqs_notification": True,  # Skip individual SQS notifications
                                },
                                "Iterator": {
                                    "StartAt": "PollLineEmbeddingBatchTask",
                                    "States": {
                                        "PollLineEmbeddingBatchTask": {
                                            "Type": "Task",
                                            "Resource": arns[1],  # Line polling Lambda
                                            "End": True,
                                        }
                                    },
                                },
                                "ResultPath": "$.poll_results",
                                "Next": "CompactAllLineDeltas",
                            },
                            "CompactAllLineDeltas": {
                                "Type": "Task",
                                "Resource": arns[2],  # Compaction Lambda
                                "Parameters": {
                                    "delta_results.$": "$.poll_results[*]",
                                    "collection_type": "line_embeddings",
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
                "state_machine_arn": self.state_machine.arn,
                "state_machine_name": self.state_machine.name,
            }
        )