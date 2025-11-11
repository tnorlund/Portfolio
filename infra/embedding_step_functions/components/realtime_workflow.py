"""Realtime embedding workflow component."""

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


class RealtimeEmbeddingWorkflow(ComponentResource):
    """Realtime embedding workflow for processing all receipts."""

    def __init__(
        self,
        name: str,
        lambda_functions,
        opts: Optional[ResourceOptions] = None,
    ):
        """Initialize realtime embedding workflow component.

        Args:
            name: Component name
            lambda_functions: Dictionary of Lambda functions
            opts: Pulumi resource options
        """
        super().__init__(
            "custom:embedding:RealtimeWorkflow",
            name,
            None,
            opts,
        )

        self.lambda_functions = lambda_functions

        # Create IAM role for Step Functions
        self._create_step_function_role()

        # Create realtime embedding workflow
        self.realtime_sf = self._create_realtime_workflow()

        # Register outputs
        self.register_outputs(
            {
                "realtime_sf_arn": self.realtime_sf.arn,
            }
        )

    def _create_step_function_role(self):
        """Create IAM role for Step Functions."""
        self.sf_role = Role(
            f"realtime-sf-role-{stack}",
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
        lambda_arns = [
            self.lambda_functions["embedding-find-receipts-realtime"].arn,
            self.lambda_functions["embedding-process-receipt-realtime"].arn,
        ]

        RolePolicy(
            f"realtime-sf-lambda-invoke-{stack}",
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

    def _create_realtime_workflow(self) -> StateMachine:
        """Create the realtime embedding workflow."""
        return StateMachine(
            f"realtime-embedding-sf-{stack}",
            role_arn=self.sf_role.arn,
            tags={"environment": stack},
            definition=Output.all(
                self.lambda_functions["embedding-find-receipts-realtime"].arn,
                self.lambda_functions["embedding-process-receipt-realtime"].arn,
            ).apply(self._create_realtime_definition),
            opts=ResourceOptions(parent=self),
        )

    def _create_realtime_definition(self, arns: list) -> str:
        """Create realtime workflow definition."""
        return json.dumps(
            {
                "Comment": "Realtime embedding workflow for all receipts",
                "StartAt": "FindReceipts",
                "States": {
                    "FindReceipts": {
                        "Type": "Task",
                        "Resource": arns[0],
                        "ResultPath": "$.find_result",
                        "Next": "CheckReceipts",
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
                            }
                        ],
                    },
                    "CheckReceipts": {
                        "Type": "Choice",
                        "Choices": [
                            {
                                "Variable": "$.find_result.receipts[0]",
                                "IsPresent": True,
                                "Next": "ProcessReceipts",
                            }
                        ],
                        "Default": "NoReceipts",
                    },
                    "ProcessReceipts": {
                        "Type": "Map",
                        "ItemsPath": "$.find_result.receipts",
                        "MaxConcurrency": 10,
                        "Iterator": {
                            "StartAt": "ProcessReceipt",
                            "States": {
                                "ProcessReceipt": {
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
                                        }
                                    ],
                                    "Catch": [
                                        {
                                            "ErrorEquals": ["States.ALL"],
                                            "Next": "ProcessReceiptFailed",
                                            "ResultPath": "$.error",
                                        }
                                    ],
                                },
                                "ProcessReceiptFailed": {
                                    "Type": "Pass",
                                    "End": True,
                                },
                            },
                        },
                        "End": True,
                    },
                    "NoReceipts": {
                        "Type": "Pass",
                        "Result": "No receipts found to process",
                        "End": True,
                    },
                },
            }
        )





