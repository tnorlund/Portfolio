"""Pulumi infrastructure for validating ReceiptMetadata entities."""

import json
import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from dynamo_db import dynamodb_table  # pylint: disable=import-error
from infra.components.lambda_layer import dynamo_layer  # pylint: disable=import-error
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
from pulumi_aws.lambda_ import Function, FunctionEnvironmentArgs, FunctionVpcConfigArgs
from pulumi_aws.sfn import StateMachine, StateMachineLoggingConfigurationArgs

# Import the CodeBuildDockerImage component
from infra.components.codebuild_docker_image import CodeBuildDockerImage

config = Config("portfolio")
ollama_api_key = config.require_secret("OLLAMA_API_KEY")
langchain_api_key = config.require_secret("LANGCHAIN_API_KEY")
google_places_api_key = config.require_secret("GOOGLE_PLACES_API_KEY")


class ValidateMetadataStepFunction(ComponentResource):
    """
    Step Function infrastructure for validating ReceiptMetadata entities.

    Uses LangGraph + CoVe to validate metadata against receipt text.

    Infrastructure Components:
    - 2 Lambda Functions:
        * list_metadata: Queries DynamoDB for all ReceiptMetadata, creates manifest
        * validate_metadata: Validates ReceiptMetadata for a single receipt (container-based)
    - Step Function state machine for orchestration
    - IAM roles and policies for service permissions
    - S3 bucket for manifest storage
    - DynamoDB access for metadata storage
    """

    def __init__(
        self,
        name: str,
        *,
        dynamodb_table_name: pulumi.Input[str],
        dynamodb_table_arn: pulumi.Input[str],
        chromadb_bucket_name: pulumi.Input[str],
        chromadb_bucket_arn: pulumi.Input[str],
        vpc_subnet_ids: pulumi.Input[list[str]] | None = None,
        security_group_id: pulumi.Input[str] | None = None,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(f"{__name__}-{name}", name, None, opts)
        stack = pulumi.get_stack()

        # Create manifest S3 bucket
        manifest_bucket = aws.s3.Bucket(
            f"{name}-manifest-bucket",
            force_destroy=True,
            tags={"environment": stack, "purpose": "metadata-validation-manifest"},
            opts=ResourceOptions(parent=self),
        )

        # S3 lifecycle policy for cleanup (7-day TTL)
        aws.s3.BucketLifecycleConfigurationV2(
            f"{name}-manifest-lifecycle",
            bucket=manifest_bucket.id,
            rules=[
                aws.s3.BucketLifecycleConfigurationV2RuleArgs(
                    id="delete-old-manifests",
                    status="Enabled",
                    expiration=aws.s3.BucketLifecycleConfigurationV2RuleExpirationArgs(
                        days=7
                    ),
                    filter=aws.s3.BucketLifecycleConfigurationV2RuleFilterArgs(
                        prefix="metadata_validation/",
                    ),
                )
            ],
            opts=ResourceOptions(parent=self),
        )

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

        # No VPC needed - Lambda downloads ChromaDB from S3 and needs internet for Ollama API
        # DynamoDB and S3 access via gateway endpoints work from anywhere

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
                                "ecr:BatchGetImage",
                                "ecr:GetDownloadUrlForLayer",
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
            f"{name}-lambda-dynamo-policy",
            role=lambda_exec_role.id,
            policy=Output.all(dynamodb_table_arn).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:DescribeTable",  # Required for DynamoClient initialization
                                    "dynamodb:GetItem",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:Query",
                                    "dynamodb:Scan",
                                    "dynamodb:BatchGetItem",
                                    "dynamodb:BatchWriteItem",
                                ],
                                "Resource": [
                                    args[0],
                                    f"{args[0]}/index/*",
                                ],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=lambda_exec_role),
        )

        # S3 access policy (ChromaDB bucket + manifest bucket)
        RolePolicy(
            f"{name}-lambda-s3-policy",
            role=lambda_exec_role.id,
            policy=Output.all(chromadb_bucket_arn, manifest_bucket.arn).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:ListBucket",
                                ],
                                "Resource": [
                                    args[0],  # ChromaDB bucket
                                    f"{args[0]}/*",
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:PutObject",
                                    "s3:ListBucket",
                                ],
                                "Resource": [
                                    args[1],  # Manifest bucket
                                    f"{args[1]}/*",
                                ],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=lambda_exec_role),
        )

        # Step Function role policy (invoke Lambdas, S3 access, CloudWatch Logs)
        RolePolicy(
            f"{name}-sfn-policy",
            role=sfn_role.id,
            policy=Output.all(
                lambda_exec_role.arn, manifest_bucket.arn
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["lambda:InvokeFunction"],
                                "Resource": f"{args[0]}:*",  # All Lambdas with this role prefix
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:PutObject",
                                    "s3:ListBucket",
                                ],
                                "Resource": [
                                    args[1],
                                    f"{args[1]}/*",
                                ],
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

        # Create zip-based list_metadata Lambda
        list_metadata_lambda = Function(
            f"{name}-list-metadata",
            name=f"{name}-list-metadata",
            role=lambda_exec_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="list_metadata.handler",
            code=AssetArchive(
                {
                    "list_metadata.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "handlers",
                            "list_metadata.py",
                        )
                    ),
                    "utils/emf_metrics.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "lambdas",
                            "utils",
                            "emf_metrics.py",
                        )
                    ),
                    "utils/__init__.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "lambdas",
                            "utils",
                            "__init__.py",
                        )
                    ),
                }
            ),
            timeout=60,
            memory_size=256,
            layers=[dynamo_layer.arn],
            tags={"environment": stack},
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                    "S3_BUCKET": manifest_bucket.bucket,
                }
            ),
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["layers"],
            ),
        )

        # Create container-based validate_metadata Lambda
        validate_metadata_lambda_config = {
            "role_arn": lambda_exec_role.arn,
            "timeout": 900,  # 15 minutes
            "memory_size": 2048,  # 2 GB
            "ephemeral_storage": 10240,  # 10 GB for ChromaDB snapshot
            "tags": {"environment": stack},
            "environment": {
                "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                "CHROMADB_BUCKET": chromadb_bucket_name,
                "S3_BUCKET": manifest_bucket.bucket,
                "OLLAMA_API_KEY": ollama_api_key,
                "LANGCHAIN_API_KEY": langchain_api_key,
                "GOOGLE_PLACES_API_KEY": google_places_api_key,
            },
        }

        # VPC not needed - Lambda downloads ChromaDB from S3 and needs internet for Ollama API
        # DynamoDB and S3 access via gateway endpoints work from anywhere

        # Create container Lambda using CodeBuildDockerImage
        # Use shorter name to avoid AWS limits (64 chars for IAM roles, 63 for S3 buckets)
        validate_metadata_docker_image = CodeBuildDockerImage(
            f"{name}-val-metadata-img",
            dockerfile_path="infra/validate_metadata/lambdas/Dockerfile",
            build_context_path=".",  # Project root
            source_paths=None,  # Use default rsync with exclusions
            lambda_function_name=f"{name}-validate-metadata",
            lambda_config=validate_metadata_lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_exec_role]),
        )

        validate_metadata_lambda = validate_metadata_docker_image.lambda_function

        # Update Step Function role policy with actual Lambda ARNs
        RolePolicy(
            f"{name}-sfn-lambda-policy",
            role=sfn_role.id,
            policy=Output.all(
                list_metadata_lambda.arn, validate_metadata_lambda.arn
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
        # For STANDARD Step Functions, logs go to /aws/stepfunctions/ prefix
        log_group = LogGroup(
            f"{name}-sf-logs",
            name=f"/aws/stepfunctions/{name}-sf",
            retention_in_days=14,  # Retain logs for 14 days to control costs
            opts=ResourceOptions(parent=self),
        )

        # Create Step Function state machine with logging enabled
        # Use .apply() to handle Output[T] for log_destination
        logging_config = log_group.arn.apply(
            lambda arn: StateMachineLoggingConfigurationArgs(
                level="ALL",  # Log all events
                include_execution_data=True,  # Include input/output data
                log_destination=f"{arn}:*",  # Log to CloudWatch Logs
            )
        )

        self.state_machine = StateMachine(
            f"{name}-sf",
            name=f"{name}-sf",
            role_arn=sfn_role.arn,
            type="STANDARD",  # Standard workflow for long-running executions (up to 1 year)
            # Express has 5-minute limit, but we process 451 receipts taking 60-100+ seconds each
            tags={"environment": stack},
            definition=Output.all(
                list_metadata_lambda.arn, validate_metadata_lambda.arn
            ).apply(self._create_step_function_definition),
            logging_configuration=logging_config,
            opts=ResourceOptions(parent=self, depends_on=[log_group]),
        )

        # Store outputs as attributes for easy access
        self.state_machine_arn = self.state_machine.arn
        self.list_metadata_lambda_arn = list_metadata_lambda.arn
        self.validate_metadata_lambda_arn = validate_metadata_lambda.arn
        self.manifest_bucket_name = manifest_bucket.bucket

        # Register outputs
        self.register_outputs(
            {
                "state_machine_arn": self.state_machine.arn,
                "list_metadata_lambda_arn": list_metadata_lambda.arn,
                "validate_metadata_lambda_arn": validate_metadata_lambda.arn,
                "manifest_bucket_name": manifest_bucket.bucket,
            }
        )

    def _create_step_function_definition(self, arns: list) -> str:
        """Create Step Function definition."""
        list_lambda_arn = arns[0]
        validate_lambda_arn = arns[1]

        definition = {
            "Comment": "Validate ReceiptMetadata entities against receipt text",
            "StartAt": "ListMetadata",
            "States": {
                "ListMetadata": {
                    "Type": "Task",
                    "Resource": list_lambda_arn,
                    "ResultPath": "$.list_result",
                    "Next": "CheckMetadata",
                    "Retry": [
                        {
                            "ErrorEquals": [
                                "Lambda.ServiceException",
                                "Lambda.AWSLambdaException",
                                "Lambda.SdkClientException",
                            ],
                            "IntervalSeconds": 2,
                            "MaxAttempts": 3,
                            "BackoffRate": 2.0,
                        }
                    ],
                },
                "CheckMetadata": {
                    "Type": "Choice",
                    "Choices": [
                        {
                            "Variable": "$.list_result.total_receipts",
                            "NumericGreaterThan": 0,
                            "Next": "ProcessMetadata",
                        }
                    ],
                    "Default": "NoMetadata",
                },
                "ProcessMetadata": {
                    "Type": "Map",
                    "ItemsPath": "$.list_result.receipt_indices",
                    "MaxConcurrency": 3,  # Reduced from 10 to avoid Ollama rate limiting
                    "Parameters": {
                        "index.$": "$$.Map.Item.Value",
                        "manifest_s3_key.$": "$.list_result.manifest_s3_key",
                        "manifest_s3_bucket.$": "$.list_result.manifest_s3_bucket",
                        "execution_id.$": "$.list_result.execution_id",
                    },
                    "Iterator": {
                        "StartAt": "ValidateMetadata",
                        "States": {
                            "ValidateMetadata": {
                                "Type": "Task",
                                "Resource": validate_lambda_arn,
                                "End": True,
                                "Retry": [
                                    {
                                        "ErrorEquals": [
                                            "Lambda.ServiceException",
                                            "Lambda.AWSLambdaException",
                                            "Lambda.SdkClientException",
                                        ],
                                        "IntervalSeconds": 2,
                                        "MaxAttempts": 3,
                                        "BackoffRate": 2.0,
                                    }
                                ],
                                "Catch": [
                                    {
                                        "ErrorEquals": ["States.ALL"],
                                        "ResultPath": "$.error",
                                        "Next": "ValidateMetadataFailed",
                                    }
                                ],
                            },
                            "ValidateMetadataFailed": {
                                "Type": "Pass",
                                "End": True,
                            },
                        },
                    },
                    "End": True,
                },
                "NoMetadata": {
                    "Type": "Pass",
                    "Result": "No ReceiptMetadata entities found",
                    "End": True,
                },
            },
        }

        return json.dumps(definition)

