"""Pulumi resources for the reader-summary API Lambda."""

import json
import os

import pulumi

from dynamo_db import dynamodb_table
from infra.components.route_lambda import (
    ManagedPolicyDefinition,
    RouteLambdaDefinition,
    create_route_lambda,
)

HANDLER_DIR = os.path.join(os.path.dirname(__file__), "handler")
ROUTE_NAME = os.path.basename(os.path.dirname(__file__))
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "https://tylernorlund.com",
    "https://www.tylernorlund.com",
    "https://dev.tylernorlund.com",
]

resources = create_route_lambda(
    RouteLambdaDefinition(
        role_name=f"api_{ROUTE_NAME}_lambda_role",
        basic_execution_attachment_name=(
            f"api_{ROUTE_NAME}_lambda_basic_execution"
        ),
        function_name=f"api_{ROUTE_NAME}_POST_lambda",
        log_group_name=f"api_{ROUTE_NAME}_lambda_log_group",
        handler_directory=HANDLER_DIR,
        policy=ManagedPolicyDefinition(
            resource_name=f"api_{ROUTE_NAME}_lambda_policy",
            attachment_name=f"api_{ROUTE_NAME}_lambda_policy_attachment",
            description=(
                "IAM policy for reader summary Lambda to aggregate reads"
            ),
            document=dynamodb_table.arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": "dynamodb:DescribeTable",
                                "Resource": arn,
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:DeleteItem",
                                    "dynamodb:GetItem",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                ],
                                "Resource": arn,
                                "Condition": {
                                    "ForAllValues:StringLike": {
                                        "dynamodb:LeadingKeys": [
                                            "READER_SUMMARY#*"
                                        ]
                                    }
                                },
                            },
                        ],
                    }
                )
            ),
        ),
        environment={
            "READER_SUMMARY_TABLE_NAME": dynamodb_table.name,
            "MINIMUM_SAMPLE_SIZE": "5",
            "ALLOWED_ORIGINS": ",".join(ALLOWED_ORIGINS),
        },
        memory_size=256,
        timeout=10,
        log_retention_in_days=14,
        use_function_log_group_name=True,
    )
)

lambda_role = resources.role
lambda_policy = resources.policy
reader_summary_lambda = resources.function
log_group = resources.log_group

pulumi.export(f"{ROUTE_NAME}_lambda_arn", reader_summary_lambda.arn)
