"""Manual, idempotent embed-everything Step Function."""

from __future__ import annotations

import json
from typing import Any, Optional

import pulumi
from pulumi import ComponentResource, Output, ResourceOptions
from pulumi_aws.iam import Role, RolePolicy
from pulumi_aws.sfn import StateMachine

stack = pulumi.get_stack()


def _control_task(
    resource: str,
    action: str,
    *,
    result_path: str | None,
    next_state: str,
    extra_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "action": action,
        "owner.$": "$$.Execution.Id",
    }
    parameters.update(extra_parameters or {})
    state: dict[str, Any] = {
        "Type": "Task",
        "Resource": resource,
        "Parameters": parameters,
        "ResultPath": result_path,
        "Next": next_state,
        "Retry": [
            {
                "ErrorEquals": [
                    "Lambda.ServiceException",
                    "Lambda.AWSLambdaException",
                    "Lambda.ResourceConflictException",
                    "Lambda.TooManyRequestsException",
                ],
                "IntervalSeconds": 5,
                "MaxAttempts": 5,
                "BackoffRate": 2.0,
                "JitterStrategy": "FULL",
            }
        ],
    }
    return state


def _child_branch(
    state_machine_arn: str,
    *,
    branch_name: str,
    submission_namespace: str | None = None,
) -> dict[str, Any]:
    child_input: dict[str, Any] = {
        "AWS_STEP_FUNCTIONS_STARTED_BY_EXECUTION_ID.$": "$$.Execution.Id"
    }
    if submission_namespace:
        child_input["submission_namespace"] = submission_namespace
    state_name = f"Run{branch_name}"
    return {
        "StartAt": state_name,
        "States": {
            state_name: {
                "Type": "Task",
                "Resource": "arn:aws:states:::states:startExecution.sync:2",
                "Parameters": {
                    "StateMachineArn": state_machine_arn,
                    "Input": child_input,
                },
                "ResultPath": None,
                "End": True,
            }
        },
    }


def build_backfill_definition(
    control_arn: str,
    line_submit_arn: str,
    word_submit_arn: str,
    line_ingest_arn: str,
    word_ingest_arn: str,
) -> str:
    """Build the manual v1 backfill orchestration definition."""
    inspect = _control_task(
        control_arn,
        "inspect",
        result_path="$.status",
        next_state="RouteWork",
    )
    inspect["Catch"] = [
        {
            "ErrorEquals": ["States.ALL"],
            "ResultPath": "$.error",
            "Next": "ReleaseAfterFailure",
        }
    ]

    states: dict[str, Any] = {
        "AcquireLease": _control_task(
            control_arn,
            "acquire",
            result_path="$.lease",
            next_state="LeaseAcquired",
        ),
        "LeaseAcquired": {
            "Type": "Choice",
            "Choices": [
                {
                    "Variable": "$.lease.acquired",
                    "BooleanEquals": True,
                    "Next": "Inspect",
                }
            ],
            "Default": "AlreadyRunning",
        },
        "Inspect": inspect,
        "RouteWork": {
            "Type": "Choice",
            "Choices": [
                {
                    "Variable": "$.status.active_batches.total",
                    "NumericGreaterThan": 0,
                    "Next": "WaitForProvider",
                },
                {
                    "Or": [
                        {
                            "Variable": "$.status.backfill_phase",
                            "StringEquals": "NEW",
                        },
                        {
                            "Variable": "$.status.backfill_phase",
                            "StringEquals": "INITIALIZING",
                        },
                    ],
                    "Next": "InitializeOnce",
                },
                {
                    "Variable": "$.status.unembedded",
                    "NumericGreaterThan": 0,
                    "Next": "SubmitMissing",
                },
                {
                    "Variable": "$.status.pending",
                    "NumericGreaterThan": 0,
                    "Next": "WaitForConsistency",
                },
            ],
            "Default": "WaitForFixedPoint",
        },
        "InitializeOnce": _control_task(
            control_arn,
            "initialize",
            result_path="$.initialization",
            next_state="Inspect",
        ),
        "SubmitMissing": {
            "Type": "Parallel",
            "Branches": [
                _child_branch(
                    line_submit_arn,
                    branch_name="LineSubmit",
                    submission_namespace="backfill-v1",
                ),
                _child_branch(
                    word_submit_arn,
                    branch_name="WordSubmit",
                    submission_namespace="backfill-v1",
                ),
            ],
            "ResultPath": None,
            "Next": "WaitForProvider",
            "Catch": [
                {
                    "ErrorEquals": ["States.ALL"],
                    "ResultPath": "$.error",
                    "Next": "ReleaseAfterFailure",
                }
            ],
        },
        "WaitForProvider": {
            "Type": "Wait",
            "Seconds": 60,
            "Next": "IngestActive",
        },
        "IngestActive": {
            "Type": "Parallel",
            "Branches": [
                _child_branch(line_ingest_arn, branch_name="LineIngest"),
                _child_branch(word_ingest_arn, branch_name="WordIngest"),
            ],
            "ResultPath": None,
            "Next": "Inspect",
            "Catch": [
                {
                    "ErrorEquals": ["States.ALL"],
                    "ResultPath": "$.error",
                    "Next": "ReleaseAfterFailure",
                }
            ],
        },
        "WaitForConsistency": {
            "Type": "Wait",
            "Seconds": 30,
            "Next": "Inspect",
        },
        "WaitForFixedPoint": {
            "Type": "Wait",
            "Comment": "Confirm the eventually consistent status GSI is stable",
            "Seconds": 30,
            "Next": "ConfirmFixedPoint",
        },
        "ConfirmFixedPoint": _control_task(
            control_arn,
            "inspect",
            result_path="$.status",
            next_state="FixedPointConfirmed",
        ),
        "FixedPointConfirmed": {
            "Type": "Choice",
            "Choices": [
                {
                    "Variable": "$.status.work_remaining",
                    "NumericGreaterThan": 0,
                    "Next": "RouteWork",
                }
            ],
            "Default": "MarkBackfillComplete",
        },
        "MarkBackfillComplete": _control_task(
            control_arn,
            "complete",
            result_path="$.completion",
            next_state="ReleaseLease",
            extra_parameters={"counts.$": "$.status.counts"},
        ),
        "ReleaseLease": _control_task(
            control_arn,
            "release",
            result_path="$.release",
            next_state="BackfillComplete",
        ),
        "ReleaseAfterFailure": _control_task(
            control_arn,
            "release",
            result_path="$.release",
            next_state="BackfillFailed",
        ),
        "BackfillFailed": {
            "Type": "Fail",
            "Error": "EmbeddingBackfillFailed",
            "Cause": "The child workflow failed; rerun v1 to resume safely",
        },
        "BackfillComplete": {"Type": "Succeed"},
        "AlreadyRunning": {"Type": "Succeed"},
    }

    # Every mutating control task must release the lease on failure.
    for name in (
        "InitializeOnce",
        "ConfirmFixedPoint",
        "MarkBackfillComplete",
    ):
        states[name]["Catch"] = [
            {
                "ErrorEquals": ["States.ALL"],
                "ResultPath": "$.error",
                "Next": "ReleaseAfterFailure",
            }
        ]

    return json.dumps(
        {
            "Comment": "One-time, resumable embedding backfill v1",
            "TimeoutSeconds": 172800,
            "StartAt": "AcquireLease",
            "States": states,
        }
    )


class EmbedAllWorkflow(ComponentResource):
    """Manual parent workflow that drains both embedding collections."""

    def __init__(
        self,
        name: str,
        *,
        control_lambda,
        line_workflow,
        word_workflow,
        opts: Optional[ResourceOptions] = None,
    ) -> None:
        super().__init__("custom:embedding:EmbedAllWorkflow", name, None, opts)

        self.role = Role(
            f"embedding-backfill-sf-role-{stack}",
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
        child_state_machines = [
            line_workflow.submit_sf.arn,
            word_workflow.submit_sf.arn,
            line_workflow.ingest_sf.arn,
            word_workflow.ingest_sf.arn,
        ]
        RolePolicy(
            f"embedding-backfill-sf-policy-{stack}",
            role=self.role.id,
            policy=Output.all(control_lambda.arn, *child_state_machines).apply(
                self._policy
            ),
            opts=ResourceOptions(parent=self),
        )

        self.state_machine = StateMachine(
            f"embedding-backfill-v1-{stack}",
            role_arn=self.role.arn,
            type="STANDARD",
            tags={"environment": stack, "purpose": "one-time-backfill"},
            definition=Output.all(
                control_lambda.arn, *child_state_machines
            ).apply(lambda arns: build_backfill_definition(*arns)),
            opts=ResourceOptions(parent=self),
        )
        self.register_outputs({"state_machine_arn": self.state_machine.arn})

    @staticmethod
    def _policy(args: list[str]) -> str:
        control_arn, *child_arns = args
        return json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "lambda:InvokeFunction",
                        "Resource": control_arn,
                    },
                    {
                        "Effect": "Allow",
                        "Action": "states:StartExecution",
                        "Resource": child_arns,
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "states:DescribeExecution",
                            "states:StopExecution",
                        ],
                        "Resource": "arn:aws:states:*:*:execution:*:*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "events:PutTargets",
                            "events:PutRule",
                            "events:DescribeRule",
                        ],
                        "Resource": (
                            "arn:aws:events:*:*:rule/"
                            "StepFunctionsGetEventsForStepFunctionsExecutionRule"
                        ),
                    },
                ],
            }
        )
