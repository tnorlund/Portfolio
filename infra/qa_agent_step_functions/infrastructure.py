"""
Pulumi infrastructure for QA Agent Step Function pipeline.

This component creates a Step Function that:
1. Runs all 32 marquee questions through the 5-node QA graph (single container Lambda)
2. Queries DynamoDB for receipt metadata (zip Lambda)
3. Triggers LangSmith bulk export (reuses existing Lambda)
4. Polls export status with retry loop (reuses existing Lambda)
5. Starts EMR Spark job to build per-question viz cache

Architecture:
- Container Lambda: run_question (runs all 32 questions with asyncio concurrency)
- Zip Lambda: query_receipt_metadata (DynamoDB lookup)
- S3 Bucket: intermediate results (NDJSON, receipts-lookup.json)
- Step Function: orchestration with export polling + EMR
"""

import json
import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import (
    AssetArchive,
    ComponentResource,
    Config,
    FileAsset,
    Output,
    ResourceOptions,
)

# Import shared components
from codebuild_docker_image import CodeBuildDockerImage
from lambda_layer import dynamo_layer

# Load secrets
config = Config("portfolio")
openrouter_api_key = config.require_secret("OPENROUTER_API_KEY")
langchain_api_key = config.require_secret("LANGCHAIN_API_KEY")
openai_api_key = config.require_secret("OPENAI_API_KEY")
chroma_cloud_api_key = config.require_secret("CHROMA_CLOUD_API_KEY")
chroma_cloud_tenant = config.get("CHROMA_CLOUD_TENANT") or ""
chroma_cloud_database = config.get("CHROMA_CLOUD_DATABASE") or ""

HANDLERS_DIR = os.path.join(os.path.dirname(__file__), "handlers")


class QAAgentStepFunction(ComponentResource):
    """Step Function infrastructure for QA Agent marquee pipeline.

    Components:
    - Container Lambda: run_question (runs all 32 questions, asyncio concurrency=10)
    - Zip Lambda: query_receipt_metadata (DynamoDB → receipts-lookup.json)
    - Step Function: orchestration with LangSmith export polling + EMR

    Workflow:
    1. RunAllQuestions → run 32 questions, write NDJSON, extract receipt keys
    2. QueryReceiptMetadata → receipts-lookup.json to S3
    3. TriggerLangSmithExport → start bulk export
    4. WaitForExport → 60s wait
    5. CheckExportStatus → poll loop (max 30 retries)
    6. StartEMRJob → qa_viz_cache_job.py
    """

    def __init__(
        self,
        name: str,
        *,
        dynamodb_table_name: pulumi.Input[str],
        dynamodb_table_arn: pulumi.Input[str],
        # EMR Serverless
        emr_application_id: pulumi.Input[str],
        emr_job_execution_role_arn: pulumi.Input[str],
        langsmith_export_bucket: pulumi.Input[str],
        analytics_output_bucket: pulumi.Input[str],
        spark_artifacts_bucket: pulumi.Input[str],
        # LangSmith export lambdas (reuse from existing infrastructure)
        trigger_export_lambda_arn: pulumi.Input[str],
        check_export_lambda_arn: pulumi.Input[str],
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(f"{__name__}-{name}", name, None, opts)
        stack = pulumi.get_stack()
        region = aws.get_region().name
        account_id = aws.get_caller_identity().account_id

        # ============================================================
        # S3 Bucket for intermediate results
        # ============================================================
        self.batch_bucket = aws.s3.Bucket(
            f"{name}-batch-bucket",
            force_destroy=True,
            tags={
                "environment": stack,
                "purpose": "qa-agent-step-function-batches",
            },
            opts=ResourceOptions(parent=self),
        )

        aws.s3.BucketVersioning(
            f"{name}-batch-bucket-versioning",
            bucket=self.batch_bucket.id,
            versioning_configuration=aws.s3.BucketVersioningVersioningConfigurationArgs(
                status="Enabled"
            ),
            opts=ResourceOptions(parent=self.batch_bucket),
        )

        # ============================================================
        # IAM Roles
        # ============================================================

        # Lambda execution role (shared by all Lambdas)
        lambda_role = aws.iam.Role(
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

        # Basic Lambda execution
        aws.iam.RolePolicyAttachment(
            f"{name}-lambda-basic-exec",
            role=lambda_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/"
                "AWSLambdaBasicExecutionRole"
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # DynamoDB permissions
        dynamodb_policy = aws.iam.RolePolicy(
            f"{name}-lambda-dynamo-policy",
            role=lambda_role.id,
            policy=Output.from_input(dynamodb_table_arn).apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:DescribeTable",
                                    "dynamodb:GetItem",
                                    "dynamodb:Query",
                                    "dynamodb:BatchGetItem",
                                ],
                                "Resource": [arn, f"{arn}/index/*"],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # S3 permissions (batch bucket only — ChromaDB is via Chroma Cloud)
        aws.iam.RolePolicy(
            f"{name}-lambda-s3-policy",
            role=lambda_role.id,
            policy=self.batch_bucket.arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:PutObject",
                                    "s3:ListBucket",
                                ],
                                "Resource": [arn, f"{arn}/*"],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # Grant EMR job execution role access to the QA batch/cache bucket
        # so the Spark job can read NDJSON/receipts-lookup and write cache files.
        emr_role_name = Output.from_input(emr_job_execution_role_arn).apply(
            lambda arn: arn.split("/")[-1]
        )
        aws.iam.RolePolicy(
            f"{name}-emr-s3-policy",
            role=emr_role_name,
            policy=self.batch_bucket.arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:PutObject",
                                    "s3:ListBucket",
                                ],
                                "Resource": [arn, f"{arn}/*"],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Step Function role
        sfn_role = aws.iam.Role(
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

        # ============================================================
        # Zip Lambda: query_receipt_metadata (uses dynamo layer)
        # ============================================================
        self.query_metadata_lambda = aws.lambda_.Function(
            f"{name}-query-receipt-metadata",
            runtime="python3.12",
            architectures=["arm64"],
            role=lambda_role.arn,
            code=AssetArchive(
                {
                    "index.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "query_receipt_metadata.py")
                    )
                }
            ),
            handler="index.handler",
            layers=[dynamo_layer.arn],
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                    "BATCH_BUCKET": self.batch_bucket.id,
                }
            ),
            memory_size=1024,
            timeout=900,
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        aws.cloudwatch.LogGroup(
            f"{name}-query-metadata-logs",
            name=self.query_metadata_lambda.name.apply(
                lambda n: f"/aws/lambda/{n}"
            ),
            retention_in_days=30,
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Container Lambda: run_question (all 32 questions, asyncio)
        # ============================================================
        lambda_config = {
            "role_arn": lambda_role.arn,
            "memory_size": 3072,
            "timeout": 900,
            "ephemeral_storage": 10240,
            "tags": {"environment": stack},
            "environment": {
                "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                "OPENROUTER_API_KEY": openrouter_api_key,
                "OPENROUTER_MODEL": "x-ai/grok-4.1-fast",
                "LANGCHAIN_API_KEY": langchain_api_key,
                "LANGCHAIN_TRACING_V2": "true",
                "LANGCHAIN_PROJECT": "qa-agent-marquee",
                "RECEIPT_AGENT_OPENAI_API_KEY": openai_api_key,
                "CHROMA_CLOUD_API_KEY": chroma_cloud_api_key,
                "CHROMA_CLOUD_TENANT": chroma_cloud_tenant,
                "CHROMA_CLOUD_DATABASE": chroma_cloud_database,
                "BATCH_BUCKET": self.batch_bucket.id,
            },
        }

        run_question_image = CodeBuildDockerImage(
            f"{name}-run-question-img",
            dockerfile_path="infra/qa_agent_step_functions/lambdas/Dockerfile",
            build_context_path=".",
            source_paths=[
                "receipt_agent",
                "receipt_dynamo",
                "receipt_dynamo_stream",
                "receipt_chroma",
                "receipt_places",
            ],
            lambda_function_name=f"{name}-run-question",
            lambda_config=lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(
                parent=self, depends_on=[lambda_role, dynamodb_policy]
            ),
        )

        # ECR permissions for container Lambda (scope to this repository)
        aws.iam.RolePolicy(
            f"{name}-lambda-ecr-policy",
            role=lambda_role.id,
            policy=run_question_image.ecr_repo.arn.apply(
                lambda repo_arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "ecr:GetAuthorizationToken",
                                ],
                                "Resource": "*",
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "ecr:BatchGetImage",
                                    "ecr:GetDownloadUrlForLayer",
                                ],
                                "Resource": repo_arn,
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(
                parent=lambda_role, depends_on=[run_question_image]
            ),
        )

        self.run_question_lambda = run_question_image.lambda_function

        aws.cloudwatch.LogGroup(
            f"{name}-run-question-logs",
            name=self.run_question_lambda.name.apply(
                lambda n: f"/aws/lambda/{n}"
            ),
            retention_in_days=30,
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Step Function IAM Policy
        # ============================================================
        sfn_policy = aws.iam.RolePolicy(
            f"{name}-sfn-policy",
            role=sfn_role.id,
            policy=Output.all(
                self.run_question_lambda.arn,
                self.query_metadata_lambda.arn,
                Output.from_input(trigger_export_lambda_arn),
                Output.from_input(check_export_lambda_arn),
                Output.from_input(emr_application_id),
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": "lambda:InvokeFunction",
                                "Resource": list(args[:-1]),
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "emr-serverless:StartJobRun",
                                    "emr-serverless:GetJobRun",
                                    "emr-serverless:CancelJobRun",
                                ],
                                "Resource": (
                                    args[-1]
                                    if str(args[-1]).startswith("arn:")
                                    else f"arn:aws:emr-serverless:{region}:{account_id}:/applications/{args[-1]}/*"
                                ),
                            },
                            {
                                "Effect": "Allow",
                                "Action": "iam:PassRole",
                                "Resource": "*",
                                "Condition": {
                                    "StringEquals": {
                                        "iam:PassedToService": "emr-serverless.amazonaws.com"
                                    }
                                },
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
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "events:PutTargets",
                                    "events:PutRule",
                                    "events:DescribeRule",
                                    "events:DeleteRule",
                                    "events:RemoveTargets",
                                ],
                                "Resource": [
                                    f"arn:aws:events:{region}:{account_id}:rule/StepFunctions*",
                                ],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=sfn_role),
        )

        # ============================================================
        # Step Function Log Group
        # ============================================================
        sfn_log_group = aws.cloudwatch.LogGroup(
            f"{name}-sfn-logs",
            name=f"/aws/states/{name}",
            retention_in_days=30,
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # State Machine Definition
        # ============================================================
        step_function_definition = Output.all(
            self.run_question_lambda.arn,
            self.query_metadata_lambda.arn,
            Output.from_input(trigger_export_lambda_arn),
            Output.from_input(check_export_lambda_arn),
            Output.from_input(emr_application_id),
            Output.from_input(emr_job_execution_role_arn),
            Output.from_input(spark_artifacts_bucket),
            Output.from_input(langsmith_export_bucket),
            self.batch_bucket.id,
        ).apply(
            lambda args: json.dumps(
                _build_state_machine_definition(
                    run_all_questions_arn=args[0],
                    query_metadata_arn=args[1],
                    trigger_export_arn=args[2],
                    check_export_arn=args[3],
                    emr_application_id=args[4],
                    emr_job_role_arn=args[5],
                    spark_artifacts_bucket=args[6],
                    langsmith_export_bucket=args[7],
                    # Cache and batch are the same bucket
                    cache_bucket=args[8],
                    batch_bucket=args[8],
                )
            )
        )

        # ============================================================
        # State Machine
        # ============================================================
        self.state_machine = aws.sfn.StateMachine(
            f"{name}-state-machine",
            name=name,
            role_arn=sfn_role.arn,
            definition=step_function_definition,
            logging_configuration=aws.sfn.StateMachineLoggingConfigurationArgs(
                level="ERROR",
                include_execution_data=True,
                log_destination=sfn_log_group.arn.apply(lambda a: f"{a}:*"),
            ),
            tags={"environment": stack},
            opts=ResourceOptions(parent=self, depends_on=[sfn_policy]),
        )

        # ============================================================
        # Outputs
        # ============================================================
        self.state_machine_arn = self.state_machine.arn
        self.batch_bucket_name = self.batch_bucket.id

        self.register_outputs(
            {
                "state_machine_arn": self.state_machine.arn,
                "batch_bucket_name": self.batch_bucket.id,
            }
        )


def _build_state_machine_definition(
    *,
    run_all_questions_arn: str,
    query_metadata_arn: str,
    trigger_export_arn: str,
    check_export_arn: str,
    emr_application_id: str,
    emr_job_role_arn: str,
    spark_artifacts_bucket: str,
    langsmith_export_bucket: str,
    cache_bucket: str,
    batch_bucket: str,
) -> dict:
    """Build the Step Function ASL definition.

    Pipeline:
    Initialize → RunAllQuestions → QueryReceiptMetadata →
    TriggerLangSmithExport → WaitForExport ⟷ CheckExportStatus →
    PrepareEMRArgs → StartEMRJob → Done
    """
    return {
        "Comment": "QA Agent pipeline: run marquee questions, export traces, build viz cache",
        "StartAt": "Initialize",
        "States": {
            "Initialize": {
                "Type": "Pass",
                "Parameters": {
                    "execution_id.$": "$$.Execution.Name",
                    "batch_bucket": batch_bucket,
                    "retry_count": 0,
                },
                "ResultPath": "$.init",
                "Next": "RunAllQuestions",
            },
            "RunAllQuestions": {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {
                    "FunctionName": run_all_questions_arn,
                    "Payload.$": "States.JsonMerge($.init, $$.Execution.Input, false)",
                },
                "ResultSelector": {
                    "receipt_keys.$": "$.Payload.receipt_keys",
                    "total_questions.$": "$.Payload.total_questions",
                    "success_count.$": "$.Payload.success_count",
                    "results_ndjson_key.$": "$.Payload.results_ndjson_key",
                    "langchain_project.$": "$.Payload.langchain_project",
                },
                "ResultPath": "$.questions_result",
                "TimeoutSeconds": 900,
                "Retry": [
                    {
                        "ErrorEquals": [
                            "Lambda.ServiceException",
                            "Lambda.AWSLambdaException",
                            "Lambda.SdkClientException",
                        ],
                        "IntervalSeconds": 30,
                        "MaxAttempts": 1,
                        "BackoffRate": 1,
                    }
                ],
                "Next": "QueryReceiptMetadata",
            },
            "QueryReceiptMetadata": {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {
                    "FunctionName": query_metadata_arn,
                    "Payload": {
                        "receipt_keys.$": "$.questions_result.receipt_keys",
                        "execution_id.$": "$.init.execution_id",
                        "batch_bucket.$": "$.init.batch_bucket",
                    },
                },
                "ResultSelector": {
                    "receipts_lookup_path.$": "$.Payload.receipts_lookup_path",
                    "receipts_found.$": "$.Payload.receipts_found",
                },
                "ResultPath": "$.metadata",
                "Next": "WaitForTraceIngestion",
            },
            # LangSmith needs time to ingest traces after the Lambda
            # flushes them.  Without this delay the bulk export may
            # return 0 rows because the traces aren't queryable yet.
            # 5 minutes is conservative but reliable for 32-trace batches.
            "WaitForTraceIngestion": {
                "Type": "Wait",
                "Seconds": 300,
                "Next": "TriggerLangSmithExport",
            },
            "TriggerLangSmithExport": {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {
                    "FunctionName": trigger_export_arn,
                    "Payload": {
                        "project_name.$": "$.questions_result.langchain_project",
                        "export_fields": [
                            "id",
                            "name",
                            "inputs",
                            "outputs",
                            "extra",
                            "parent_run_id",
                            "trace_id",
                            "dotted_order",
                            "is_root",
                            "start_time",
                            "end_time",
                            "run_type",
                            "status",
                            "total_tokens",
                            "prompt_tokens",
                            "completion_tokens",
                            "tags",
                            "session_id",
                        ],
                    },
                },
                "ResultSelector": {
                    "export_id.$": "$.Payload.export_id",
                    "status.$": "$.Payload.status",
                },
                "ResultPath": "$.export",
                "Next": "WaitForExport",
            },
            "WaitForExport": {
                "Type": "Wait",
                "Seconds": 60,
                "Next": "CheckExportStatus",
            },
            "CheckExportStatus": {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {
                    "FunctionName": check_export_arn,
                    "Payload": {
                        "export_id.$": "$.export.export_id",
                    },
                },
                "ResultSelector": {
                    "status.$": "$.Payload.status",
                    "export_id.$": "$.Payload.export_id",
                },
                "ResultPath": "$.export_check",
                "Next": "ExportReady",
            },
            "ExportReady": {
                "Type": "Choice",
                "Choices": [
                    {
                        "Variable": "$.export_check.status",
                        "StringEquals": "completed",
                        "Next": "PrepareEMRArgs",
                    },
                    {
                        "Variable": "$.export_check.status",
                        "StringEquals": "failed",
                        "Next": "ExportFailed",
                    },
                ],
                "Default": "IncrementRetryCount",
            },
            "IncrementRetryCount": {
                "Type": "Pass",
                "Parameters": {
                    "execution_id.$": "$.init.execution_id",
                    "batch_bucket.$": "$.init.batch_bucket",
                    "retry_count.$": "States.MathAdd($.init.retry_count, 1)",
                },
                "ResultPath": "$.init",
                "Next": "CheckRetryLimit",
            },
            "CheckRetryLimit": {
                "Type": "Choice",
                "Choices": [
                    {
                        "Variable": "$.init.retry_count",
                        "NumericGreaterThanEquals": 30,
                        "Next": "MaxRetriesExceeded",
                    }
                ],
                "Default": "WaitForExport",
            },
            # Format dynamic EMR arguments in a Pass state so the
            # StartEMRJob state can reference them with States.Array.
            "PrepareEMRArgs": {
                "Type": "Pass",
                "Parameters": {
                    "execution_id.$": "$.init.execution_id",
                    "receipts_json.$": "$.metadata.receipts_lookup_path",
                    "results_ndjson.$": (
                        "States.Format('s3://{}/{}', "
                        "$.init.batch_bucket, "
                        "$.questions_result.results_ndjson_key)"
                    ),
                    "langchain_project.$": "$.questions_result.langchain_project",
                    "parquet_input.$": (
                        f"States.Format('s3://{langsmith_export_bucket}"
                        "/traces/export_id={}/'"
                        ", $.export.export_id)"
                    ),
                },
                "ResultPath": "$.emr_args",
                "Next": "StartEMRJob",
            },
            "StartEMRJob": {
                "Type": "Task",
                "Resource": "arn:aws:states:::emr-serverless:startJobRun.sync",
                "Parameters": {
                    "ApplicationId": emr_application_id,
                    "ExecutionRoleArn": emr_job_role_arn,
                    "Name.$": "States.Format('qa-viz-cache-{}', $.init.execution_id)",
                    "JobDriver": {
                        "SparkSubmit": {
                            "EntryPoint": f"s3://{spark_artifacts_bucket}/spark/merged_job.py",
                            "EntryPointArguments.$": (
                                "States.Array("
                                "'--job-type', 'qa-cache', "
                                "'--parquet-input', $.emr_args.parquet_input, "
                                f"'--cache-bucket', '{cache_bucket}', "
                                "'--execution-id', $.emr_args.execution_id, "
                                "'--receipts-json', $.emr_args.receipts_json, "
                                "'--results-ndjson', $.emr_args.results_ndjson, "
                                "'--langchain-project', $.emr_args.langchain_project"
                                ")"
                            ),
                            "SparkSubmitParameters": (
                                "--conf spark.executor.memory=4g "
                                "--conf spark.executor.cores=2 "
                                "--conf spark.dynamicAllocation.enabled=true "
                                "--conf spark.dynamicAllocation.minExecutors=1 "
                                "--conf spark.dynamicAllocation.maxExecutors=4 "
                                "--conf spark.sql.legacy.parquet.nanosAsLong=true"
                            ),
                        }
                    },
                    "ConfigurationOverrides": {
                        "MonitoringConfiguration": {
                            "S3MonitoringConfiguration": {
                                "LogUri": f"s3://{spark_artifacts_bucket}/logs/"
                            }
                        }
                    },
                },
                "ResultPath": "$.emr_result",
                "Next": "Done",
            },
            "Done": {
                "Type": "Succeed",
            },
            "ExportFailed": {
                "Type": "Fail",
                "Error": "LangSmithExportFailed",
                "Cause": "LangSmith bulk export failed",
            },
            "MaxRetriesExceeded": {
                "Type": "Fail",
                "Error": "MaxRetriesExceeded",
                "Cause": "LangSmith export polling exceeded 30 retries",
            },
        },
    }
