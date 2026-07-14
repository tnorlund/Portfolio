"""Pulumi resources for the images API Lambda."""

import json
import os

from dynamo_db import dynamodb_table
from infra.components.lambda_layer import dynamo_layer
from infra.components.route_lambda import (
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
            description="IAM policy for Lambda to access DynamoDB",
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
                                "Resource": [arn, f"{arn}/index/GSI3"],
                            }
                        ],
                    }
                )
            ),
        ),
        environment={"DYNAMODB_TABLE_NAME": DYNAMODB_TABLE_NAME},
        layers=(dynamo_layer.arn,),
        memory_size=1024,
        timeout=30,
    )
)

lambda_role = resources.role
lambda_policy = resources.policy
lambda_role_policy_attachment = resources.policy_attachment
images_lambda = resources.function
log_group = resources.log_group
