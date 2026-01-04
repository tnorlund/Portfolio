"""Step Function infrastructure for batch LayoutLM inference cache generation.

This creates a Step Function that:
1. Lists random receipts with VALID labels
2. Batches them into groups of 10
3. Processes each batch in parallel via the inference Lambda
4. Runs weekly to refresh the cache pool
"""

import json
import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Input, Output, ResourceOptions, AssetArchive, FileArchive

# Import the DynamoDB table from dynamo_db module
from dynamo_db import dynamodb_table

# Import the Lambda Layer
from infra.components.lambda_layer import dynamo_layer

# Get stack configuration
stack = pulumi.get_stack()

# Handler directory
HANDLER_DIR = os.path.join(os.path.dirname(__file__), "lambdas")


class LayoutLMBatchCacheGenerator(ComponentResource):
    """Step Function for batch LayoutLM inference cache generation."""

    def __init__(
        self,
        name: str,
        *,
        inference_lambda_arn: Input[str],
        opts: Optional[ResourceOptions] = None,
    ):
        """Create the batch cache generator Step Function.

        Args:
            name: Resource name prefix
            inference_lambda_arn: ARN of the inference Lambda (batch_handler)
            opts: Pulumi resource options
        """
        super().__init__(
            f"custom:layoutlm-batch-cache:{name}",
            name,
            None,
            opts,
        )

        # Create IAM role for List Receipts Lambda
        self.list_receipts_role = aws.iam.Role(
            f"{name}-list-receipts-role",
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }),
            tags={
                "Name": f"{name}-list-receipts-role",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Basic Lambda execution policy
        aws.iam.RolePolicyAttachment(
            f"{name}-list-receipts-basic-execution",
            role=self.list_receipts_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self.list_receipts_role),
        )

        # DynamoDB read policy for list receipts
        aws.iam.RolePolicy(
            f"{name}-list-receipts-dynamodb-policy",
            role=self.list_receipts_role.id,
            policy=dynamodb_table.arn.apply(
                lambda arn: json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:Query",
                            "dynamodb:GetItem",
                            "dynamodb:DescribeTable",
                        ],
                        "Resource": [
                            arn,
                            f"{arn}/index/*",
                        ],
                    }],
                })
            ),
            opts=ResourceOptions(parent=self),
        )

        # Create List Receipts Lambda
        self.list_receipts_lambda = aws.lambda_.Function(
            f"{name}-list-receipts-lambda",
            runtime="python3.12",
            architectures=["arm64"],
            role=self.list_receipts_role.arn,
            code=AssetArchive({
                ".": FileArchive(HANDLER_DIR),
            }),
            handler="list_receipts.handler",
            layers=[dynamo_layer.arn] if dynamo_layer.arn else None,
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                }
            ),
            memory_size=256,
            timeout=60,
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        # CloudWatch log group for list receipts Lambda
        aws.cloudwatch.LogGroup(
            f"{name}-list-receipts-log-group",
            name=self.list_receipts_lambda.name.apply(
                lambda fn: f"/aws/lambda/{fn}"
            ),
            retention_in_days=30,
            opts=ResourceOptions(parent=self),
        )

        # Create IAM role for Step Function
        self.sfn_role = aws.iam.Role(
            f"{name}-sfn-role",
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "states.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }),
            tags={
                "Name": f"{name}-sfn-role",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Policy for Step Function to invoke Lambdas
        inference_lambda_arn_output = Output.from_input(inference_lambda_arn)

        aws.iam.RolePolicy(
            f"{name}-sfn-lambda-policy",
            role=self.sfn_role.id,
            policy=Output.all(
                self.list_receipts_lambda.arn,
                inference_lambda_arn_output
            ).apply(
                lambda arns: json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Effect": "Allow",
                        "Action": "lambda:InvokeFunction",
                        "Resource": [arns[0], arns[1]],
                    }],
                })
            ),
            opts=ResourceOptions(parent=self),
        )

        # CloudWatch Logs policy for Step Function
        aws.iam.RolePolicy(
            f"{name}-sfn-logs-policy",
            role=self.sfn_role.id,
            policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogDelivery",
                        "logs:GetLogDelivery",
                        "logs:UpdateLogDelivery",
                        "logs:DeleteLogDelivery",
                        "logs:ListLogDeliveries",
                        "logs:PutResourcePolicy",
                        "logs:DescribeResourcePolicies",
                        "logs:DescribeLogGroups",
                    ],
                    "Resource": "*",
                }],
            }),
            opts=ResourceOptions(parent=self),
        )

        # Create Step Function state machine
        self.state_machine = aws.sfn.StateMachine(
            f"{name}-state-machine",
            role_arn=self.sfn_role.arn,
            definition=Output.all(
                self.list_receipts_lambda.arn,
                inference_lambda_arn_output
            ).apply(
                lambda arns: json.dumps({
                    "Comment": "LayoutLM Batch Inference Cache Generator",
                    "StartAt": "ListReceipts",
                    "States": {
                        "ListReceipts": {
                            "Type": "Task",
                            "Resource": arns[0],
                            "Parameters": {
                                "target_count": 100,
                                "batch_size": 10
                            },
                            "ResultPath": "$.listResult",
                            "Next": "CheckReceipts"
                        },
                        "CheckReceipts": {
                            "Type": "Choice",
                            "Choices": [{
                                "Variable": "$.listResult.total_count",
                                "NumericEquals": 0,
                                "Next": "NoReceiptsFound"
                            }],
                            "Default": "ProcessBatches"
                        },
                        "NoReceiptsFound": {
                            "Type": "Succeed",
                            "Comment": "No receipts with VALID labels found"
                        },
                        "ProcessBatches": {
                            "Type": "Map",
                            "ItemsPath": "$.listResult.batches",
                            "MaxConcurrency": 5,
                            "ItemProcessor": {
                                "ProcessorConfig": {
                                    "Mode": "INLINE"
                                },
                                "StartAt": "ProcessBatch",
                                "States": {
                                    "ProcessBatch": {
                                        "Type": "Task",
                                        "Resource": arns[1],
                                        "Parameters": {
                                            "receipts.$": "$"
                                        },
                                        "End": True
                                    }
                                }
                            },
                            "ResultPath": "$.batchResults",
                            "Next": "Success"
                        },
                        "Success": {
                            "Type": "Succeed"
                        }
                    }
                })
            ),
            tags={
                "Name": f"{name}-state-machine",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Create IAM role for EventBridge to trigger Step Function
        self.eventbridge_role = aws.iam.Role(
            f"{name}-eventbridge-role",
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "events.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }),
            tags={
                "Name": f"{name}-eventbridge-role",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Policy for EventBridge to start Step Function
        aws.iam.RolePolicy(
            f"{name}-eventbridge-sfn-policy",
            role=self.eventbridge_role.id,
            policy=self.state_machine.arn.apply(
                lambda arn: json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Effect": "Allow",
                        "Action": "states:StartExecution",
                        "Resource": arn,
                    }],
                })
            ),
            opts=ResourceOptions(parent=self),
        )

        # Weekly EventBridge schedule (Sunday 3am UTC)
        self.schedule = aws.cloudwatch.EventRule(
            f"{name}-weekly-schedule",
            description="Trigger LayoutLM batch cache generation weekly",
            schedule_expression="cron(0 3 ? * SUN *)",
            tags={
                "Name": f"{name}-weekly-schedule",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # EventBridge target to start Step Function
        aws.cloudwatch.EventTarget(
            f"{name}-schedule-target",
            rule=self.schedule.name,
            arn=self.state_machine.arn,
            role_arn=self.eventbridge_role.arn,
            opts=ResourceOptions(parent=self),
        )

        # Export outputs
        self.register_outputs({
            "state_machine_arn": self.state_machine.arn,
            "state_machine_name": self.state_machine.name,
            "list_receipts_lambda_arn": self.list_receipts_lambda.arn,
            "schedule_arn": self.schedule.arn,
        })


def create_batch_cache_generator(
    inference_lambda_arn: Input[str],
    opts: Optional[ResourceOptions] = None,
) -> LayoutLMBatchCacheGenerator:
    """Factory function to create the batch cache generator."""
    return LayoutLMBatchCacheGenerator(
        f"layoutlm-batch-cache-{stack}",
        inference_lambda_arn=inference_lambda_arn,
        opts=opts,
    )
