"""Enhanced receipt processor step function with error handling and monitoring."""

import json

import pulumi
import pulumi_aws as aws
from lambda_functions.receipt_processor.infra import (list_receipts_lambda,
                                                      process_receipt_lambda)
from notifications import NotificationSystem


def create_enhanced_receipt_processor(
    notification_system: NotificationSystem,
) -> aws.sfn.StateMachine:
    """Create receipt processor step function with error handling."""

    # Create IAM role for Step Functions
    step_function_role = aws.iam.Role(
        "receipt_processor_enhanced_step_function_role",
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
        tags={"Component": "ReceiptProcessor", "Enhanced": "true"},
    )

    # Create policy to allow Step Functions to invoke Lambda and SNS
    step_function_policy = aws.iam.Policy(
        "enhanced_step_function_policy",
        description="IAM policy for enhanced Step Functions",
        policy=pulumi.Output.all(
            list_receipts_arn=list_receipts_lambda.arn,
            process_receipt_arn=process_receipt_lambda.arn,
            sns_topic_arn=notification_system.step_function_topic_arn,
        ).apply(
            lambda args: json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": ["lambda:InvokeFunction"],
                            "Resource": [
                                args["list_receipts_arn"],
                                args["process_receipt_arn"],
                            ],
                        },
                        {
                            "Effect": "Allow",
                            "Action": ["sns:Publish"],
                            "Resource": args["sns_topic_arn"],
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

    # Attach policy to role
    policy_attachment = aws.iam.RolePolicyAttachment(
        "enhanced_step_function_policy_attachment",
        role=step_function_role.name,
        policy_arn=step_function_policy.arn,
    )

    # CloudWatch log group removed - logging configuration not supported in current pulumi-aws version

    # Create the enhanced step function definition
    step_function_definition = pulumi.Output.all(
        list_receipts_arn=list_receipts_lambda.arn,
        process_receipt_arn=process_receipt_lambda.arn,
        sns_topic_arn=notification_system.step_function_topic_arn,
    ).apply(
        lambda args: json.dumps(
            {
                "Comment": "Enhanced receipt processor with error handling and monitoring",
                "StartAt": "ListReceipts",
                "TimeoutSeconds": 3600,  # 1 hour timeout
                "States": {
                    "ListReceipts": {
                        "Type": "Task",
                        "Resource": args["list_receipts_arn"],
                        "TimeoutSeconds": 300,  # 5 minutes
                        "Retry": [
                            {
                                "ErrorEquals": [
                                    "States.TaskFailed",
                                    "States.Timeout",
                                ],
                                "IntervalSeconds": 2,
                                "MaxAttempts": 3,
                                "BackoffRate": 2.0,
                            }
                        ],
                        "Catch": [
                            {
                                "ErrorEquals": ["States.ALL"],
                                "Next": "NotifyListReceiptsError",
                                "ResultPath": "$.error",
                            }
                        ],
                        "Next": "CheckReceiptCount",
                    },
                    "CheckReceiptCount": {
                        "Type": "Choice",
                        "Choices": [
                            {
                                "Variable": "$.receipts[0]",
                                "IsPresent": True,
                                "Next": "ProcessReceipts",
                            }
                        ],
                        "Default": "NoReceiptsToProcess",
                    },
                    "NoReceiptsToProcess": {
                        "Type": "Pass",
                        "Result": "No receipts to process",
                        "End": True,
                    },
                    "ProcessReceipts": {
                        "Type": "Map",
                        "InputPath": "$.receipts",
                        "ItemsPath": "$",
                        "MaxConcurrency": 10,
                        "Catch": [
                            {
                                "ErrorEquals": ["States.ALL"],
                                "Next": "NotifyMapError",
                                "ResultPath": "$.error",
                            }
                        ],
                        "Iterator": {
                            "StartAt": "ProcessReceipt",
                            "States": {
                                "ProcessReceipt": {
                                    "Type": "Task",
                                    "Resource": args["process_receipt_arn"],
                                    "TimeoutSeconds": 180,  # 3 minutes per receipt
                                    "Retry": [
                                        {
                                            "ErrorEquals": [
                                                "States.TaskFailed",
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
                                            "Next": "LogReceiptError",
                                        }
                                    ],
                                    "End": True,
                                },
                                "LogReceiptError": {
                                    "Type": "Pass",
                                    "Parameters": {
                                        "error.$": "$.error",
                                        "receipt.$": "$",
                                        "message": "Failed to process individual receipt",
                                    },
                                    "End": True,
                                },
                            },
                        },
                        "ResultPath": "$.processResults",
                        "Next": "ProcessingComplete",
                    },
                    "ProcessingComplete": {
                        "Type": "Pass",
                        "Parameters": {
                            "message": "Receipt processing completed",
                            "results.$": "$.processResults",
                            "timestamp.$": "$$.State.EnteredTime",
                        },
                        "End": True,
                    },
                    "NotifyListReceiptsError": {
                        "Type": "Task",
                        "Resource": "arn:aws:states:::sns:publish",
                        "Parameters": {
                            "TopicArn": args["sns_topic_arn"],
                            "Subject": "Receipt Processor Error: Failed to List Receipts",
                            "Message.$": "States.Format('Error listing receipts: {}', $.error)",
                        },
                        "Next": "ListReceiptsFailed",
                    },
                    "ListReceiptsFailed": {
                        "Type": "Fail",
                        "Error": "ListReceiptsError",
                        "Cause": "Failed to list receipts from database",
                    },
                    "NotifyMapError": {
                        "Type": "Task",
                        "Resource": "arn:aws:states:::sns:publish",
                        "Parameters": {
                            "TopicArn": args["sns_topic_arn"],
                            "Subject": "Receipt Processor Error: Map State Failed",
                            "Message.$": "States.Format('Error in receipt processing map: {}', $.error)",
                        },
                        "Next": "MapProcessingFailed",
                    },
                    "MapProcessingFailed": {
                        "Type": "Fail",
                        "Error": "MapProcessingError",
                        "Cause": "Failed to process receipts in parallel",
                    },
                },
            }
        )
    )

    # Create the enhanced state machine
    enhanced_step_function = aws.sfn.StateMachine(
        "receipt_processor_enhanced_step_function",
        role_arn=step_function_role.arn,
        definition=step_function_definition,
        tags={
            "Component": "ReceiptProcessor",
            "Enhanced": "true",
            "Monitoring": "enabled",
        },
    )

    # Create CloudWatch alarm for failures
    notification_system.create_step_function_alarm(
        "receipt-processor-enhanced",
        enhanced_step_function.arn,
        failure_threshold=1,
    )

    return enhanced_step_function
