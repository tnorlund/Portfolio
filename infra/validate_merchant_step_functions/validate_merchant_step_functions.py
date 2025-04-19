import os
import json
from pulumi import (
    ComponentResource,
    Output,
    ResourceOptions,
    Config,
    FileAsset,
    AssetArchive,
)
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment
from pulumi_aws.lambda_ import Function, FunctionEnvironmentArgs
from pulumi_aws.sfn import StateMachine


from dynamo_db import dynamodb_table
from lambda_layer import dynamo_layer, label_layer

config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")
pinecone_api_key = config.require_secret("PINECONE_API_KEY")
pinecone_index_name = config.require("PINECONE_INDEX_NAME")
pinecone_host = config.require("PINECONE_HOST")
google_places_api_key = config.require_secret("GOOGLE_PLACES_API_KEY")


class ValidateMerchantStepFunctions(ComponentResource):
    def __init__(self, name: str, opts: ResourceOptions = None):
        super().__init__(f"{__name__}-{name}", "aws:stepfunctions:StateMachine", opts)
        stack = "dev"

        # Define IAM role for Step Function
        sfn_role = Role(
            f"{name}-merchant-sfn-role",
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
            f"{name}-lambda-role",
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
            f"{name}-lambda-basic-execution",
            role=lambda_exec_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )

        # Define Lambda: list_receipts
        list_receipts_lambda = Function(
            f"{name}-list-receipts",
            role=lambda_exec_role.arn,
            runtime="python3.12",
            handler="list_receipts_handler.list_handler",
            code=AssetArchive(
                {
                    "list_receipts_handler.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__), "list_receipts_handler.py"
                        )
                    )
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
            f"{name}-validate-receipt",
            role=lambda_exec_role.arn,
            runtime="python3.12",
            handler="validate_single_receipt_handler.validate_handler",
            code=AssetArchive(
                {
                    "validate_single_receipt_handler.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__),
                            "validate_single_receipt_handler.py",
                        )
                    )
                }
            ),
            timeout=900,
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
            f"{name}-invoke-policy",
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
            f"{name}-lambda-dynamo-policy",
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
                                "Resource": f"arn:aws:dynamodb:*:*:table/{table_name}*",
                            }
                        ],
                    }
                )
            ),
        )

        # Step Function definition
        StateMachine(
            f"{name}-merchant-validation-sm",
            role_arn=sfn_role.arn,
            definition=Output.all(
                list_receipts_lambda.arn, validate_receipt_lambda.arn
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
                                "MaxConcurrency": 25,
                                "Parameters": {
                                    "image_id.$": "$$.Map.Item.Value.image_id",
                                    "receipt_id.$": "$$.Map.Item.Value.receipt_id",
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
                                "End": True,
                            },
                        },
                    }
                ),
            ),
            opts=ResourceOptions(parent=self),
        )
