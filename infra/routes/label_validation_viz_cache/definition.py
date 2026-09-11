"""Label cache orchestration using native traces already in S3."""

from typing import Any


def build_state_machine_definition(cache_lambda_arn: str) -> dict[str, Any]:
    """A single retryable Lambda builds and publishes the receipt cache."""
    return {
        "Comment": "Build label validation cache from native S3 receipt traces",
        "StartAt": "BuildReceiptCache",
        "States": {
            "BuildReceiptCache": {
                "Type": "Task",
                "Resource": cache_lambda_arn,
                "TimeoutSeconds": 960,
                "Retry": [
                    {
                        "ErrorEquals": [
                            "Lambda.ServiceException",
                            "Lambda.AWSLambdaException",
                            "Lambda.SdkClientException",
                            "Lambda.TooManyRequestsException",
                        ],
                        "IntervalSeconds": 5,
                        "MaxAttempts": 2,
                        "BackoffRate": 2,
                    }
                ],
                "End": True,
            }
        },
    }
