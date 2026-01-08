"""Pulumi resources for CoreML export queue infrastructure.

This component creates the SQS queues needed to export LayoutLM models to CoreML format.
Since coremltools only runs on macOS, we use a two-queue pattern:
1. Job queue: AWS sends export requests, macOS worker polls
2. Results queue: macOS worker sends results, Lambda processes
"""

import json
import os
from typing import cast

import pulumi
import pulumi_aws as aws
from pulumi import (
    AssetArchive,
    ComponentResource,
    FileAsset,
    Output,
    ResourceOptions,
)
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment
from pulumi_aws.lambda_ import Function, FunctionEnvironmentArgs
from pulumi_aws.sqs import Queue

stack = pulumi.get_stack()


class CoreMLExportComponent(ComponentResource):
    """Infrastructure for CoreML model export processing.

    Creates:
    - SQS job queue for export requests (AWS -> macOS)
    - SQS results queue for export results (macOS -> AWS)
    - Lambda to process results and update DynamoDB
    """

    def __init__(
        self,
        name: str,
        dynamodb_table_name: pulumi.Input[str],
        dynamodb_table_arn: pulumi.Input[str],
        layoutlm_bucket_name: pulumi.Input[str],
        layoutlm_bucket_arn: pulumi.Input[str],
        lambda_layer_arn: pulumi.Input[str],
        opts: ResourceOptions | None = None,
    ):
        """Initialize CoreML export infrastructure.

        Args:
            name: Resource name prefix
            dynamodb_table_name: Name of the DynamoDB table
            dynamodb_table_arn: ARN of the DynamoDB table
            layoutlm_bucket_name: Name of the S3 bucket for LayoutLM models
            layoutlm_bucket_arn: ARN of the S3 bucket for LayoutLM models
            lambda_layer_arn: ARN of the Lambda layer with receipt_dynamo
            opts: Pulumi resource options
        """
        super().__init__(
            f"{__name__}-{name}",
            "aws:lambda:CoreMLExport",
            {},
            opts,
        )

        # Job Queue - requests from AWS to macOS worker
        # Long visibility timeout since model export can take 30-60 minutes
        self.job_queue = Queue(
            f"{name}-coreml-export-job-queue",
            name=f"{name}-{stack}-coreml-export-job-queue",
            visibility_timeout_seconds=3600,  # 1 hour for large models
            message_retention_seconds=1209600,  # 14 days
            receive_wait_time_seconds=20,  # Long polling
            tags={
                "Purpose": "CoreML Export Job Queue",
                "Component": name,
                "Environment": stack,
            },
            opts=ResourceOptions(parent=self),
        )

        # Results Queue - responses from macOS worker
        # Shorter timeout since Lambda processing is quick
        self.results_queue = Queue(
            f"{name}-coreml-export-results-queue",
            name=f"{name}-{stack}-coreml-export-results-queue",
            visibility_timeout_seconds=900,  # 15 minutes
            message_retention_seconds=345600,  # 4 days
            receive_wait_time_seconds=0,  # Short polling for Lambda trigger
            tags={
                "Purpose": "CoreML Export Results Queue",
                "Component": name,
                "Environment": stack,
            },
            opts=ResourceOptions(parent=self),
        )

        # IAM Role for results processing Lambda
        process_results_role = Role(
            f"{name}-process-coreml-results-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Basic execution policy
        RolePolicyAttachment(
            f"{name}-process-coreml-results-basic-exec",
            role=process_results_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self),
        )

        # SQS execution policy for event source mapping
        RolePolicyAttachment(
            f"{name}-process-coreml-results-sqs-exec",
            role=process_results_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole",
            opts=ResourceOptions(parent=self),
        )

        # Inline policy for DynamoDB access
        RolePolicy(
            f"{name}-process-coreml-results-dynamo-policy",
            role=process_results_role.id,
            policy=Output.all(
                dynamodb_table_arn,
                self.results_queue.arn,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:DescribeTable",
                                    "dynamodb:GetItem",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:Query",
                                ],
                                "Resource": [
                                    args[0],
                                    f"{args[0]}/index/*",
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "sqs:ReceiveMessage",
                                    "sqs:DeleteMessage",
                                    "sqs:GetQueueAttributes",
                                ],
                                "Resource": args[1],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Results processing Lambda
        self.process_results_lambda = Function(
            f"{name}-process-coreml-results-lambda",
            name=f"{name}-{stack}-process-coreml-results",
            role=process_results_role.arn,
            runtime="python3.12",
            handler="process_results.handler",
            code=AssetArchive(
                {
                    "process_results.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__), "process_results.py"
                        )
                    )
                }
            ),
            architectures=["arm64"],
            layers=[lambda_layer_arn],
            timeout=60,
            memory_size=256,
            tags={"environment": stack},
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMO_TABLE_NAME": dynamodb_table_name,
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Event source mapping to trigger Lambda from results queue
        aws.lambda_.EventSourceMapping(
            f"{name}-coreml-results-mapping",
            event_source_arn=self.results_queue.arn,
            function_name=self.process_results_lambda.name,
            batch_size=10,
            enabled=True,
            opts=ResourceOptions(parent=self),
        )

        # Export queue URLs for configuration
        self.job_queue_url = self.job_queue.url
        self.results_queue_url = self.results_queue.url


# Export the component
__all__ = ["CoreMLExportComponent"]
