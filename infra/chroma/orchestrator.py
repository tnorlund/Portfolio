from __future__ import annotations

import json
from typing import Optional
from pathlib import Path

import pulumi
import pulumi_aws as aws
from pulumi import ResourceOptions


class ChromaOrchestrator(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        *,
        cluster_arn: pulumi.Input[str],
        service_arn: pulumi.Input[str],
        chroma_endpoint: pulumi.Input[str],
        worker_lambda_arn: pulumi.Input[str],
        lambda_role_name: pulumi.Input[str],
        opts: Optional[ResourceOptions] = None,
    ) -> None:
        super().__init__("custom:chroma:Orchestrator", name, None, opts)

        # Wait-for-heartbeat Lambda (zip-based)
        self.wait_role = aws.iam.Role(
            f"{name}-wait-role",
            assume_role_policy='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}',
            opts=ResourceOptions(parent=self),
        )
        aws.iam.RolePolicyAttachment(
            f"{name}-wait-basic",
            role=self.wait_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self),
        )

        handler_dir = Path(__file__).resolve().parent / "wait_handler"

        self.wait_fn = aws.lambda_.Function(
            f"{name}-wait",
            runtime="python3.12",
            role=self.wait_role.arn,
            handler="index.lambda_handler",
            code=pulumi.AssetArchive(
                {
                    ".": pulumi.FileArchive(str(handler_dir)),
                }
            ),
            environment={
                "variables": {
                    "CHROMA_HTTP_ENDPOINT": chroma_endpoint,
                    "WAIT_TIMEOUT_SECONDS": "120",
                    "WAIT_INTERVAL_SECONDS": "5",
                }
            },
            architectures=["arm64"],
            timeout=180,
            opts=ResourceOptions(parent=self),
        )

        # State machine role
        self.sfn_role = aws.iam.Role(
            f"{name}-sfn-role",
            assume_role_policy='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"states.amazonaws.com"},"Action":"sts:AssumeRole"}]}',
            opts=ResourceOptions(parent=self),
        )

        # Allow update/describe ECS service and invoke Lambdas
        policy_doc = pulumi.Output.all(
            cluster_arn, service_arn, self.wait_fn.arn, worker_lambda_arn
        ).apply(
            lambda args: json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "ecs:UpdateService",
                                "ecs:DescribeServices",
                            ],
                            "Resource": [args[1]],
                        },
                        {
                            "Effect": "Allow",
                            "Action": [
                                "lambda:InvokeFunction",
                            ],
                            "Resource": [args[2], args[3]],
                        },
                    ],
                }
            )
        )
        self.sfn_policy = aws.iam.RolePolicy(
            f"{name}-sfn-policy",
            role=self.sfn_role.name,
            policy=policy_doc,
            opts=ResourceOptions(parent=self),
        )

        # State machine definition
        definition = pulumi.Output.all(
            cluster_arn, service_arn, self.wait_fn.arn, worker_lambda_arn
        ).apply(
            lambda args: json.dumps(
                {
                    "Comment": "Scale ECS up, wait for Chroma, run worker, scale down",
                    "StartAt": "ScaleUp",
                    "States": {
                        "ScaleUp": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::aws-sdk:ecs:updateService",
                            "Parameters": {
                                "Cluster": args[0],
                                "Service": args[1],
                                "DesiredCount": 1,
                            },
                            "Next": "WaitForHealthy",
                        },
                        "WaitForHealthy": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::lambda:invoke",
                            "Parameters": {
                                "FunctionName": args[2],
                                "Payload": {},
                            },
                            "Next": "RunWorker",
                        },
                        "RunWorker": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::lambda:invoke",
                            "Parameters": {
                                "FunctionName": args[3],
                                "Payload.$": "$",
                            },
                            "Next": "ScaleDown",
                        },
                        "ScaleDown": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::aws-sdk:ecs:updateService",
                            "Parameters": {
                                "Cluster": args[0],
                                "Service": args[1],
                                "DesiredCount": 0,
                            },
                            "End": True,
                        },
                    },
                }
            )
        )

        self.state_machine = aws.sfn.StateMachine(
            f"{name}-sfn",
            role_arn=self.sfn_role.arn,
            definition=definition,
            opts=ResourceOptions(parent=self),
        )

        self.register_outputs(
            {
                "state_machine_arn": self.state_machine.arn,
                "wait_lambda_arn": self.wait_fn.arn,
            }
        )
