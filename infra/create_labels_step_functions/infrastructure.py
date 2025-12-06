"""Pulumi infrastructure for creating/updating labels with PENDING validation status."""

import json
import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from dynamo_db import dynamodb_table  # pylint: disable=import-error
from infra.components.lambda_layer import dynamo_layer  # pylint: disable=import-error
from pulumi import (
    AssetArchive,
    ComponentResource,
    Config,
    FileAsset,
    Output,
    ResourceOptions,
)
from pulumi_aws.cloudwatch import LogGroup
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment
from pulumi_aws.lambda_ import Function, FunctionEnvironmentArgs
from pulumi_aws.sfn import StateMachine, StateMachineLoggingConfigurationArgs

# Import the CodeBuildDockerImage component
from infra.components.codebuild_docker_image import CodeBuildDockerImage

config = Config("portfolio")
ollama_api_key = config.require_secret("OLLAMA_API_KEY")
langchain_api_key = config.require_secret("LANGCHAIN_API_KEY")


class CreateLabelsStepFunction(ComponentResource):
    """
    Step Function infrastructure for creating/updating labels with PENDING validation status.

    Uses LangGraph + Ollama to analyze receipts and create/update labels.

    Infrastructure Components:
    - 2 Lambda Functions:
        * list_receipts: Lists all receipts from DynamoDB (zip-based)
        * create_labels: Creates/updates labels for a single receipt (container-based)
    - Step Function state machine for orchestration
    - IAM roles and policies for service permissions
    - DynamoDB access for receipt and label storage
    """

    def __init__(
        self,
        name: str,
        *,
        dynamodb_table_name: pulumi.Input[str],
        dynamodb_table_arn: pulumi.Input[str],
        max_concurrency: int = 3,  # Reduced from 10 to avoid Ollama rate limiting
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(f"{__name__}-{name}", name, None, opts)
        stack = pulumi.get_stack()

        # Define IAM role for Step Function
        sfn_role = Role(
            f"{name}-sfn-role",
            name=f"{name}-sfn-role",
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

        # Lambda execution role
        lambda_exec_role = Role(
            f"{name}-lambda-role",
            name=f"{name}-lambda-role",
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

        # Basic Lambda execution policy
        RolePolicyAttachment(
            f"{name}-lambda-basic-exec",
            role=lambda_exec_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/"
                "AWSLambdaBasicExecutionRole"
            ),
        )

        # ECR permissions for container Lambda
        RolePolicy(
            f"{name}-lambda-ecr-policy",
            role=lambda_exec_role.id,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "ecr:GetAuthorizationToken",
                                "ecr:BatchGetImage",
                                "ecr:GetDownloadUrlForLayer",
                            ],
                            "Resource": "*",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=lambda_exec_role),
        )

        # DynamoDB access policy
        RolePolicy(
            f"{name}-lambda-dynamo-policy",
            role=lambda_exec_role.id,
            policy=Output.all(dynamodb_table_arn).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:DescribeTable",  # Required for DynamoClient initialization
                                    "dynamodb:GetItem",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:Query",
                                    "dynamodb:Scan",
                                    "dynamodb:BatchGetItem",
                                    "dynamodb:BatchWriteItem",
                                ],
                                "Resource": [
                                    args[0],
                                    f"{args[0]}/index/*",
                                ],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=lambda_exec_role),
        )

        # Step Function role policy (invoke Lambdas, CloudWatch Logs)
        RolePolicy(
            f"{name}-sfn-policy",
            role=sfn_role.id,
            policy=Output.all(lambda_exec_role.arn).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["lambda:InvokeFunction"],
                                "Resource": f"{args[0]}:*",  # All Lambdas with this role prefix
                            },
                            {
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
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=sfn_role),
        )

        # Handlers directory
        CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
        HANDLER_DIR = os.path.join(CURRENT_DIR, "handlers")

        # Create zip-based list_receipts Lambda
        list_receipts_lambda = Function(
            f"{name}-list-receipts",
            name=f"{name}-list-receipts",
            role=lambda_exec_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="list_receipts.handler",
            code=AssetArchive(
                {
                    "list_receipts.py": FileAsset(
                        os.path.join(HANDLER_DIR, "list_receipts.py")
                    ),
                }
            ),
            timeout=120,
            memory_size=512,
            layers=[dynamo_layer.arn],
            tags={"environment": stack},
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                }
            ),
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["layers"],
            ),
        )

        # Create container-based create_labels Lambda
        create_labels_lambda_config = {
            "role_arn": lambda_exec_role.arn,
            "timeout": 600,  # 10 minutes
            "memory_size": 1536,  # 1.5 GB
            "tags": {"environment": stack},
            "environment": {
                "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                "OLLAMA_API_KEY": ollama_api_key,
                "LANGCHAIN_API_KEY": langchain_api_key,
                "LANGCHAIN_TRACING_V2": "true",
                "LANGCHAIN_ENDPOINT": "https://api.smith.langchain.com",
                "LANGCHAIN_PROJECT": "create-labels",
            },
        }

        # Create container Lambda using CodeBuildDockerImage
        # Use shorter name to avoid AWS limits (64 chars for IAM roles, 63 for S3 buckets)
        create_labels_docker_image = CodeBuildDockerImage(
            f"{name}-create-labels-img",
            dockerfile_path="infra/create_labels_step_functions/lambdas/Dockerfile",
            build_context_path=".",  # Project root
            source_paths=None,  # Use default rsync with exclusions
            lambda_function_name=f"{name}-create-labels",
            lambda_config=create_labels_lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_exec_role]),
        )

        create_labels_lambda = create_labels_docker_image.lambda_function

        # Update Step Function role policy with actual Lambda ARNs
        RolePolicy(
            f"{name}-sfn-lambda-policy",
            role=sfn_role.id,
            policy=Output.all(
                list_receipts_lambda.arn, create_labels_lambda.arn
            ).apply(
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
            opts=ResourceOptions(parent=sfn_role),
        )

        # Create CloudWatch Log Group for Step Function execution logs
        log_group = LogGroup(
            f"{name}-sf-logs",
            name=f"/aws/stepfunctions/{name}-sf",
            retention_in_days=14,  # Retain logs for 14 days to control costs
            opts=ResourceOptions(parent=self),
        )

        # Create Step Function state machine with logging enabled
        logging_config = log_group.arn.apply(
            lambda arn: StateMachineLoggingConfigurationArgs(
                level="ALL",  # Log all events
                include_execution_data=True,  # Include input/output data
                log_destination=f"{arn}:*",  # Log to CloudWatch Logs
            )
        )

        self.state_machine = StateMachine(
            f"{name}-sf",
            name=f"{name}-sf",
            role_arn=sfn_role.arn,
            type="STANDARD",  # Standard workflow for long-running executions
            tags={"environment": stack},
            definition=Output.all(
                list_receipts_lambda.arn, create_labels_lambda.arn
            ).apply(
                lambda arns: self._create_step_function_definition(
                    arns[0], arns[1], max_concurrency
                )
            ),
            logging_configuration=logging_config,
            opts=ResourceOptions(parent=self, depends_on=[log_group]),
        )

        # Store outputs as attributes for easy access
        self.state_machine_arn = self.state_machine.arn
        self.list_receipts_lambda_arn = list_receipts_lambda.arn
        self.create_labels_lambda_arn = create_labels_lambda.arn

        # Register outputs
        self.register_outputs(
            {
                "state_machine_arn": self.state_machine.arn,
                "list_receipts_lambda_arn": list_receipts_lambda.arn,
                "create_labels_lambda_arn": create_labels_lambda.arn,
            }
        )

    def _create_step_function_definition(
        self, list_arn: str, process_arn: str, max_concurrency: int
    ) -> str:
        """Create Step Function definition."""
        definition = {
            "Comment": "Create/update labels with PENDING validation status for all receipts",
            "StartAt": "ListReceipts",
            # No global timeout - STANDARD type can run up to 1 year
            # Individual task timeouts are set per state (600s for CreateLabels task)
            "States": {
                "ListReceipts": {
                    "Type": "Task",
                    "Resource": list_arn,
                    "TimeoutSeconds": 300,
                    "Retry": [
                        {
                            "ErrorEquals": [
                                "States.TaskFailed",
                                "States.Timeout",
                                "Lambda.ServiceException",
                                "Lambda.AWSLambdaException",
                            ],
                            "IntervalSeconds": 2,
                            "MaxAttempts": 3,
                            "BackoffRate": 2.0,
                        }
                    ],
                    "Catch": [
                        {
                            "ErrorEquals": ["States.ALL"],
                            "Next": "ListFailed",
                            "ResultPath": "$.error",
                        }
                    ],
                    "Next": "HasReceipts?",
                },
                "HasReceipts?": {
                    "Type": "Choice",
                    "Choices": [
                        {
                            "Variable": "$.receipts[0]",
                            "IsPresent": True,
                            "Next": "ForEachReceipt",
                        }
                    ],
                    "Default": "NoReceipts",
                },
                "NoReceipts": {
                    "Type": "Pass",
                    "Result": {"message": "No receipts to process"},
                    "End": True,
                },
                "ForEachReceipt": {
                    "Type": "Map",
                    "ItemsPath": "$.receipts",
                    "MaxConcurrency": max_concurrency,
                    "Catch": [
                        {
                            "ErrorEquals": ["States.ALL"],
                            "Next": "MapFailed",
                            "ResultPath": "$.error",
                        }
                    ],
                    "Iterator": {
                        "StartAt": "CreateLabels",
                        "States": {
                            "CreateLabels": {
                                "Type": "Task",
                                "Resource": process_arn,
                                "TimeoutSeconds": 600,
                                "Retry": [
                                    {
                                        "ErrorEquals": [
                                            "States.TaskFailed",
                                            "Lambda.ServiceException",
                                            "Lambda.AWSLambdaException",
                                            "States.Timeout",
                                        ],
                                        "IntervalSeconds": 2,
                                        "MaxAttempts": 3,
                                        "BackoffRate": 2.0,
                                    }
                                ],
                                "End": True,
                            }
                        },
                    },
                    "ResultPath": "$.results",
                    "Next": "Done",
                },
                "Done": {
                    "Type": "Pass",
                    "Result": {"message": "Label creation complete"},
                    "End": True,
                },
                "ListFailed": {
                    "Type": "Fail",
                    "Error": "ListReceiptsError",
                    "Cause": "Failed to list receipts",
                },
                "MapFailed": {
                    "Type": "Fail",
                    "Error": "MapProcessingError",
                    "Cause": "Failure in map state",
                },
            },
        }

        return json.dumps(definition)
