"""LayoutLM epochs API Lambda - serves per-epoch evaluation results from S3.

Mirrors the word_similarity route: a small GET Lambda that reads the
``epoch-eval/`` artifacts produced by the eval-checkpoints Processing job. The
Lambda is wired into API Gateway from __main__.py after the bucket is known.
"""

import json
import os

import pulumi
import pulumi_aws as aws
from pulumi import AssetArchive, FileArchive, Input, Output

HANDLER_DIR = os.path.join(os.path.dirname(__file__), "handler")
ROUTE_NAME = os.path.basename(os.path.dirname(__file__))

stack = pulumi.get_stack()


def create_layoutlm_epochs_lambda(
    cache_bucket_name: Input[str],
) -> aws.lambda_.Function:
    """Create the LayoutLM epochs API Lambda.

    Args:
        cache_bucket_name: S3 bucket holding ``epoch-eval/`` artifacts (the
            LayoutLM training/output bucket).

    Returns:
        The Lambda function resource.
    """
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

    s3_policy = aws.iam.Policy(
        f"api_{ROUTE_NAME}_s3_policy",
        description="Allow Lambda to read LayoutLM epoch-eval artifacts",
        policy=Output.from_input(cache_bucket_name).apply(
            lambda bucket: json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": ["s3:GetObject", "s3:ListBucket"],
                            "Resource": [
                                f"arn:aws:s3:::{bucket}/*",
                                f"arn:aws:s3:::{bucket}",
                            ],
                        }
                    ],
                }
            )
        ),
    )

    aws.iam.RolePolicyAttachment(
        f"api_{ROUTE_NAME}_s3_policy_attachment",
        role=lambda_role.name,
        policy_arn=s3_policy.arn,
    )
    aws.iam.RolePolicyAttachment(
        f"api_{ROUTE_NAME}_basic_execution",
        role=lambda_role.name,
        policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )

    epochs_lambda = aws.lambda_.Function(
        f"api_{ROUTE_NAME}_GET_lambda",
        runtime="python3.12",
        architectures=["arm64"],
        role=lambda_role.arn,
        code=AssetArchive({".": FileArchive(HANDLER_DIR)}),
        handler="index.handler",
        environment={
            "variables": {
                "S3_CACHE_BUCKET": Output.from_input(cache_bucket_name),
            }
        },
        memory_size=512,
        timeout=30,
        tags={"environment": stack},
    )

    aws.cloudwatch.LogGroup(
        f"api_{ROUTE_NAME}_lambda_log_group",
        name=epochs_lambda.name.apply(lambda fn: f"/aws/lambda/{fn}"),
        retention_in_days=30,
    )

    return epochs_lambda


# Set by __main__.py after the Lambda is created (for api_gateway wiring).
layoutlm_epochs_lambda: aws.lambda_.Function | None = None
