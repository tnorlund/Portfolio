"""Pulumi infrastructure for merchant validation Step Functions."""

import json
import os
from typing import Optional

import pulumi
from dynamo_db import dynamodb_table  # pylint: disable=import-error
from lambda_layer import dynamo_layer  # pylint: disable=import-error
from lambda_layer import label_layer
from pulumi import (AssetArchive, ComponentResource, Config, FileAsset, Output,
                    ResourceOptions)
from pulumi_aws.cloudwatch import EventRule, EventTarget
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment
from pulumi_aws.lambda_ import Function, FunctionEnvironmentArgs
from pulumi_aws.sfn import StateMachine

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

    def __init__(self, name: str, opts: Optional[ResourceOptions] = None):
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

        RolePolicyAttachment(
            f"{name}-{stack}-lambda-basic-execution",
            role=lambda_exec_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/" "AWSLambdaBasicExecutionRole"
            ),
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

        # Define Lambda: validate_receipt
        validate_receipt_lambda = Function(
            f"{name}-{stack}-validate-receipt",
            name=f"{name}-{stack}-validate-receipt",
            role=lambda_exec_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="handlers.validate_single_receipt_v2.validate_handler",
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
                    "handlers/validate_single_receipt_v2.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "handlers",
                            "validate_single_receipt_v2.py",
                        )
                    ),
                }
            ),
            timeout=900,
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
                                    f"arn:aws:dynamodb:*:*:table/" f"{table_name}*"
                                ),
                            }
                        ],
                    }
                )
            ),
        )

        # Step Function definition
        StateMachine(
            f"{name}-{stack}-merchant-validation-sm",
            name=f"{name}-{stack}-merchant-validation-sm",
            role_arn=sfn_role.arn,
            definition=Output.all(
                list_receipts_lambda.arn,
                validate_receipt_lambda.arn,
                consolidate_new_metadata_lambda.arn,
            ).apply(
                lambda arns: json.dumps(
                    {
                        "StartAt": "ListReceipts",
                        "States": {
                            "ListReceipts": {
                                "Type": "Task",
                                "Resource": arns[0],  # list_receipts_lambda.arn
                                "Next": "ForEachReceipt",
                            },
                            "ForEachReceipt": {
                                "Type": "Map",
                                "ItemsPath": "$.receipts",
                                "MaxConcurrency": 5,
                                "Parameters": {
                                    "image_id.$": "$$.Map.Item.Value.image_id",
                                    "receipt_id.$": ("$$.Map.Item.Value.receipt_id"),
                                },
                                "Iterator": {
                                    "StartAt": "ValidateReceipt",
                                    "States": {
                                        "ValidateReceipt": {
                                            "Type": "Task",
                                            "Resource": arns[
                                                1
                                            ],  # validate_receipt_lambda.arn
                                            "End": True,
                                        }
                                    },
                                },
                                "ResultPath": "$.validationResults",
                                "Next": "ConsolidateMetadata",
                            },
                            "ConsolidateMetadata": {
                                "Type": "Task",
                                "Resource": arns[
                                    2
                                ],  # consolidate_new_metadata_lambda.arn
                                # Pass the entire state including both
                                # original receipts and validation results
                                "InputPath": "$",
                                "End": True,
                            },
                        },
                    }
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
