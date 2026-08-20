"""Pulumi resources for the receipts API Lambda."""

import json
import os

from dynamo_db import dynamodb_table
from infra.components.route_lambda import (
    API_DYNAMO_ASSET_PATH,
    ManagedPolicyDefinition,
    RouteLambdaDefinition,
    create_route_lambda,
)

HANDLER_DIR = os.path.join(os.path.dirname(__file__), "handler")
ROUTE_NAME = os.path.basename(os.path.dirname(__file__))
DYNAMODB_TABLE_NAME = dynamodb_table.name

resources = create_route_lambda(
    RouteLambdaDefinition(
        role_name=f"api_{ROUTE_NAME}_lambda_role",
        basic_execution_attachment_name=(
            f"api_{ROUTE_NAME}_lambda_basic_execution"
        ),
        function_name=f"api_{ROUTE_NAME}_GET_lambda",
        log_group_name=f"api_{ROUTE_NAME}_lambda_log_group",
        handler_directory=HANDLER_DIR,
        policy=ManagedPolicyDefinition(
            resource_name=f"api_{ROUTE_NAME}_lambda_policy",
            attachment_name=f"api_{ROUTE_NAME}_lambda_policy_attachment",
            description=(
                "IAM policy for '/images' route Lambda to query DynamoDB"
            ),
            document=dynamodb_table.arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:Query",
                                    "dynamodb:DescribeTable",
                                ],
                                "Resource": [
                                    arn,
                                    f"{arn}/index/GSITYPE",
                                ],
                            }
                        ],
                    }
                )
            ),
        ),
        environment={"DYNAMODB_TABLE_NAME": DYNAMODB_TABLE_NAME},
        memory_size=1024,
        timeout=120,
        enable_dev_profiling=True,
        extra_code_assets={"_api_dynamo.py": API_DYNAMO_ASSET_PATH},
    )
)

lambda_role = resources.role
lambda_policy = resources.policy
lambda_role_policy_attachment = resources.policy_attachment
receipts_lambda = resources.function
log_group = resources.log_group
