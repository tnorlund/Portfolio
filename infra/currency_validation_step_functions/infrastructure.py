import json
import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import AssetArchive, FileArchive, ResourceOptions

from infra.dynamo_db import dynamodb_table
from infra.lambda_layer import dynamo_layer, label_layer
from infra.notifications import NotificationSystem


# Handlers directory collocated with this file
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
HANDLER_DIR = os.path.join(CURRENT_DIR, "handlers")


# IAM role for both lambdas
lambda_role = aws.iam.Role(
    "currency_validation_lambda_role",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "sts:AssumeRole",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Effect": "Allow",
                }
            ],
        }
    ),
)

# Basic execution policy
aws.iam.RolePolicyAttachment(
    "currency_validation_lambda_basic_execution",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)


# DynamoDB access policy (Query, Get, Put, Update, BatchWrite)
dynamodb_policy = aws.iam.Policy(
    "currency_validation_dynamodb_policy",
    description="IAM policy for currency validation to access DynamoDB",
    policy=dynamodb_table.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:DescribeTable",
                            "dynamodb:Query",
                            "dynamodb:GetItem",
                            "dynamodb:PutItem",
                            "dynamodb:UpdateItem",
                            "dynamodb:BatchWriteItem",
                            "dynamodb:Scan",
                        ],
                        "Resource": [
                            arn,
                            f"{arn}/index/GSITYPE",
                        ],
                    }
                ],
            }
        )
    ),
)

aws.iam.RolePolicyAttachment(
    "currency_validation_dynamodb_policy_attachment",
    role=lambda_role.name,
    policy_arn=dynamodb_policy.arn,
)


# List receipts Lambda
currency_validation_list_lambda = aws.lambda_.Function(
    "currency_validation_list_lambda",
    runtime="python3.12",
    architectures=["arm64"],
    role=lambda_role.arn,
    code=AssetArchive({".": FileArchive(HANDLER_DIR)}),
    handler="list_receipts.handler",
    layers=[dynamo_layer.arn],
    memory_size=512,
    timeout=120,
    environment={"variables": {"DYNAMODB_TABLE_NAME": dynamodb_table.name}},
)


# Process single receipt Lambda
currency_validation_process_lambda = aws.lambda_.Function(
    "currency_validation_process_lambda",
    runtime="python3.12",
    architectures=["arm64"],
    role=lambda_role.arn,
    code=AssetArchive({".": FileArchive(HANDLER_DIR)}),
    handler="process_receipt.handler",
    layers=[dynamo_layer.arn, label_layer.arn],
    memory_size=1536,
    timeout=600,
    environment={
        "variables": {
            "DYNAMODB_TABLE_NAME": dynamodb_table.name,
            # LangChain/Ollama keys are provided at execution via input, not env
        }
    },
)


def _build_state_machine_definition(
    list_arn: str, process_arn: str, sns_topic_arn: str, max_concurrency: int
) -> str:
    return json.dumps(
        {
            "Comment": "Currency validation fan-out over receipts",
            "StartAt": "ListReceipts",
            "TimeoutSeconds": 7200,
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
                            "Next": "NotifyListError",
                            "ResultPath": "$.error",
                        }
                    ],
                    "Next": "HasReceipts?",
                },
                "HasReceipts?": {
                    "Type": "Choice",
                    "Choices": [
                        {"Variable": "$.receipts[0]", "IsPresent": True, "Next": "ForEachReceipt"}
                    ],
                    "Default": "NoReceipts",
                },
                "NoReceipts": {
                    "Type": "Pass",
                    "Result": {"message": "No receipts to validate"},
                    "End": True,
                },
                "ForEachReceipt": {
                    "Type": "Map",
                    "ItemsPath": "$.receipts",
                    "MaxConcurrency": max_concurrency,
                    "Catch": [
                        {
                            "ErrorEquals": ["States.ALL"],
                            "Next": "NotifyMapError",
                            "ResultPath": "$.error",
                        }
                    ],
                    "Iterator": {
                        "StartAt": "ValidateReceipt",
                        "States": {
                            "ValidateReceipt": {
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
                    "Result": {"message": "Currency validation complete"},
                    "End": True,
                },
                "NotifyListError": {
                    "Type": "Task",
                    "Resource": "arn:aws:states:::sns:publish",
                    "Parameters": {
                        "TopicArn": sns_topic_arn,
                        "Subject": "Currency Validation: List Receipts Failed",
                        "Message.$": "States.Format('Error listing receipts: {}', $.error)",
                    },
                    "Next": "ListFailed",
                },
                "ListFailed": {
                    "Type": "Fail",
                    "Error": "ListReceiptsError",
                    "Cause": "Failed to list receipts",
                },
                "NotifyMapError": {
                    "Type": "Task",
                    "Resource": "arn:aws:states:::sns:publish",
                    "Parameters": {
                        "TopicArn": sns_topic_arn,
                        "Subject": "Currency Validation: Map Failed",
                        "Message.$": "States.Format('Map failure: {}', $.error)",
                    },
                    "Next": "MapFailed",
                },
                "MapFailed": {
                    "Type": "Fail",
                    "Error": "MapProcessingError",
                    "Cause": "Failure in map state",
                },
            },
        }
    )


def create_currency_validation_state_machine(
    notification_system: NotificationSystem,
    *,
    max_concurrency: int = 10,
) -> aws.sfn.StateMachine:
    """Provision the fan-out Step Functions workflow and monitoring."""

    # IAM role for Step Functions
    step_role = aws.iam.Role(
        "currency_validation_sfn_role",
        assume_role_policy=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Action": "sts:AssumeRole",
                        "Principal": {"Service": "states.amazonaws.com"},
                        "Effect": "Allow",
                    }
                ],
            }
        ),
    )

    # Policy allowing SFN to invoke lambdas and publish to SNS
    policy = aws.iam.Policy(
        "currency_validation_sfn_policy",
        policy=pulumi.Output.all(
            currency_validation_list_lambda.arn,
            currency_validation_process_lambda.arn,
            notification_system.step_function_topic_arn,
        ).apply(
            lambda args: json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": ["lambda:InvokeFunction"],
                            "Resource": [args[0], args[1]],
                        },
                        {
                            "Effect": "Allow",
                            "Action": ["sns:Publish"],
                            "Resource": args[2],
                        },
                        {
                            "Effect": "Allow",
                            "Action": [
                                "logs:CreateLogGroup",
                                "logs:CreateLogStream",
                                "logs:PutLogEvents",
                            ],
                            "Resource": "*",
                        },
                    ],
                }
            )
        ),
    )

    aws.iam.RolePolicyAttachment(
        "currency_validation_sfn_policy_attach",
        role=step_role.name,
        policy_arn=policy.arn,
    )

    # Definition
    definition = pulumi.Output.all(
        currency_validation_list_lambda.arn,
        currency_validation_process_lambda.arn,
        notification_system.step_function_topic_arn,
    ).apply(
        lambda args: _build_state_machine_definition(
            args[0], args[1], args[2], max_concurrency
        )
    )

    state_machine = aws.sfn.StateMachine(
        "currency_validation_state_machine",
        role_arn=step_role.arn,
        definition=definition,
        tags={
            "Component": "CurrencyValidation",
            "FanOut": "true",
            "ManagedBy": "Pulumi",
        },
    )

    # CloudWatch alarm
    notification_system.create_step_function_alarm(
        "currency-validation",
        state_machine.arn,
        failure_threshold=1,
    )

    return state_machine


