"""Pulumi resources for the process API Lambda."""

# pylint: disable=wrong-import-order

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
from raw_bucket import raw_bucket
from s3_website import site_bucket

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
                "IAM policy for '/process' route Lambda to query DynamoDB"
            ),
            document=pulumi.Output.all(
                dynamodb_table.arn,
                raw_bucket.arn,
                site_bucket.arn,
            ).apply(
                lambda arns: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:Query",
                                    "dynamodb:DescribeTable",
                                    "dynamodb:PutItem",
                                    "dynamodb:BatchWriteItem",
                                ],
                                "Resource": [
                                    arns[0],
                                    f"{arns[0]}/index/GSI1",
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:PutObject",
                                    "s3:GetObject",
                                    "s3:HeadObject",
                                    "s3:ListBucket",
                                ],
                                "Resource": [arns[1], f"{arns[1]}/*"],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:PutObject",
                                    "s3:GetObject",
                                    "s3:HeadObject",
                                    "s3:ListBucket",
                                ],
                                "Resource": [arns[2], f"{arns[2]}/*"],
                            },
                        ],
                    }
                )
            ),
        ),
        environment={"DYNAMODB_TABLE_NAME": DYNAMODB_TABLE_NAME},
        layers=(dynamo_layer.arn,),
        memory_size=3072,
        timeout=300,
    )
)

lambda_role = resources.role
lambda_policy = resources.policy
lambda_role_policy_attachment = resources.policy_attachment
process_lambda = resources.function
log_group = resources.log_group
