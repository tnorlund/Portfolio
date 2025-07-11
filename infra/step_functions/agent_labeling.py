"""
Step Function for agent-based receipt labeling.

This module implements the orchestration of the agent labeling pipeline,
integrating with existing merchant validation and batch processing systems.
"""

import json
import os
from typing import Optional

import pulumi
from pulumi import (
    AssetArchive,
    ComponentResource,
    Config,
    FileAsset,
    Output,
    ResourceOptions,
)
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment
from pulumi_aws.lambda_ import Function, FunctionEnvironmentArgs
from pulumi_aws.sfn import StateMachine

from ..dynamo_db import dynamodb_table
from ..lambda_layer import dynamo_layer, label_layer
from ..notifications import NotificationSystem
from .base import BaseStepFunction

# Import references to other step functions
from ..validate_merchant_step_functions import ValidateMerchantStepFunctions
from ..word_label_step_functions import WordLabelStepFunctions

config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")
pinecone_api_key = config.require_secret("PINECONE_API_KEY")
pinecone_index_name = config.require("PINECONE_INDEX_NAME")
pinecone_host = config.require("PINECONE_HOST")


class AgentLabelingStepFunction(BaseStepFunction):
    """
    Step Function for agent-based receipt labeling with smart GPT integration.

    This orchestrates the complete labeling pipeline:
    1. Check for existing receipt metadata
    2. Conditionally run merchant validation
    3. Verify embeddings exist in Pinecone
    4. Run agent-based labeling with pattern detection
    5. Make smart decisions about GPT usage
    6. Queue for batch processing if needed
    7. Store labels in DynamoDB
    8. Optionally trigger validation pipeline

    The system achieves ~70% reduction in GPT calls through:
    - Pattern detection (dates, currency, contacts, quantities)
    - Smart decision logic based on essential labels
    - Merchant-specific pattern matching from Pinecone
    """

    def __init__(
        self,
        name: str,
        notification_system: NotificationSystem,
        validate_merchant_sfn: ValidateMerchantStepFunctions,
        word_label_sfn: WordLabelStepFunctions,
        opts: Optional[ResourceOptions] = None,
    ):
        """
        Initialize agent labeling Step Function.

        Args:
            name: Name for the Step Function
            notification_system: Notification system for errors
            validate_merchant_sfn: Reference to merchant validation
            word_label_sfn: Reference to batch processing
            opts: Pulumi resource options
        """
        super().__init__(name, notification_system, opts)

        self.validate_merchant_sfn = validate_merchant_sfn
        self.word_label_sfn = word_label_sfn

        # Create Lambda functions
        self.lambdas = self._create_lambda_functions()

        # Create Lambda invoke policy
        lambda_arns = [
            lambda_func.arn for lambda_func in self.lambdas.values()
        ]
        lambda_policy = self.create_lambda_invoke_policy(lambda_arns)

        # Attach Lambda policy to Step Function role
        lambda_policy_attachment = RolePolicyAttachment(
            f"{self.name}-lambda-policy-attachment",
            role=self.step_function_role.name,
            policy_arn=lambda_policy.arn,
            opts=ResourceOptions(parent=self),
        )

        # Add permissions for invoking other Step Functions
        self._add_step_function_permissions()

        # Create the state machine
        self.state_machine = self._create_state_machine()

    def _create_lambda_functions(self) -> dict:
        """Create Lambda functions for each state."""
        lambdas = {}

        # Lambda execution role
        lambda_role = Role(
            f"{self.name}-lambda-role",
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
        lambda_basic_policy = RolePolicyAttachment(
            f"{self.name}-lambda-basic-policy",
            role=lambda_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self),
        )

        # DynamoDB access policy
        dynamo_policy = RolePolicy(
            f"{self.name}-lambda-dynamo-policy",
            role=lambda_role.name,
            policy=dynamodb_table.arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:GetItem",
                                    "dynamodb:PutItem",
                                    "dynamodb:Query",
                                    "dynamodb:BatchWriteItem",
                                    "dynamodb:UpdateItem",
                                ],
                                "Resource": [arn, f"{arn}/index/*"],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Environment variables
        env_vars = {
            "DYNAMO_TABLE_NAME": dynamodb_table.name,
            "OPENAI_API_KEY": openai_api_key,
            "PINECONE_API_KEY": pinecone_api_key,
            "PINECONE_INDEX_NAME": pinecone_index_name,
            "PINECONE_HOST": pinecone_host,
        }

        # 1. Check Metadata Lambda
        lambdas["check_metadata"] = Function(
            f"{self.name}-check-metadata",
            role=lambda_role.arn,
            runtime="python3.12",
            handler="handler.lambda_handler",
            timeout=60,
            memory_size=256,
            architectures=["arm64"],
            code=AssetArchive(
                {
                    "handler.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "../lambda_functions/agent_labeling/check_metadata/handler.py",
                        )
                    ),
                }
            ),
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMO_TABLE_NAME": dynamodb_table.name,
                }
            ),
            layers=[dynamo_layer.arn],
            tags={"Component": self.name, "Function": "CheckMetadata"},
            opts=ResourceOptions(parent=self),
        )

        # 2. Check Embeddings Lambda
        lambdas["check_embeddings"] = Function(
            f"{self.name}-check-embeddings",
            role=lambda_role.arn,
            runtime="python3.12",
            handler="handler.lambda_handler",
            timeout=60,
            memory_size=256,
            architectures=["arm64"],
            code=AssetArchive(
                {
                    "handler.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "../lambda_functions/agent_labeling/check_embeddings/handler.py",
                        )
                    ),
                }
            ),
            environment=FunctionEnvironmentArgs(
                variables={
                    "PINECONE_API_KEY": pinecone_api_key,
                    "PINECONE_INDEX_NAME": pinecone_index_name,
                    "PINECONE_HOST": pinecone_host,
                }
            ),
            layers=[label_layer.arn],
            tags={"Component": self.name, "Function": "CheckEmbeddings"},
            opts=ResourceOptions(parent=self),
        )

        # 3. Run Agent Labeling Lambda
        lambdas["run_agent"] = Function(
            f"{self.name}-run-agent",
            role=lambda_role.arn,
            runtime="python3.12",
            handler="handler.lambda_handler",
            timeout=300,  # 5 minutes for full labeling
            memory_size=1024,  # More memory for pattern detection
            architectures=["arm64"],
            code=AssetArchive(
                {
                    "handler.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "../lambda_functions/agent_labeling/run_agent/handler.py",
                        )
                    ),
                }
            ),
            environment=FunctionEnvironmentArgs(variables=env_vars),
            layers=[dynamo_layer.arn, label_layer.arn],
            tags={"Component": self.name, "Function": "RunAgent"},
            opts=ResourceOptions(parent=self),
        )

        # 4. Prepare Batch Lambda
        lambdas["prepare_batch"] = Function(
            f"{self.name}-prepare-batch",
            role=lambda_role.arn,
            runtime="python3.12",
            handler="handler.lambda_handler",
            timeout=60,
            memory_size=256,
            architectures=["arm64"],
            code=AssetArchive(
                {
                    "handler.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "../lambda_functions/agent_labeling/prepare_batch/handler.py",
                        )
                    ),
                }
            ),
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMO_TABLE_NAME": dynamodb_table.name,
                }
            ),
            layers=[dynamo_layer.arn],
            tags={"Component": self.name, "Function": "PrepareBatch"},
            opts=ResourceOptions(parent=self),
        )

        # 5. Store Labels Lambda
        lambdas["store_labels"] = Function(
            f"{self.name}-store-labels",
            role=lambda_role.arn,
            runtime="python3.12",
            handler="handler.lambda_handler",
            timeout=60,
            memory_size=256,
            architectures=["arm64"],
            code=AssetArchive(
                {
                    "handler.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "../lambda_functions/agent_labeling/store_labels/handler.py",
                        )
                    ),
                }
            ),
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMO_TABLE_NAME": dynamodb_table.name,
                }
            ),
            layers=[dynamo_layer.arn],
            tags={"Component": self.name, "Function": "StoreLabels"},
            opts=ResourceOptions(parent=self),
        )

        return lambdas

    def _add_step_function_permissions(self) -> None:
        """Add permissions to invoke other Step Functions."""
        sfn_policy = RolePolicy(
            f"{self.name}-invoke-sfn-policy",
            role=self.step_function_role.name,
            policy=Output.all(
                merchant_arn=self.validate_merchant_sfn.state_machine.arn,
                batch_arn=self.word_label_sfn.state_machine.arn,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["states:StartExecution"],
                                "Resource": [
                                    args["merchant_arn"],
                                    args["batch_arn"],
                                ],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

    def _create_state_machine(self) -> StateMachine:
        """Create the agent labeling state machine."""
        definition = Output.all(
            check_metadata_arn=self.lambdas["check_metadata"].arn,
            check_embeddings_arn=self.lambdas["check_embeddings"].arn,
            run_agent_arn=self.lambdas["run_agent"].arn,
            prepare_batch_arn=self.lambdas["prepare_batch"].arn,
            store_labels_arn=self.lambdas["store_labels"].arn,
            merchant_sfn_arn=self.validate_merchant_sfn.state_machine.arn,
            batch_sfn_arn=self.word_label_sfn.state_machine.arn,
        ).apply(
            lambda args: {
                "Comment": "Agent-based receipt labeling with smart GPT integration",
                "StartAt": "CheckMetadata",
                "States": {
                    "CheckMetadata": {
                        "Type": "Task",
                        "Resource": args["check_metadata_arn"],
                        "ResultPath": "$.metadata",
                        "Next": "HasMetadata",
                    },
                    "HasMetadata": {
                        "Type": "Choice",
                        "Choices": [
                            {
                                "Variable": "$.metadata.found",
                                "BooleanEquals": True,
                                "Next": "CheckEmbeddings",
                            }
                        ],
                        "Default": "RunMerchantValidation",
                    },
                    "RunMerchantValidation": {
                        "Type": "Task",
                        "Resource": "arn:aws:states:::states:startExecution.sync:2",
                        "Parameters": {
                            "StateMachineArn": args["merchant_sfn_arn"],
                            "Input": {
                                "receipt_id.$": "$.receipt_id",
                            },
                        },
                        "ResultPath": "$.merchantValidation",
                        "Next": "CheckEmbeddings",
                    },
                    "CheckEmbeddings": {
                        "Type": "Task",
                        "Resource": args["check_embeddings_arn"],
                        "ResultPath": "$.embeddings",
                        "Next": "RunAgentLabeling",
                    },
                    "RunAgentLabeling": {
                        "Type": "Task",
                        "Resource": args["run_agent_arn"],
                        "ResultPath": "$.labeling",
                        "TimeoutSeconds": 300,
                        "Next": "CheckGPTDecision",
                    },
                    "CheckGPTDecision": {
                        "Type": "Choice",
                        "Choices": [
                            {
                                "Variable": "$.labeling.gpt_required",
                                "BooleanEquals": True,
                                "Next": "CheckBatchThreshold",
                            },
                        ],
                        "Default": "StoreLabels",
                    },
                    "CheckBatchThreshold": {
                        "Type": "Choice",
                        "Choices": [
                            {
                                "Variable": "$.labeling.unlabeled_count",
                                "NumericGreaterThan": 5,
                                "Next": "PrepareBatch",
                            },
                        ],
                        "Default": "StoreLabels",
                    },
                    "PrepareBatch": {
                        "Type": "Task",
                        "Resource": args["prepare_batch_arn"],
                        "ResultPath": "$.batch",
                        "Next": "QueueForBatchProcessing",
                    },
                    "QueueForBatchProcessing": {
                        "Type": "Task",
                        "Resource": "arn:aws:states:::states:startExecution",
                        "Parameters": {
                            "StateMachineArn": args["batch_sfn_arn"],
                            "Input": {
                                "batch_data.$": "$.batch",
                            },
                        },
                        "ResultPath": "$.batchExecution",
                        "Next": "WaitForBatchCompletion",
                    },
                    "WaitForBatchCompletion": {
                        "Type": "Wait",
                        "Seconds": 60,
                        "Next": "StoreLabels",
                    },
                    "StoreLabels": {
                        "Type": "Task",
                        "Resource": args["store_labels_arn"],
                        "ResultPath": "$.storage",
                        "Next": "CheckValidationRequired",
                    },
                    "CheckValidationRequired": {
                        "Type": "Choice",
                        "Choices": [
                            {
                                "Variable": "$.validate_labels",
                                "BooleanEquals": True,
                                "Next": "TriggerValidation",
                            }
                        ],
                        "Default": "Success",
                    },
                    "TriggerValidation": {
                        "Type": "Task",
                        "Resource": "arn:aws:states:::sns:publish",
                        "Parameters": {
                            "TopicArn": self.notification_system.validation_topic_arn,
                            "Message": {
                                "receipt_id.$": "$.receipt_id",
                                "action": "validate_labels",
                            },
                        },
                        "Next": "Success",
                    },
                    "Success": {
                        "Type": "Succeed",
                    },
                },
            }
        )

        return self.create_state_machine(definition)
