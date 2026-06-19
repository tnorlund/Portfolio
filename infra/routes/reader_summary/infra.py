import json
import os

import pulumi
import pulumi_aws as aws
from pulumi import AssetArchive, FileArchive

from dynamo_db import dynamodb_table

HANDLER_DIR = os.path.join(os.path.dirname(__file__), "handler")
ROUTE_NAME = os.path.basename(os.path.dirname(__file__))

lambda_role = aws.iam.Role(
    f"api_{ROUTE_NAME}_lambda_role",
    assume_role_policy="""{
        "Version": "2012-10-17",
        "Statement": [
            {
                "Action": "sts:AssumeRole",
                "Principal": {
                    "Service": "lambda.amazonaws.com"
                },
                "Effect": "Allow",
                "Sid": ""
            }
        ]
    }""",
)

lambda_policy = aws.iam.Policy(
    f"api_{ROUTE_NAME}_lambda_policy",
    description="IAM policy for reader summary Lambda to aggregate reads",
    policy=dynamodb_table.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:DescribeTable",
                            "dynamodb:GetItem",
                            "dynamodb:PutItem",
                            "dynamodb:UpdateItem",
                        ],
                        "Resource": arn,
                    }
                ],
            }
        )
    ),
)

aws.iam.RolePolicyAttachment(
    f"api_{ROUTE_NAME}_lambda_policy_attachment",
    role=lambda_role.name,
    policy_arn=lambda_policy.arn,
)

aws.iam.RolePolicyAttachment(
    f"api_{ROUTE_NAME}_lambda_basic_execution",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)

reader_summary_lambda = aws.lambda_.Function(
    f"api_{ROUTE_NAME}_POST_lambda",
    runtime="python3.12",
    architectures=["arm64"],
    role=lambda_role.arn,
    code=AssetArchive(
        {
            ".": FileArchive(HANDLER_DIR),
        }
    ),
    handler="index.handler",
    environment={
        "variables": {
            "READER_SUMMARY_TABLE_NAME": dynamodb_table.name,
            "MINIMUM_SAMPLE_SIZE": "5",
        }
    },
    memory_size=256,
    timeout=10,
    tags={"environment": pulumi.get_stack()},
)

log_group = aws.cloudwatch.LogGroup(
    f"api_{ROUTE_NAME}_lambda_log_group",
    name=pulumi.Output.concat("/aws/lambda/", reader_summary_lambda.name),
    retention_in_days=14,
)

pulumi.export(f"{ROUTE_NAME}_lambda_arn", reader_summary_lambda.arn)
