"""
Pulumi infrastructure for QA Agent Step Function pipeline.

This component creates a Step Function that:
1. Lists the 32 marquee questions (zip Lambda)
2. Runs each question through the 5-node QA graph (container Lambda, Map state)
3. Aggregates results and extracts receipt keys (zip Lambda)
4. Queries DynamoDB for receipt metadata (zip Lambda)
5. Triggers LangSmith bulk export (reuses existing Lambda)
6. Polls export status with retry loop (reuses existing Lambda)
7. Starts EMR Spark job to build per-question viz cache

Architecture:
- Zip Lambdas: list_questions, aggregate_results, query_receipt_metadata
- Container Lambda: run_question (QA graph with LLM agent)
- S3 Bucket: intermediate results (NDJSON, receipts-lookup.json)
- Step Function: orchestration with Map state (MaxConcurrency=10)
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
    FileArchive,
    FileAsset,
    Output,
    ResourceOptions,
)

# Import shared components
try:
    from codebuild_docker_image import CodeBuildDockerImage
    from lambda_layer import dynamo_layer
except ImportError:
    CodeBuildDockerImage = None  # type: ignore
    dynamo_layer = None  # type: ignore

# Load secrets
config = Config("portfolio")
openrouter_api_key = config.require_secret("OPENROUTER_API_KEY")
langchain_api_key = config.require_secret("LANGCHAIN_API_KEY")
openai_api_key = config.require_secret("OPENAI_API_KEY")

HANDLERS_DIR = os.path.join(os.path.dirname(__file__), "handlers")


class QAAgentStepFunction(ComponentResource):
    """Step Function infrastructure for QA Agent marquee pipeline.

    Components:
    - Zip Lambda: list_questions (returns 32 questions)
    - Container Lambda: run_question (5-node QA graph)
    - Zip Lambda: aggregate_results (extract receipt keys, write NDJSON)
    - Zip Lambda: query_receipt_metadata (DynamoDB → receipts-lookup.json)
    - Step Function: orchestration with Map state + LangSmith export + EMR

    Workflow:
    1. ListQuestions → 32 questions
    2. RunQuestions (Map, MaxConcurrency=10) → per-question results
    3. AggregateResults → NDJSON to S3, extract receipt keys
    4. QueryReceiptMetadata → receipts-lookup.json to S3
    5. TriggerLangSmithExport → start bulk export
    6. WaitForExport → 60s wait
    7. CheckExportStatus → poll loop (max 30 retries)
    8. StartEMRJob → qa_viz_cache_job.py
    """

    def __init__(
        self,
        name: str,
        *,
        dynamodb_table_name: pulumi.Input[str],
        dynamodb_table_arn: pulumi.Input[str],
        chromadb_bucket_name: pulumi.Input[str],
        chromadb_bucket_arn: pulumi.Input[str],
        # EMR Serverless
        emr_application_id: pulumi.Input[str],
        emr_job_execution_role_arn: pulumi.Input[str],
        langsmith_export_bucket: pulumi.Input[str],
        analytics_output_bucket: pulumi.Input[str],
        spark_artifacts_bucket: pulumi.Input[str],
        cache_bucket: pulumi.Input[str],
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

        # ECR permissions for container Lambda
        aws.iam.RolePolicy(
            f"{name}-lambda-ecr-policy",
            role=lambda_role.id,
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

        # S3 permissions (batch bucket + ChromaDB bucket)
        aws.iam.RolePolicy(
            f"{name}-lambda-s3-policy",
            role=lambda_role.id,
            policy=Output.all(
                self.batch_bucket.arn,
                Output.from_input(chromadb_bucket_arn),
            ).apply(
                lambda args: json.dumps(
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
                                "Resource": [
                                    args[0],
                                    f"{args[0]}/*",
                                    args[1],
                                    f"{args[1]}/*",
                                ],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=lambda_role),
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
        # Zip Lambdas
        # ============================================================

        # list_questions Lambda
        self.list_questions_lambda = aws.lambda_.Function(
            f"{name}-list-questions",
            runtime="python3.12",
            architectures=["arm64"],
            role=lambda_role.arn,
            code=AssetArchive(
                {"index.py": FileAsset(os.path.join(HANDLERS_DIR, "list_questions.py"))}
            ),
            handler="index.handler",
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "BATCH_BUCKET": self.batch_bucket.id,
                }
            ),
            memory_size=256,
            timeout=60,
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        aws.cloudwatch.LogGroup(
            f"{name}-list-questions-logs",
            name=self.list_questions_lambda.name.apply(
                lambda n: f"/aws/lambda/{n}"
            ),
            retention_in_days=30,
            opts=ResourceOptions(parent=self),
        )

        # aggregate_results Lambda
        self.aggregate_results_lambda = aws.lambda_.Function(
            f"{name}-aggregate-results",
            runtime="python3.12",
            architectures=["arm64"],
            role=lambda_role.arn,
            code=AssetArchive(
                {"index.py": FileAsset(os.path.join(HANDLERS_DIR, "aggregate_results.py"))}
            ),
            handler="index.handler",
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={
                    "BATCH_BUCKET": self.batch_bucket.id,
                }
            ),
            memory_size=512,
            timeout=300,
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        aws.cloudwatch.LogGroup(
            f"{name}-aggregate-results-logs",
            name=self.aggregate_results_lambda.name.apply(
                lambda n: f"/aws/lambda/{n}"
            ),
            retention_in_days=30,
            opts=ResourceOptions(parent=self),
        )

        # query_receipt_metadata Lambda (uses dynamo layer)
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
        # Container Lambda: run_question (QA graph)
        # ============================================================
        lambda_config = {
            "memory_size": 3072,
            "timeout": 900,
            "ephemeral_storage_size": 10240,
            "architectures": ["arm64"],
            "environment": {
                "variables": {
                    "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                    "OPENROUTER_API_KEY": openrouter_api_key,
                    "OPENROUTER_MODEL": "x-ai/grok-4.1-fast",
                    "LANGCHAIN_API_KEY": langchain_api_key,
                    "LANGCHAIN_TRACING_V2": "true",
                    "LANGCHAIN_PROJECT": "qa-agent-marquee",
                    "RECEIPT_AGENT_OPENAI_API_KEY": openai_api_key,
                    "CHROMADB_BUCKET": chromadb_bucket_name,
                }
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
                self.list_questions_lambda.arn,
                self.run_question_lambda.arn,
                self.aggregate_results_lambda.arn,
                self.query_metadata_lambda.arn,
                Output.from_input(trigger_export_lambda_arn),
                Output.from_input(check_export_lambda_arn),
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": "lambda:InvokeFunction",
                                "Resource": list(args),
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "emr-serverless:StartJobRun",
                                    "emr-serverless:GetJobRun",
                                    "emr-serverless:CancelJobRun",
                                ],
                                "Resource": "*",
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
            self.list_questions_lambda.arn,
            self.run_question_lambda.arn,
            self.aggregate_results_lambda.arn,
            self.query_metadata_lambda.arn,
            Output.from_input(trigger_export_lambda_arn),
            Output.from_input(check_export_lambda_arn),
            Output.from_input(emr_application_id),
            Output.from_input(emr_job_execution_role_arn),
            Output.from_input(spark_artifacts_bucket),
            Output.from_input(langsmith_export_bucket),
            Output.from_input(cache_bucket),
            self.batch_bucket.id,
        ).apply(
            lambda args: json.dumps(
                _build_state_machine_definition(
                    list_questions_arn=args[0],
                    run_question_arn=args[1],
                    aggregate_results_arn=args[2],
                    query_metadata_arn=args[3],
                    trigger_export_arn=args[4],
                    check_export_arn=args[5],
                    emr_application_id=args[6],
                    emr_job_role_arn=args[7],
                    spark_artifacts_bucket=args[8],
                    langsmith_export_bucket=args[9],
                    cache_bucket=args[10],
                    batch_bucket=args[11],
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
    list_questions_arn: str,
    run_question_arn: str,
    aggregate_results_arn: str,
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
    """Build the Step Function ASL definition."""
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
                "Next": "ListQuestions",
            },
            "ListQuestions": {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {
                    "FunctionName": list_questions_arn,
                    "Payload": {
                        "execution_id.$": "$.init.execution_id",
                    },
                },
                "ResultSelector": {
                    "questions.$": "$.Payload.questions",
                    "total_questions.$": "$.Payload.total_questions",
                    "batch_bucket.$": "$.Payload.batch_bucket",
                },
                "ResultPath": "$.questions_result",
                "Next": "RunQuestions",
            },
            "RunQuestions": {
                "Type": "Map",
                "ItemsPath": "$.questions_result.questions",
                "MaxConcurrency": 10,
                "Parameters": {
                    "question.$": "$$.Map.Item.Value",
                    "execution_id.$": "$.init.execution_id",
                    "batch_bucket.$": "$.init.batch_bucket",
                },
                "ItemProcessor": {
                    "ProcessorConfig": {"Mode": "INLINE"},
                    "StartAt": "RunQuestion",
                    "States": {
                        "RunQuestion": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::lambda:invoke",
                            "Parameters": {
                                "FunctionName": run_question_arn,
                                "Payload.$": "$",
                            },
                            "ResultSelector": {"result.$": "$.Payload"},
                            "OutputPath": "$.result",
                            "TimeoutSeconds": 900,
                            "Retry": [
                                {
                                    "ErrorEquals": [
                                        "Lambda.ServiceException",
                                        "Lambda.AWSLambdaException",
                                        "Lambda.SdkClientException",
                                    ],
                                    "IntervalSeconds": 10,
                                    "MaxAttempts": 2,
                                    "BackoffRate": 2,
                                }
                            ],
                            "End": True,
                        }
                    },
                },
                "ResultPath": "$.results",
                "Next": "AggregateResults",
            },
            "AggregateResults": {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {
                    "FunctionName": aggregate_results_arn,
                    "Payload": {
                        "results.$": "$.results",
                        "execution_id.$": "$.init.execution_id",
                        "batch_bucket.$": "$.init.batch_bucket",
                    },
                },
                "ResultSelector": {
                    "receipt_keys.$": "$.Payload.receipt_keys",
                    "total_questions.$": "$.Payload.total_questions",
                    "success_count.$": "$.Payload.success_count",
                    "results_ndjson_key.$": "$.Payload.results_ndjson_key",
                },
                "ResultPath": "$.aggregate",
                "Next": "QueryReceiptMetadata",
            },
            "QueryReceiptMetadata": {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {
                    "FunctionName": query_metadata_arn,
                    "Payload": {
                        "receipt_keys.$": "$.aggregate.receipt_keys",
                        "execution_id.$": "$.init.execution_id",
                        "batch_bucket.$": "$.init.batch_bucket",
                    },
                },
                "ResultSelector": {
                    "receipts_lookup_path.$": "$.Payload.receipts_lookup_path",
                    "receipts_found.$": "$.Payload.receipts_found",
                },
                "ResultPath": "$.metadata",
                "Next": "TriggerLangSmithExport",
            },
            "TriggerLangSmithExport": {
                "Type": "Task",
                "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {
                    "FunctionName": trigger_export_arn,
                    "Payload": {
                        "project_name": "qa-agent-marquee",
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
                        "Next": "StartEMRJob",
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
            "StartEMRJob": {
                "Type": "Task",
                "Resource": "arn:aws:states:::emr-serverless:startJobRun.sync",
                "Parameters": {
                    "ApplicationId": emr_application_id,
                    "ExecutionRoleArn": emr_job_role_arn,
                    "Name.$": "States.Format('qa-viz-cache-{}', $.init.execution_id)",
                    "JobDriver": {
                        "SparkSubmit": {
                            "EntryPoint": f"s3://{spark_artifacts_bucket}/scripts/merged_job.py",
                            "EntryPointArguments": [
                                "--job-type",
                                "qa-cache",
                                "--parquet-input",
                                f"s3://{langsmith_export_bucket}/traces/",
                                "--cache-bucket",
                                cache_bucket,
                                "--batch-bucket",
                                batch_bucket,
                                "--execution-id.$",
                                "$.init.execution_id",
                                "--receipts-json.$",
                                "$.metadata.receipts_lookup_path",
                                "--results-ndjson.$",
                                "States.Format('s3://{}/{}', $.init.batch_bucket, $.aggregate.results_ndjson_key)",
                            ],
                            "SparkSubmitParameters": (
                                "--conf spark.executor.memory=4g "
                                "--conf spark.executor.cores=2 "
                                "--conf spark.dynamicAllocation.enabled=true "
                                "--conf spark.dynamicAllocation.minExecutors=1 "
                                "--conf spark.dynamicAllocation.maxExecutors=4"
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
