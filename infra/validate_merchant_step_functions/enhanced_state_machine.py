"""Enhanced state machine definition for merchant validation with error handling."""

import json
from typing import Any, Dict

from pulumi import Output


def create_enhanced_state_machine_definition(
    list_receipts_arn: str,
    validate_receipt_arn: str,
    consolidate_metadata_arn: str,
    sns_topic_arn: str,
) -> str:
    """
    Create an enhanced state machine definition with comprehensive error handling.

    This adds:
    - Retry policies for all Lambda invocations
    - Catch blocks for error handling
    - SNS notifications for failures
    - Timeouts at state and execution level
    - Better error context preservation
    """
    return json.dumps(
        {
            "Comment": "Enhanced merchant validation with error handling and monitoring",
            "StartAt": "ListReceiptsForMerchantValidation",
            "TimeoutSeconds": 7200,  # 2 hour timeout for entire execution
            "States": {
                "ListReceiptsForMerchantValidation": {
                    "Type": "Task",
                    "Resource": list_receipts_arn,
                    "TimeoutSeconds": 300,  # 5 minute timeout
                    "Retry": [
                        {
                            "ErrorEquals": [
                                "States.TaskFailed",
                                "States.Timeout",
                            ],
                            "IntervalSeconds": 2,
                            "MaxAttempts": 3,
                            "BackoffRate": 2.0,
                        },
                        {
                            "ErrorEquals": [
                                "Lambda.ServiceException",
                                "Lambda.AWSLambdaException",
                            ],
                            "IntervalSeconds": 1,
                            "MaxAttempts": 2,
                            "BackoffRate": 2.0,
                        },
                    ],
                    "Catch": [
                        {
                            "ErrorEquals": ["States.ALL"],
                            "Next": "NotifyListError",
                            "ResultPath": "$.listError",
                        }
                    ],
                    "Next": "CheckIfReceiptsFound",
                    "ResultPath": "$.receipts",
                },
                "CheckIfReceiptsFound": {
                    "Type": "Choice",
                    "Choices": [
                        {
                            "Variable": "$.receipts[0]",
                            "IsPresent": True,
                            "Next": "ForEachReceipt",
                        }
                    ],
                    "Default": "NoReceiptsToValidate",
                },
                "NoReceiptsToValidate": {
                    "Type": "Pass",
                    "Result": {
                        "message": "No receipts found requiring merchant validation",
                        "count": 0,
                    },
                    "End": True,
                },
                "ForEachReceipt": {
                    "Type": "Map",
                    "ItemsPath": "$.receipts",
                    "MaxConcurrency": 10,  # Process up to 10 receipts in parallel
                    "Retry": [
                        {
                            "ErrorEquals": ["States.ALL"],
                            "IntervalSeconds": 5,
                            "MaxAttempts": 2,
                            "BackoffRate": 2.0,
                        }
                    ],
                    "Catch": [
                        {
                            "ErrorEquals": ["States.ALL"],
                            "Next": "NotifyMapError",
                            "ResultPath": "$.mapError",
                        }
                    ],
                    "Iterator": {
                        "StartAt": "ValidateReceiptWithContext",
                        "States": {
                            "ValidateReceiptWithContext": {
                                "Type": "Task",
                                "Resource": validate_receipt_arn,
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
                                        "MaxDelaySeconds": 30,
                                    },
                                    {
                                        "ErrorEquals": ["States.Timeout"],
                                        "IntervalSeconds": 1,
                                        "MaxAttempts": 1,
                                    },
                                ],
                                "Catch": [
                                    {
                                        "ErrorEquals": ["States.ALL"],
                                        "Next": "LogValidationError",
                                        "ResultPath": "$.validationError",
                                    }
                                ],
                                "End": True,
                            },
                            "LogValidationError": {
                                "Type": "Pass",
                                "Parameters": {
                                    "error.$": "$.validationError",
                                    "receiptId.$": "$.receipt_id",
                                    "timestamp.$": "$$.State.EnteredTime",
                                    "message": "Failed to validate receipt merchant data",
                                },
                                "End": True,
                            },
                        },
                    },
                    "Next": "ConsolidateNewMetadata",
                    "ResultPath": "$.validationResults",
                },
                "ConsolidateNewMetadata": {
                    "Type": "Task",
                    "Resource": consolidate_metadata_arn,
                    "InputPath": "$.validationResults",
                    "TimeoutSeconds": 600,  # 10 minutes for consolidation
                    "Retry": [
                        {
                            "ErrorEquals": ["States.TaskFailed"],
                            "IntervalSeconds": 5,
                            "MaxAttempts": 3,
                            "BackoffRate": 2.0,
                        }
                    ],
                    "Catch": [
                        {
                            "ErrorEquals": ["States.ALL"],
                            "Next": "NotifyConsolidationError",
                            "ResultPath": "$.consolidationError",
                        }
                    ],
                    "Next": "ValidationComplete",
                },
                "ValidationComplete": {
                    "Type": "Pass",
                    "Parameters": {
                        "message": "Merchant validation completed successfully",
                        "timestamp.$": "$$.State.EnteredTime",
                        "processedCount.$": "States.ArrayLength($.validationResults)",
                    },
                    "End": True,
                },
                # Error notification states
                "NotifyListError": {
                    "Type": "Task",
                    "Resource": "arn:aws:states:::sns:publish",
                    "Parameters": {
                        "TopicArn": sns_topic_arn,
                        "Subject": "Merchant Validation Error: List Receipts Failed",
                        "Message.$": "States.Format('Failed to list receipts for validation. Error: {}', $.listError)",
                    },
                    "Next": "ListReceiptsFailed",
                },
                "ListReceiptsFailed": {
                    "Type": "Fail",
                    "Error": "ListReceiptsError",
                    "Cause": "Failed to retrieve receipts from DynamoDB for merchant validation",
                },
                "NotifyMapError": {
                    "Type": "Task",
                    "Resource": "arn:aws:states:::sns:publish",
                    "Parameters": {
                        "TopicArn": sns_topic_arn,
                        "Subject": "Merchant Validation Error: Batch Processing Failed",
                        "Message.$": "States.Format('Failed to process receipt batch. Error: {}', $.mapError)",
                    },
                    "Next": "MapProcessingFailed",
                },
                "MapProcessingFailed": {
                    "Type": "Fail",
                    "Error": "BatchProcessingError",
                    "Cause": "Failed to process receipts in parallel during merchant validation",
                },
                "NotifyConsolidationError": {
                    "Type": "Task",
                    "Resource": "arn:aws:states:::sns:publish",
                    "Parameters": {
                        "TopicArn": sns_topic_arn,
                        "Subject": "Merchant Validation Error: Consolidation Failed",
                        "Message.$": "States.Format('Failed to consolidate merchant metadata. Error: {}', $.consolidationError)",
                    },
                    "Next": "ConsolidationFailed",
                },
                "ConsolidationFailed": {
                    "Type": "Fail",
                    "Error": "ConsolidationError",
                    "Cause": "Failed to consolidate validated merchant metadata",
                },
            },
        }
    )


def get_enhanced_iam_policy(sns_topic_arn: Output[str]) -> Output[str]:
    """
    Get enhanced IAM policy that includes SNS permissions for error notifications.
    """
    return sns_topic_arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["lambda:InvokeFunction"],
                        "Resource": "*",  # Should be restricted to specific Lambda ARNs in production
                    },
                    {
                        "Effect": "Allow",
                        "Action": ["sns:Publish"],
                        "Resource": arn,
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
                    {
                        "Effect": "Allow",
                        "Action": [
                            "xray:PutTraceSegments",
                            "xray:PutTelemetryRecords",
                        ],
                        "Resource": "*",
                    },
                ],
            }
        )
    )
