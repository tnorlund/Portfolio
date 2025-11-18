"""Pulumi infrastructure for batch validating labels using CoVe templates."""

import json
import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from dynamo_db import dynamodb_table  # pylint: disable=import-error
from lambda_layer import dynamo_layer  # pylint: disable=import-error
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
from codebuild_docker_image import CodeBuildDockerImage

config = Config("portfolio")
ollama_api_key = config.require_secret("OLLAMA_API_KEY")
langchain_api_key = config.require_secret("LANGCHAIN_API_KEY")


class BatchValidateLabelsCoVeStepFunction(ComponentResource):
    """
    Step Function infrastructure for batch validating labels using CoVe templates.

    Uses CoVe verification records from DynamoDB to validate PENDING/NEEDS_REVIEW labels
    by reusing verification questions from successful CoVe runs.

    Infrastructure Components:
    - 2 Lambda Functions:
        * list_receipts_with_pending_labels: Lists receipts with PENDING/NEEDS_REVIEW labels (zip-based)
        * validate_receipt_cove_templates: Validates labels using CoVe templates (container-based)
    - Step Function state machine for orchestration
    - IAM roles and policies for service permissions
    - DynamoDB access for label and CoVe verification record storage
    """

    def __init__(
        self,
        name: str,
        *,
        dynamodb_table_name: pulumi.Input[str],
        dynamodb_table_arn: pulumi.Input[str],
        max_concurrency: int = 2,  # Reduced to 2 to avoid Ollama rate limiting
        # Note: Each receipt may have multiple labels, each requiring an LLM call.
        # With max_concurrency=2, we limit concurrent receipts to avoid rate limits.
        # Labels within a receipt are processed sequentially with retry logic.
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(f"{__name__}-{name}", name, None, opts)
        stack = pulumi.get_stack()

        # Store max_concurrency for use in Step Function definition
        self.max_concurrency = max_concurrency

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
                                "ecr:BatchCheckLayerAvailability",
                                "ecr:GetDownloadUrlForLayer",
                                "ecr:BatchGetImage",
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
            f"{name}-lambda-dynamodb-policy",
            role=lambda_exec_role.id,
            policy=Output.all(dynamodb_table_arn).apply(
                lambda arns: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:GetItem",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:DeleteItem",
                                    "dynamodb:Query",
                                    "dynamodb:Scan",
                                    "dynamodb:BatchGetItem",
                                    "dynamodb:BatchWriteItem",
                                ],
                                "Resource": [
                                    arns[0],
                                    f"{arns[0]}/index/*",
                                ],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=lambda_exec_role),
        )

        # List receipts Lambda (zip-based, lightweight)
        list_receipts_code = AssetArchive(
            {
                "handler.py": FileAsset(
                    os.path.join(
                        os.path.dirname(__file__),
                        "handlers",
                        "list_receipts_with_pending_labels.py",
                    )
                ),
            }
        )

        list_receipts_lambda = Function(
            f"{name}-list-receipts",
            name=f"{name}-list-receipts",
            runtime="python3.12",
            handler="handler.handler",
            role=lambda_exec_role.arn,
            code=list_receipts_code,
            timeout=300,
            memory_size=256,
            layers=[dynamo_layer.arn],
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                }
            ),
            opts=ResourceOptions(parent=self, depends_on=[lambda_exec_role]),
        )

        # Validate receipt Lambda (container-based, needs LangGraph/Ollama)
        validate_receipt_lambda_config = {
            "role_arn": lambda_exec_role.arn,
            "timeout": 600,  # 10 minutes per receipt
            "memory_size": 1536,
            "tags": {"environment": stack},
            "environment": {
                "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                "OLLAMA_API_KEY": ollama_api_key,
                "LANGCHAIN_API_KEY": langchain_api_key,
                "LANGCHAIN_TRACING_V2": "true",
                "LANGCHAIN_ENDPOINT": "https://api.smith.langchain.com",
                "LANGCHAIN_PROJECT": "batch-validate-labels-cove",
            },
        }

        # Create container Lambda using CodeBuildDockerImage
        # Use shorter name to avoid AWS limits (64 chars for IAM roles, 63 for S3 buckets)
        # Shortened component name: "batch-val-labels-cove-dev-val-receipt-img" = 50 chars
        # CodeBuild role will be: "{component-name}-cb-role-{7chars}" = 50 + 11 = 61 chars (under 64 limit)
        validate_receipt_docker_image = CodeBuildDockerImage(
            f"batch-val-labels-cove-{stack}-val-receipt-img",  # Shortened to fit IAM role name limit
            dockerfile_path="infra/batch_validate_labels_cove/lambdas/Dockerfile",
            build_context_path=".",  # Project root
            source_paths=None,  # Use default rsync with exclusions
            lambda_function_name=f"{name}-validate-receipt",
            lambda_config=validate_receipt_lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_exec_role]),
        )

        validate_receipt_lambda = validate_receipt_docker_image.lambda_function

        # Update Step Function role policy with actual Lambda ARNs
        RolePolicy(
            f"{name}-sfn-lambda-policy",
            role=sfn_role.id,
            policy=Output.all(
                list_receipts_lambda.arn, validate_receipt_lambda.arn
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
            retention_in_days=14,
            opts=ResourceOptions(parent=self),
        )

        # Create Step Function state machine with logging enabled
        logging_config = log_group.arn.apply(
            lambda arn: StateMachineLoggingConfigurationArgs(
                level="ALL",
                include_execution_data=True,
                log_destination=f"{arn}:*",
            )
        )

        # Create Step Function state machine with logging enabled
        # Use .apply() to handle Output[T] for log_destination
        self.state_machine = StateMachine(
            f"{name}-sf",
            name=f"{name}-sf",
            role_arn=sfn_role.arn,
            type="STANDARD",  # Standard workflow for long-running executions (up to 1 year)
            tags={"environment": stack},
            definition=Output.all(
                list_receipts_lambda.arn, validate_receipt_lambda.arn
            ).apply(self._create_step_function_definition),
            logging_configuration=logging_config,
            opts=ResourceOptions(parent=self, depends_on=[log_group]),
        )

        # Store outputs as attributes
        self.state_machine_arn = self.state_machine.arn
        self.list_receipts_lambda_arn = list_receipts_lambda.arn
        self.validate_receipt_lambda_arn = validate_receipt_lambda.arn

        # Register outputs
        self.register_outputs(
            {
                "state_machine_arn": self.state_machine.arn,
                "list_receipts_lambda_arn": list_receipts_lambda.arn,
                "validate_receipt_lambda_arn": validate_receipt_lambda.arn,
            }
        )

    def _create_step_function_definition(self, arns: list) -> str:
        """Create Step Function definition."""
        list_lambda_arn = arns[0]
        validate_lambda_arn = arns[1]

        definition = {
            "Comment": "Batch validate PENDING/NEEDS_REVIEW labels using CoVe templates",
            "StartAt": "ListReceipts",
            "States": {
                "ListReceipts": {
                    "Type": "Task",
                    "Resource": list_lambda_arn,
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
                    "Result": {"message": "No receipts with PENDING/NEEDS_REVIEW labels"},
                    "End": True,
                },
                "ForEachReceipt": {
                    "Type": "Map",
                    "ItemsPath": "$.receipts",
                    "MaxConcurrency": self.max_concurrency,
                    "Catch": [
                        {
                            "ErrorEquals": ["States.ALL"],
                            "Next": "MapFailed",
                            "ResultPath": "$.error",
                        }
                    ],
                    "Iterator": {
                        "StartAt": "ValidateReceipt",
                        "States": {
                            "ValidateReceipt": {
                                "Type": "Task",
                                "Resource": validate_lambda_arn,
                                "TimeoutSeconds": 600,
                                "Retry": [
                                    {
                                        "ErrorEquals": [
                                            "States.TaskFailed",
                                            "Lambda.ServiceException",
                                            "Lambda.AWSLambdaException",
                                            "Lambda.SdkClientException",
                                            "States.Timeout",
                                        ],
                                        "IntervalSeconds": 5,  # Longer initial delay for rate limits
                                        "MaxAttempts": 5,  # More attempts for rate limit recovery
                                        "BackoffRate": 2.0,  # Exponential backoff: 5s, 10s, 20s, 40s, 80s
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
                    "Result": {"message": "Batch validation complete"},
                    "End": True,
                },
                "ListFailed": {
                    "Type": "Fail",
                    "Error": "ListReceiptsFailed",
                    "Cause": "Failed to list receipts with PENDING/NEEDS_REVIEW labels",
                },
                "MapFailed": {
                    "Type": "Fail",
                    "Error": "MapFailed",
                    "Cause": "One or more receipts failed validation",
                },
            },
        }
        return json.dumps(definition)

