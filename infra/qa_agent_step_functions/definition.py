"""Pure ASL definition for the QA pipeline; no cloud access at import time."""

from typing import Any


def build_state_machine_definition(
    *,
    run_all_questions_arn: str,
    query_metadata_arn: str,
    build_cache_arn: str,
    batch_bucket: str,
) -> dict[str, Any]:
    """Build caches from the runner's own records without hosted trace export."""
    retry = [
        {
            "ErrorEquals": [
                "Lambda.ServiceException",
                "Lambda.AWSLambdaException",
                "Lambda.SdkClientException",
                "Lambda.TooManyRequestsException",
            ],
            "IntervalSeconds": 5,
            "BackoffRate": 2,
            "MaxAttempts": 2,
        }
    ]
    return {
        "StartAt": "RunAllQuestions",
        "States": {
            "RunAllQuestions": {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {
                    "FunctionName": run_all_questions_arn,
                    "Payload": {"execution_id.$": "$$.Execution.Name"},
                },
                "ResultSelector": {
                    "receipt_keys.$": "$.Payload.receipt_keys",
                    "total_questions.$": "$.Payload.total_questions",
                    "results_ndjson_key.$": "$.Payload.results_ndjson_key",
                    "langchain_project.$": "$.Payload.langchain_project",
                },
                "ResultPath": "$.questions_result",
                "TimeoutSeconds": 960,
                "Retry": [
                    *retry,
                    {
                        "ErrorEquals": ["States.TaskFailed", "States.Timeout"],
                        "IntervalSeconds": 10,
                        "MaxAttempts": 1,
                        "BackoffRate": 1,
                    },
                ],
                "Next": "QueryReceiptData",
            },
            "QueryReceiptData": {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {
                    "FunctionName": query_metadata_arn,
                    "Payload": {
                        "receipt_keys.$": "$.questions_result.receipt_keys",
                        "execution_id.$": "$$.Execution.Name",
                        "batch_bucket": batch_bucket,
                    },
                },
                "ResultSelector": {
                    "receipts_lookup_path.$": "$.Payload.receipts_lookup_path"
                },
                "ResultPath": "$.metadata",
                "TimeoutSeconds": 960,
                "Retry": retry,
                "Next": "BuildVizCache",
            },
            "BuildVizCache": {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {
                    "FunctionName": build_cache_arn,
                    "Payload": {
                        "execution_id.$": "$$.Execution.Name",
                        "results_ndjson_key.$": "$.questions_result.results_ndjson_key",
                        "total_questions.$": "$.questions_result.total_questions",
                        "langchain_project.$": "$.questions_result.langchain_project",
                        "receipts_lookup_path.$": "$.metadata.receipts_lookup_path",
                    },
                },
                "TimeoutSeconds": 150,
                "Retry": retry,
                "End": True,
            },
        },
    }
