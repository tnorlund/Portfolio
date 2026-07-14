"""Pulumi resources for the label-validation-count API Lambda."""

import json
import os

import pulumi

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
is_production = pulumi.get_stack() == "prod"

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
                "IAM policy for label validation count Lambda to query "
                "DynamoDB"
            ),
            document=dynamodb_table.arn.apply(
                lambda table_arn: json.dumps(
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
                                    table_arn,
                                    f"{table_arn}/index/GSI1",
                                    f"{table_arn}/index/GSITYPE",
                                ],
                            }
                        ],
                    }
                )
            ),
        ),
        environment={"DYNAMODB_TABLE_NAME": DYNAMODB_TABLE_NAME},
        layers=(dynamo_layer.arn,),
        memory_size=1536 if is_production else 512,
        timeout=30,
        reserved_concurrent_executions=100 if is_production else None,
        log_retention_in_days=7 if is_production else 3,
    )
)

lambda_role = resources.role
lambda_policy = resources.policy
lambda_role_policy_attachment = resources.policy_attachment
label_validation_count_lambda = resources.function
log_group = resources.log_group

pulumi.export(f"{ROUTE_NAME}_lambda_arn", label_validation_count_lambda.arn)
pulumi.export(f"{ROUTE_NAME}_lambda_name", label_validation_count_lambda.name)
