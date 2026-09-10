"""Run QA questions and build visualization caches directly from S3 records."""

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

from .definition import (
    build_state_machine_definition as _build_state_machine_definition,
)

# Load secrets
config = Config("portfolio")
openrouter_api_key = config.require_secret("OPENROUTER_API_KEY")
openai_api_key = config.require_secret("OPENAI_API_KEY")

# Model is a stack config so per-stack experiments (e.g. a pricier model on
# one stack) live in IaC instead of hand-edited Lambda env that the next
# deploy silently reverts. Override: pulumi config set portfolio:QA_OPENROUTER_MODEL <slug>
qa_openrouter_model = (
    config.get("QA_OPENROUTER_MODEL") or "openai/gpt-oss-120b"
)

HANDLERS_DIR = os.path.join(os.path.dirname(__file__), "handlers")


class QAAgentStepFunction(ComponentResource):
    """Run questions, look up receipt images, and publish the native cache."""

    def __init__(
        self,
        name: str,
        *,
        dynamodb_table_name: pulumi.Input[str],
        dynamodb_table_arn: pulumi.Input[str],
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(f"{__name__}-{name}", name, None, opts)
        stack = pulumi.get_stack()

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
                                    "dynamodb:SearchVectors",
                                ],
                                "Resource": [arn, f"{arn}/index/*"],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # S3 permissions (batch bucket only)
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
            runtime="python3.13",
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
                "OPENROUTER_MODEL": qa_openrouter_model,
                "LANGCHAIN_TRACING_V2": "false",
                "LANGSMITH_TRACING": "false",
                "LANGCHAIN_PROJECT": "qa-agent-marquee",
                "RECEIPT_AGENT_OPENAI_API_KEY": openai_api_key,
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
                "receipt_embeddings",
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

        self.build_cache_lambda = aws.lambda_.Function(
            f"{name}-build-viz-cache",
            runtime="python3.13",
            architectures=["arm64"],
            role=lambda_role.arn,
            code=AssetArchive(
                {
                    "index.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "build_viz_cache.py")
                    )
                }
            ),
            handler="index.handler",
            environment=aws.lambda_.FunctionEnvironmentArgs(
                variables={"BATCH_BUCKET": self.batch_bucket.id}
            ),
            memory_size=512,
            timeout=120,
            opts=ResourceOptions(parent=self),
        )
        aws.cloudwatch.LogGroup(
            f"{name}-build-viz-cache-logs",
            name=self.build_cache_lambda.name.apply(
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
                self.build_cache_lambda.arn,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": "lambda:InvokeFunction",
                                "Resource": args,
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
            self.run_question_lambda.arn,
            self.query_metadata_lambda.arn,
            self.build_cache_lambda.arn,
            self.batch_bucket.id,
        ).apply(
            lambda args: json.dumps(
                _build_state_machine_definition(
                    run_all_questions_arn=args[0],
                    query_metadata_arn=args[1],
                    build_cache_arn=args[2],
                    batch_bucket=args[3],
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
