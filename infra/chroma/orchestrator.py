from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

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
        # Optional non-VPC egress Lambda for external API calls
        egress_lambda_arn: pulumi.Input[str] | None = None,
        nat_instance_id: pulumi.Input[str] | None = None,
        lambda_role_name: pulumi.Input[str],
        subnets: pulumi.Input[list[str]] | None = None,
        security_group_id: pulumi.Input[str] | None = None,
        opts: Optional[ResourceOptions] = None,
    ) -> None:
        super().__init__("custom:chroma:Orchestrator", name, None, opts)

        # Wait-for-heartbeat Lambda (zip-based)
        self.wait_role = aws.iam.Role(
            f"{name}-wait-role",
            assume_role_policy='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}',
            opts=ResourceOptions(parent=self),
        )
        wait_basic = aws.iam.RolePolicyAttachment(
            f"{name}-wait-basic",
            role=self.wait_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self),
        )

        # Allow ENI creation for VPC-enabled Lambda
        wait_vpc = aws.iam.RolePolicyAttachment(
            f"{name}-wait-vpc-access",
            role=self.wait_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
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
                    "ECS_CLUSTER_ARN": cluster_arn,
                    "ECS_SERVICE_ARN": service_arn,
                }
            },
            architectures=["arm64"],
            timeout=180,
            vpc_config=aws.lambda_.FunctionVpcConfigArgs(
                security_group_ids=([security_group_id] if security_group_id else None),
                subnet_ids=subnets if subnets else None,
            ),
            opts=ResourceOptions(parent=self, depends_on=[wait_basic, wait_vpc]),
        )

        # Allow the wait Lambda to discover ECS tasks/IPs
        aws.iam.RolePolicy(
            f"{name}-wait-ecs-policy",
            role=self.wait_role.name,
            policy=pulumi.Output.all(cluster_arn, service_arn).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "ecs:ListTasks",
                                    "ecs:DescribeTasks",
                                ],
                                "Resource": [args[0]],
                            }
                        ],
                    }
                )
            ),
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
            cluster_arn,
            service_arn,
            self.wait_fn.arn,
            worker_lambda_arn,
            nat_instance_id,
            egress_lambda_arn,
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
                            "Resource": [
                                arn for arn in [args[2], args[3], args[5]] if arn
                            ],
                        },
                        # EC2 permissions for NAT instance start/stop/describe (resource must be "*")
                        {
                            "Effect": "Allow",
                            "Action": [
                                "ec2:StartInstances",
                                "ec2:StopInstances",
                                "ec2:DescribeInstanceStatus",
                                "ec2:DescribeInstances",
                            ],
                            "Resource": "*",
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
            cluster_arn,
            service_arn,
            self.wait_fn.arn,
            worker_lambda_arn,
            nat_instance_id,
            egress_lambda_arn,
        ).apply(
            lambda args: json.dumps(
                {
                    "Comment": "Scale ECS up, wait for running tasks, HTTP readiness, run worker, scale down (with optional NAT start/stop)",
                    "StartAt": "StartNat",
                    "States": {
                        "StartNat": {
                            "Type": "Choice",
                            "Choices": [
                                {
                                    "And": [
                                        {
                                            "Variable": "$.useNat",
                                            "IsPresent": True,
                                        },
                                        {
                                            "Variable": "$.useNat",
                                            "BooleanEquals": True,
                                        },
                                    ],
                                    "Next": "StartNatInstance",
                                }
                            ],
                            "Default": "ScaleUp",
                        },
                        "StartNatInstance": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::aws-sdk:ec2:startInstances",
                            "Parameters": {"InstanceIds": [args[4]]},
                            "Next": "WaitNatRunning",
                        },
                        "WaitNatRunning": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::aws-sdk:ec2:describeInstanceStatus",
                            "Parameters": {
                                "InstanceIds": [args[4]],
                                "IncludeAllInstances": True,
                            },
                            "ResultSelector": {
                                "state.$": "$.InstanceStatuses[0].InstanceState.Name"
                            },
                            "ResultPath": "$.nat",
                            "Next": "NatReady?",
                        },
                        "NatReady?": {
                            "Type": "Choice",
                            "Choices": [
                                {
                                    "Variable": "$.nat.state",
                                    "StringEquals": "running",
                                    "Next": "ScaleUp",
                                }
                            ],
                            "Default": "WaitNatDelay",
                        },
                        "WaitNatDelay": {
                            "Type": "Wait",
                            "Seconds": 5,
                            "Next": "WaitNatRunning",
                        },
                        "ScaleUp": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::aws-sdk:ecs:updateService",
                            "Parameters": {
                                "Cluster": args[0],
                                "Service": args[1],
                                "DesiredCount": 1,
                            },
                            "Next": "AwaitTasks",
                        },
                        "AwaitTasks": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::aws-sdk:ecs:describeServices",
                            "Parameters": {
                                "Cluster": args[0],
                                "Services": [args[1]],
                            },
                            "ResultSelector": {
                                "runningCount.$": "$.Services[0].RunningCount",
                                "desiredCount.$": "$.Services[0].DesiredCount",
                            },
                            "ResultPath": "$.svc",
                            "Next": "TasksReady?",
                        },
                        "TasksReady?": {
                            "Type": "Choice",
                            "Choices": [
                                {
                                    "Variable": "$.svc.runningCount",
                                    "NumericGreaterThanEquals": 1,
                                    "Next": "WaitForHealthy",
                                }
                            ],
                            "Default": "WaitDelay",
                        },
                        "WaitDelay": {
                            "Type": "Wait",
                            "Seconds": 5,
                            "Next": "AwaitTasks",
                        },
                        "WaitForHealthy": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::lambda:invoke",
                            "Parameters": {
                                "FunctionName": args[2],
                                "Payload": {},
                            },
                            "ResultPath": "$.wait",
                            "Next": "RunWorker",
                        },
                        "RunWorker": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::lambda:invoke",
                            "Parameters": {
                                "FunctionName": args[3],
                                "Payload.$": "$",
                            },
                            "ResultPath": "$.worker",
                            "Next": "MaybeEgress",
                        },
                        "MaybeEgress": {
                            "Type": "Choice",
                            "Choices": [
                                {
                                    "And": [
                                        {
                                            "Variable": "$.egressEnabled",
                                            "IsPresent": True,
                                        },
                                        {
                                            "Variable": "$.egressEnabled",
                                            "BooleanEquals": True,
                                        },
                                    ],
                                    "Next": "CallEgress",
                                }
                            ],
                            "Default": "ScaleDown",
                        },
                        "CallEgress": (
                            {
                                "Type": "Task",
                                "Resource": "arn:aws:states:::lambda:invoke",
                                "Parameters": {
                                    "FunctionName": args[5],
                                    "Payload.$": "$",
                                },
                                "ResultPath": "$.egress",
                                "Next": "ScaleDown",
                            }
                            if args[5]
                            else {
                                "Type": "Pass",
                                "Result": {"skipped": True},
                                "ResultPath": "$.egress",
                                "Next": "ScaleDown",
                            }
                        ),
                        "ScaleDown": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::aws-sdk:ecs:updateService",
                            "Parameters": {
                                "Cluster": args[0],
                                "Service": args[1],
                                "DesiredCount": 0,
                            },
                            "Next": "MaybeStopNat",
                        },
                        "MaybeStopNat": {
                            "Type": "Choice",
                            "Choices": [
                                {
                                    "And": [
                                        {
                                            "Variable": "$.useNat",
                                            "IsPresent": True,
                                        },
                                        {
                                            "Variable": "$.useNat",
                                            "BooleanEquals": True,
                                        },
                                    ],
                                    "Next": "StopNatInstance",
                                }
                            ],
                            "Default": "Done",
                        },
                        "StopNatInstance": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::aws-sdk:ec2:stopInstances",
                            "Parameters": {"InstanceIds": [args[4]]},
                            "Next": "WaitNatStopped",
                        },
                        "WaitNatStopped": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::aws-sdk:ec2:describeInstanceStatus",
                            "Parameters": {
                                "InstanceIds": [args[4]],
                                "IncludeAllInstances": True,
                            },
                            "ResultSelector": {
                                "state.$": "$.InstanceStatuses[0].InstanceState.Name"
                            },
                            "ResultPath": "$.natStop",
                            "Next": "NatStopped?",
                        },
                        "NatStopped?": {
                            "Type": "Choice",
                            "Choices": [
                                {
                                    "Variable": "$.natStop.state",
                                    "StringEquals": "stopped",
                                    "Next": "Done",
                                }
                            ],
                            "Default": "WaitNatStopDelay",
                        },
                        "WaitNatStopDelay": {
                            "Type": "Wait",
                            "Seconds": 5,
                            "Next": "WaitNatStopped",
                        },
                        "Done": {"Type": "Succeed"},
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
