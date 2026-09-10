"""Label cache orchestration using native traces already in S3."""

from typing import Any


def build_state_machine_definition(
    query_arn: str,
    emr_application_id: str,
    emr_role_arn: str,
    artifacts_bucket: str,
    trace_bucket: str,
    cache_bucket: str,
) -> dict[str, Any]:
    return {
        "Comment": "Build label validation cache from native S3 receipt traces",
        "StartAt": "QueryDynamoDB",
        "States": {
            "QueryDynamoDB": {
                "Type": "Task",
                "Resource": query_arn,
                "ResultPath": "$.dynamo_result",
                "Next": "StartEMRJob",
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
            },
            "StartEMRJob": {
                "Type": "Task",
                "Resource": "arn:aws:states:::emr-serverless:startJobRun.sync",
                "TimeoutSeconds": 1800,
                "Retry": [
                    {
                        "ErrorEquals": ["EMRServerless.ThrottlingException"],
                        "IntervalSeconds": 10,
                        "MaxAttempts": 2,
                        "BackoffRate": 2,
                    }
                ],
                "Parameters": {
                    "ApplicationId": emr_application_id,
                    "ExecutionRoleArn": emr_role_arn,
                    "Name.$": "States.Format('label-val-viz-{}', $$.Execution.Name)",
                    "JobDriver": {
                        "SparkSubmit": {
                            "EntryPoint": f"s3://{artifacts_bucket}/spark/label_validation_viz_cache_job.py",
                            "EntryPointArguments.$": (
                                "States.Array('--trace-format', 'native', "
                                f"'--parquet-bucket', '{trace_bucket}', "
                                "'--parquet-prefix', 'native-traces/', "
                                f"'--cache-bucket', '{cache_bucket}', "
                                "'--receipts-json', $.dynamo_result.receipts_s3_path)"
                            ),
                            "SparkSubmitParameters": (
                                "--conf spark.sql.adaptive.enabled=true "
                                "--conf spark.sql.shuffle.partitions=32 "
                                "--conf spark.executor.cores=2 --conf spark.executor.memory=4g "
                                "--conf spark.executor.instances=2 --conf spark.driver.cores=2 "
                                "--conf spark.driver.memory=4g"
                            ),
                        }
                    },
                    "ConfigurationOverrides": {
                        "MonitoringConfiguration": {
                            "S3MonitoringConfiguration": {
                                "LogUri": f"s3://{artifacts_bucket}/logs/"
                            }
                        }
                    },
                },
                "ResultPath": "$.emr_result",
                "End": True,
            },
        },
    }
