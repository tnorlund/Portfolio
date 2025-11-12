"""Pulumi infrastructure for merchant validation Step Functions."""

import json
import os
from typing import Optional
from pathlib import Path

import pulumi
from dynamo_db import dynamodb_table  # pylint: disable=import-error
from lambda_layer import dynamo_layer  # pylint: disable=import-error
from lambda_layer import label_layer
from pulumi import (
    AssetArchive,
    ComponentResource,
    Config,
    FileAsset,
    Output,
    ResourceOptions,
    StringAsset,
)
from pulumi_aws.cloudwatch import EventRule, EventTarget
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment
from pulumi_aws.lambda_ import (
    Function,
    FunctionEnvironmentArgs,
    FunctionVpcConfigArgs,
)
from pulumi_aws.ecr import (
    Repository,
    RepositoryImageScanningConfigurationArgs,
)
from pulumi_aws.sfn import StateMachine

# Import the CodeBuildDockerImage component
from codebuild_docker_image import CodeBuildDockerImage

config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")
pinecone_api_key = config.require_secret("PINECONE_API_KEY")
pinecone_index_name = config.require("PINECONE_INDEX_NAME")
pinecone_host = config.require("PINECONE_HOST")
google_places_api_key = config.require_secret("GOOGLE_PLACES_API_KEY")


class ValidateMerchantStepFunctions(ComponentResource):
    """
    AWS Step Functions infrastructure for merchant validation and
    consolidation.

    This Pulumi component creates a comprehensive merchant validation system
    that
    processes receipt metadata to establish canonical merchant representations.
    The system operates in three phases:

    Phase 1 - Real-time Validation (Step Function):
        1. ListReceipts: Identifies receipts requiring merchant validation
        2. ForEachReceipt: Parallel processing of individual receipts
           (Map state)
        3. ConsolidateMetadata: Merges validated merchant data

    Phase 2 - Incremental Consolidation:
        Runs after each receipt validation to update canonical merchant data
        based on matching place_ids or self-canonization for new merchants.

    Phase 3 - Weekly Batch Cleaning:
        Scheduled CloudWatch Event triggers comprehensive merchant data
        reconciliation every Wednesday at midnight, including:
        - Clustering similar merchants
        - Geographic validation
        - Canonical data updates across all records

    Infrastructure Components:
    - 4 Lambda Functions:
        * list_receipts: Queries DynamoDB for receipts needing validation
        * validate_receipt: Enriches individual receipt with merchant data
        * consolidate_new_metadata: Updates canonical merchant information
        * batch_clean_merchants: Performs weekly comprehensive cleaning
    - Step Function state machine for orchestration
    - CloudWatch Events rule for weekly scheduling
    - IAM roles and policies for service permissions
    - DynamoDB access for metadata storage

    Environment Requirements:
    - OPENAI_API_KEY: For merchant name/address normalization
    - GOOGLE_PLACES_API_KEY: For location validation
    - PINECONE_*: Vector database for similarity matching
    - DYNAMO_TABLE_NAME: Metadata storage table

    Attributes:
        scheduled_cleaning_rule: CloudWatch Events rule for weekly processing
        scheduled_cleaning_target: Event target linking rule to Lambda
    """

    def __init__(
        self,
        name: str,
        *,
        vpc_subnet_ids: pulumi.Input[list[str]] | None = None,
        security_group_id: pulumi.Input[str] | None = None,
        chroma_http_endpoint: pulumi.Input[str] | None = None,
        ecs_cluster_arn: pulumi.Input[str] | None = None,
        ecs_service_arn: pulumi.Input[str] | None = None,
        nat_instance_id: pulumi.Input[str] | None = None,
        chromadb_bucket_name: pulumi.Input[str] | None = None,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(f"{__name__}-{name}", name, None, opts)
        stack = pulumi.get_stack()

        # Define IAM role for Step Function
        sfn_role = Role(
            f"{name}-{stack}-merchant-sfn-role",
            name=f"{name}-{stack}-merchant-sfn-role",
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

        lambda_exec_role = Role(
            f"{name}-{stack}-lambda-role",
            name=f"{name}-{stack}-lambda-role",
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

        # Simple connectivity probe Lambda (zip) to verify internet egress via NAT
        connectivity_probe = Function(
            f"{name}-{stack}-connectivity-probe",
            name=f"{name}-{stack}-connectivity-probe",
            role=lambda_exec_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="probe.lambda_handler",
            code=AssetArchive(
                {
                    "probe.py": StringAsset(
                        """
import json
import os
import urllib.request

def lambda_handler(event, _ctx):
    url = event.get("url", "https://api.openai.com/v1/models")
    headers = {}
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=int(event.get("timeout", 5))) as resp:
            return {"status": resp.status, "reason": resp.reason}
    except Exception as e:
        return {"error": str(e)}
                        """
                    )
                }
            ),
            timeout=30,
            memory_size=128,
            environment=FunctionEnvironmentArgs(
                variables={
                    "OPENAI_API_KEY": openai_api_key,
                }
            ),
            vpc_config=(
                FunctionVpcConfigArgs(
                    subnet_ids=vpc_subnet_ids,
                    security_group_ids=(
                        [security_group_id] if security_group_id else None
                    ),
                )
                if vpc_subnet_ids and security_group_id
                else None
            ),
            opts=ResourceOptions(parent=self),
        )

        RolePolicyAttachment(
            f"{name}-{stack}-lambda-basic-execution",
            role=lambda_exec_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/"
                "AWSLambdaBasicExecutionRole"
            ),
        )

        # Required for VPC-enabled Lambdas to manage ENIs
        RolePolicyAttachment(
            f"{name}-{stack}-lambda-vpc-access",
            role=lambda_exec_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/"
                "AWSLambdaVPCAccessExecutionRole"
            ),
        )

        # Add ECR permissions for container image Lambda functions
        # Lambda service needs to pull images from ECR when code is updated
        RolePolicy(
            f"{name}-{stack}-lambda-ecr-policy",
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

        # Define Lambda: batch_clean_merchants (manual run)
        batch_clean_merchants_lambda = Function(
            f"{name}-{stack}-batch-clean-merchants",
            name=f"{name}-{stack}-batch-clean-merchants",
            role=lambda_exec_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="handlers.batch_clean_merchants.batch_handler",
            code=AssetArchive(
                {
                    "handlers/__init__.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "handlers",
                            "__init__.py",
                        )
                    ),
                    "handlers/common.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "handlers",
                            "common.py",
                        )
                    ),
                    "handlers/batch_clean_merchants.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "handlers",
                            "batch_clean_merchants.py",
                        )
                    ),
                }
            ),
            timeout=900,
            memory_size=512,
            layers=[dynamo_layer.arn, label_layer.arn],
            tags={"environment": stack},
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMO_TABLE_NAME": dynamodb_table.name,
                    "GOOGLE_PLACES_API_KEY": google_places_api_key,
                    "OPENAI_API_KEY": openai_api_key,
                    "PINECONE_API_KEY": pinecone_api_key,
                    "PINECONE_INDEX_NAME": pinecone_index_name,
                    "PINECONE_HOST": pinecone_host,
                }
            ),
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["layers"],
            ),
        )

        # Define Lambda: list_receipts
        list_receipts_lambda = Function(
            f"{name}-{stack}-list-receipts",
            name=f"{name}-{stack}-list-receipts",
            role=lambda_exec_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="handlers.list_receipts.list_handler",
            code=AssetArchive(
                {
                    "handlers/__init__.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "handlers",
                            "__init__.py",
                        )
                    ),
                    "handlers/common.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "handlers",
                            "common.py",
                        )
                    ),
                    "handlers/list_receipts.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "handlers",
                            "list_receipts.py",
                        )
                    ),
                }
            ),
            timeout=900,
            memory_size=512,
            layers=[dynamo_layer.arn, label_layer.arn],
            tags={"environment": stack},
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMO_TABLE_NAME": dynamodb_table.name,
                    "GOOGLE_PLACES_API_KEY": google_places_api_key,
                    "OPENAI_API_KEY": openai_api_key,
                    "PINECONE_API_KEY": pinecone_api_key,
                    "PINECONE_INDEX_NAME": pinecone_index_name,
                    "PINECONE_HOST": pinecone_host,
                }
            ),
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["layers"],
            ),
        )

        # Container image for container-based validate_receipt
        # Build using repository root so Dockerfile can COPY package dirs
        repo_root = Path(__file__).resolve().parents[2]
        container_dir = (
            repo_root
            / "infra"
            / "validate_merchant_step_functions"
            / "container"
        )
        # Build Lambda config
        validate_lambda_config = {
            "role_arn": lambda_exec_role.arn,
            "timeout": 900,
            "memory_size": 512,
            "tags": {"environment": stack},
            "environment": {
                "DYNAMO_TABLE_NAME": dynamodb_table.name,
                "GOOGLE_PLACES_API_KEY": google_places_api_key,
                "OPENAI_API_KEY": openai_api_key,
                "PINECONE_API_KEY": pinecone_api_key,
                "PINECONE_INDEX_NAME": pinecone_index_name,
                "PINECONE_HOST": pinecone_host,
                # Optional remote Chroma endpoint for HTTP queries
                "CHROMA_HTTP_ENDPOINT": chroma_http_endpoint or "",
                # Bucket for delta uploads
                "CHROMADB_BUCKET": (
                    chromadb_bucket_name
                    if chromadb_bucket_name is not None
                    else pulumi.Output.secret(
                        pulumi.Config("portfolio").get(
                            "chromadb_bucket_name"
                        )
                        or ""
                    )
                ),
            },
        }
        
        # Add VPC config if provided
        if vpc_subnet_ids and security_group_id:
            validate_lambda_config["vpc_config"] = {
                "subnet_ids": vpc_subnet_ids,
                "security_group_ids": [security_group_id],
            }
        
        # Use CodeBuildDockerImage for AWS-based builds
        validate_docker_image = CodeBuildDockerImage(
            f"{name}-{stack}-validate-image",
            dockerfile_path="infra/validate_merchant_step_functions/container/Dockerfile",
            build_context_path=".",  # Project root for monorepo access
            source_paths=None,  # Use default rsync with exclusions
            lambda_function_name=f"{name}-{stack}-validate-receipt",
            lambda_config=validate_lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_exec_role]),
        )

        # Use the Lambda function created by CodeBuildDockerImage
        validate_receipt_lambda = validate_docker_image.lambda_function

        # Allow Lambda to write deltas to the Chroma bucket (put/list)
        if chromadb_bucket_name is not None:
            RolePolicy(
                f"{name}-{stack}-lambda-chroma-s3-write",
                role=lambda_exec_role.id,
                policy=chromadb_bucket_name.apply(
                    lambda bkt: json.dumps(
                        {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Action": [
                                        "s3:PutObject",
                                        "s3:PutObjectAcl",
                                        "s3:AbortMultipartUpload",
                                        "s3:ListBucketMultipartUploads",
                                        "s3:ListBucket",
                                        "s3:CreateMultipartUpload",
                                        "s3:ListMultipartUploadParts",
                                    ],
                                    "Resource": [
                                        f"arn:aws:s3:::{bkt}",
                                        f"arn:aws:s3:::{bkt}/*",
                                    ],
                                }
                            ],
                        }
                    )
                ),
            )

        # Optional: Chroma wait Lambda to ensure HTTP server is healthy before validation
        # Reuse handler from infra/chroma/wait_handler
        wait_lambda = None
        if chroma_http_endpoint and ecs_cluster_arn and ecs_service_arn:
            handler_dir = (
                Path(__file__).resolve().parent.parent
                / "chroma"
                / "wait_handler"
            )
            wait_lambda = Function(
                f"{name}-{stack}-chroma-wait",
                name=f"{name}-{stack}-chroma-wait",
                role=lambda_exec_role.arn,
                runtime="python3.12",
                architectures=["arm64"],
                handler="index.lambda_handler",
                code=pulumi.FileArchive(str(handler_dir)),
                timeout=180,
                memory_size=256,
                environment=FunctionEnvironmentArgs(
                    variables={
                        "CHROMA_HTTP_ENDPOINT": chroma_http_endpoint,
                        "ECS_CLUSTER_ARN": ecs_cluster_arn,
                        "ECS_SERVICE_ARN": ecs_service_arn,
                        "WAIT_TIMEOUT_SECONDS": "120",
                        "WAIT_INTERVAL_SECONDS": "5",
                    }
                ),
                vpc_config=(
                    FunctionVpcConfigArgs(
                        subnet_ids=vpc_subnet_ids,
                        security_group_ids=(
                            [security_group_id] if security_group_id else None
                        ),
                    )
                    if vpc_subnet_ids and security_group_id
                    else None
                ),
                opts=ResourceOptions(parent=self),
            )

            # Allow Step Function to call ECS APIs, wait Lambda, and optionally EC2 for NAT
            RolePolicy(
                f"{name}-{stack}-sfn-ecs-policy",
                role=sfn_role.name,
                policy=Output.all(
                    ecs_service_arn, wait_lambda.arn, nat_instance_id
                ).apply(
                    lambda args: json.dumps(
                        {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Action": [
                                        "ecs:UpdateService",
                                        "ecs:DescribeServices",
                                    ],
                                    "Resource": [args[0]],
                                },
                                {
                                    "Effect": "Allow",
                                    "Action": ["lambda:InvokeFunction"],
                                    "Resource": [args[1]],
                                },
                                # EC2 permissions for NAT instance start/stop/describe (if provided)
                                *(
                                    [
                                        {
                                            "Effect": "Allow",
                                            "Action": [
                                                "ec2:StartInstances",
                                                "ec2:StopInstances",
                                                "ec2:DescribeInstanceStatus",
                                                "ec2:DescribeInstances",
                                            ],
                                            "Resource": "*",
                                        }
                                    ]
                                    if args[2]
                                    else []
                                ),
                            ],
                        }
                    )
                ),
                opts=ResourceOptions(parent=self),
            )

        # Define Lambda: consolidate_new_metadata (Phase 2)
        consolidate_new_metadata_lambda = Function(
            f"{name}-{stack}-consolidate",
            name=f"{name}-{stack}-consolidate",
            role=lambda_exec_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="handlers.consolidate_new_metadata.consolidate_handler",
            code=AssetArchive(
                {
                    "handlers/__init__.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "handlers",
                            "__init__.py",
                        )
                    ),
                    "handlers/common.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "handlers",
                            "common.py",
                        )
                    ),
                    "handlers/consolidate_new_metadata.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "handlers",
                            "consolidate_new_metadata.py",
                        )
                    ),
                }
            ),
            timeout=300,
            memory_size=512,
            layers=[dynamo_layer.arn, label_layer.arn],
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMO_TABLE_NAME": dynamodb_table.name,
                    "GOOGLE_PLACES_API_KEY": google_places_api_key,
                    "OPENAI_API_KEY": openai_api_key,
                    "PINECONE_API_KEY": pinecone_api_key,
                    "PINECONE_INDEX_NAME": pinecone_index_name,
                    "PINECONE_HOST": pinecone_host,
                }
            ),
            tags={"environment": stack},
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["layers"],
            ),
        )

        # Allow Step Function to invoke Lambdas
        RolePolicy(
            f"{name}-{stack}-invoke-policy",
            role=sfn_role.id,
            policy=list_receipts_lambda.arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": "lambda:InvokeFunction",
                                "Resource": "*",
                            }
                        ],
                    }
                )
            ),
        )

        # Custom inline policy for DynamoDB access
        RolePolicy(
            f"{name}-{stack}-lambda-dynamo-policy",
            role=lambda_exec_role.id,
            policy=dynamodb_table.name.apply(
                lambda table_name: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:DescribeTable",
                                    "dynamodb:GetItem",
                                    "dynamodb:BatchGetItem",
                                    "dynamodb:Query",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:BatchWriteItem",
                                ],
                                "Resource": (
                                    f"arn:aws:dynamodb:*:*:table/"
                                    f"{table_name}*"
                                ),
                            }
                        ],
                    }
                )
            ),
        )

        # Step Function definition (optionally warm up Chroma before validation)
        StateMachine(
            f"{name}-{stack}-merchant-validation-sm",
            name=f"{name}-{stack}-merchant-validation-sm",
            role_arn=sfn_role.arn,
            definition=Output.all(
                list_receipts_lambda.arn,
                validate_receipt_lambda.arn,
                consolidate_new_metadata_lambda.arn,
                wait_lambda.arn if wait_lambda else Output.from_input(""),
                ecs_cluster_arn or Output.from_input(""),
                ecs_service_arn or Output.from_input(""),
                nat_instance_id or Output.from_input(""),
            ).apply(
                lambda arns: json.dumps(
                    (
                        {
                            "StartAt": (
                                "ScaleUpChroma" if arns[3] else "ListReceipts"
                            ),
                            "States": {
                                **(
                                    {
                                        "ScaleUpChroma": {
                                            "Type": "Task",
                                            "Resource": "arn:aws:states:::aws-sdk:ecs:updateService",
                                            "Parameters": {
                                                "Cluster": arns[4],
                                                "Service": arns[5],
                                                "DesiredCount": 1,
                                            },
                                            "Next": "AwaitChromaTasks",
                                        },
                                        "AwaitChromaTasks": {
                                            "Type": "Task",
                                            "Resource": "arn:aws:states:::aws-sdk:ecs:describeServices",
                                            "Parameters": {
                                                "Cluster": arns[4],
                                                "Services": [arns[5]],
                                            },
                                            "ResultSelector": {
                                                "runningCount.$": "$.Services[0].RunningCount",
                                            },
                                            "ResultPath": "$.svc",
                                            "Next": "ChromaTasksReady?",
                                        },
                                        "ChromaTasksReady?": {
                                            "Type": "Choice",
                                            "Choices": [
                                                {
                                                    "Variable": "$.svc.runningCount",
                                                    "NumericGreaterThanEquals": 1,
                                                    "Next": "WaitForChromaHealthy",
                                                }
                                            ],
                                            "Default": "WaitChromaDelay",
                                        },
                                        "WaitChromaDelay": {
                                            "Type": "Wait",
                                            "Seconds": 5,
                                            "Next": "AwaitChromaTasks",
                                        },
                                        "WaitForChromaHealthy": {
                                            "Type": "Task",
                                            "Resource": "arn:aws:states:::lambda:invoke",
                                            "Parameters": {
                                                "FunctionName": arns[3]
                                            },
                                            "Next": "ListReceipts",
                                        },
                                    }
                                    if arns[3]
                                    else {}
                                ),
                                "ListReceipts": {
                                    "Type": "Task",
                                    "Resource": arns[0],
                                    "Next": "ForEachReceipt",
                                },
                                "ForEachReceipt": {
                                    "Type": "Map",
                                    "ItemsPath": "$.receipts",
                                    "MaxConcurrency": 5,
                                    "Parameters": {
                                        "image_id.$": "$$.Map.Item.Value.image_id",
                                        "receipt_id.$": "$$.Map.Item.Value.receipt_id",
                                    },
                                    "Iterator": {
                                        "StartAt": "ValidateReceipt",
                                        "States": {
                                            "ValidateReceipt": {
                                                "Type": "Task",
                                                "Resource": arns[1],
                                                "End": True,
                                            }
                                        },
                                    },
                                    "ResultPath": "$.validationResults",
                                    "Next": "ConsolidateMetadata",
                                },
                                "ConsolidateMetadata": {
                                    "Type": "Task",
                                    "Resource": arns[2],
                                    "InputPath": "$",
                                    "Next": (
                                        "ScaleDownChroma"
                                        if arns[3]
                                        else "Done"
                                    ),
                                },
                                **(
                                    {
                                        "ScaleDownChroma": {
                                            "Type": "Task",
                                            "Resource": "arn:aws:states:::aws-sdk:ecs:updateService",
                                            "Parameters": {
                                                "Cluster": arns[4],
                                                "Service": arns[5],
                                                "DesiredCount": 0,
                                            },
                                            "Next": "Done",
                                        }
                                    }
                                    if arns[3]
                                    else {}
                                ),
                                "Done": {"Type": "Succeed"},
                            },
                        }
                    )
                ),
            ),
            opts=ResourceOptions(parent=self),
        )

        # Set up weekly scheduled cleaning (Phase 3)
        # Create the CloudWatch Event rule for Wednesday at midnight
        # (cron expression)
        # Cron format: minute hour day-of-month month day-of-week year
        # 0 0 ? * WED * = At 00:00 (midnight) on Wednesday
        scheduled_cleaning_rule = EventRule(
            f"{name}-{stack}-weekly-cleaning-schedule",
            name=f"{name}-{stack}-weekly-cleaning-schedule",
            description=(
                "Trigger weekly merchant data cleaning process every "
                "Wednesday at midnight"
            ),
            schedule_expression="cron(0 0 ? * WED *)",
            state="ENABLED",
            opts=ResourceOptions(parent=self),
        )

        # Define the input to pass to the Lambda function
        event_input = {
            "max_records": None,  # Process all records (no limit)
            "geographic_validation": True,  # Enable geographic validation
            "schedule_info": "Weekly run on Wednesday at midnight",
        }

        # Define the event target (the batch cleaning Lambda)
        scheduled_cleaning_target = EventTarget(
            f"{name}-{stack}-lambda-target",
            rule=scheduled_cleaning_rule.name,
            arn=batch_clean_merchants_lambda.arn,
            input=json.dumps(event_input),
            opts=ResourceOptions(parent=self),
        )

        # Add additional permission to the Lambda role to allow
        # CloudWatch Events to invoke it
        RolePolicy(
            f"{name}-{stack}-cloudwatch-invoke-policy",
            role=lambda_exec_role.name,
            policy=batch_clean_merchants_lambda.arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["lambda:InvokeFunction"],
                                "Resource": arn,
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Export the scheduled cleaning resources
        self.scheduled_cleaning_rule = scheduled_cleaning_rule
        self.scheduled_cleaning_target = scheduled_cleaning_target
