import json
import os

import pulumi
import pulumi_aws as aws
from dynamo_db import dynamodb_table
from infra.components.lambda_layer import dynamo_layer
from pulumi import AssetArchive, FileArchive

HANDLER_DIR = os.path.join(os.path.dirname(__file__), "handler")
ROUTE_NAME = os.path.basename(os.path.dirname(__file__))
DYNAMODB_TABLE_NAME = dynamodb_table.name

lambda_role = aws.iam.Role(
    f"api_{ROUTE_NAME}_lambda_role",
    assume_role_policy="""{
        "Version": "2012-10-17",
        "Statement": [
            {
                "Action": "sts:AssumeRole",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Effect": "Allow",
                "Sid": ""
            }
        ]
    }""",
)

lambda_policy = aws.iam.Policy(
    f"api_{ROUTE_NAME}_lambda_policy",
    description="IAM policy for Lambda to access DynamoDB",
    policy=dynamodb_table.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["dynamodb:Query", "dynamodb:DescribeTable"],
                        "Resource": [arn, f"{arn}/index/GSI3"],
                    }
                ],
            }
        )
    ),
)

lambda_role_policy_attachment = aws.iam.RolePolicyAttachment(
    f"api_{ROUTE_NAME}_lambda_policy_attachment",
    role=lambda_role.name,
    policy_arn=lambda_policy.arn,
)

aws.iam.RolePolicyAttachment(
    f"api_{ROUTE_NAME}_lambda_basic_execution",
    role=lambda_role.name,
    policy_arn=("arn:aws:iam::aws:policy/service-role/" "AWSLambdaBasicExecutionRole"),
)

images_lambda = aws.lambda_.Function(
    f"api_{ROUTE_NAME}_GET_lambda",
    runtime="python3.12",
    architectures=["arm64"],
    role=lambda_role.arn,
    code=AssetArchive({".": FileArchive(HANDLER_DIR)}),
    handler="index.handler",
    layers=[dynamo_layer.arn],
    environment={"variables": {"DYNAMODB_TABLE_NAME": DYNAMODB_TABLE_NAME}},
    memory_size=1024,
    timeout=30,
    tags={"environment": pulumi.get_stack()},
)

log_group = aws.cloudwatch.LogGroup(
    f"api_{ROUTE_NAME}_lambda_log_group",
    retention_in_days=30,
)
