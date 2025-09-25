"""Task 7: Workers - Lambda Functions for ChromaDB Queries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import (
    ComponentResource,
    ResourceOptions,
    Output,
    AssetArchive,
    FileArchive,
)

from chroma.base import dynamo_layer, label_layer


class ChromaWorkers(ComponentResource):
    def __init__(
        self,
        name: str,
        *,
        vpc_id: pulumi.Input[str],  # unused, kept for signature parity
        subnets: pulumi.Input[list[str]],
        security_group_id: pulumi.Input[str],
        dynamodb_table_name: pulumi.Input[str],
        chroma_service_dns: pulumi.Input[str],
        base_image_ref: Optional[
            pulumi.Input[str]
        ] = None,  # unused for zip mode
        opts: Optional[ResourceOptions] = None,
    ) -> None:
        super().__init__("custom:chroma:Workers", name, None, opts)

        # Touch the param to avoid linter warning; zip mode ignores this
        _ignore_base = base_image_ref

        # Zip-based worker Lambda: prepare handler directory
        repo_root = Path(__file__).resolve().parents[2]
        handler_dir = repo_root / "infra" / "chroma" / "workers_handler"

        # IAM role
        self.role = aws.iam.Role(
            f"{name}-role",
            assume_role_policy='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}',
            opts=ResourceOptions(parent=self),
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-basic-exec",
            role=self.role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self),
        )

        # Required for VPC-enabled Lambdas to manage ENIs
        aws.iam.RolePolicyAttachment(
            f"{name}-vpc-access",
            role=self.role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
            opts=ResourceOptions(parent=self),
        )

        # DDB read policy
        # Create IAM policy allowing DynamoDB read operations
        aws.iam.RolePolicy(
            f"{name}-ddb-read",
            role=self.role.name,
            policy=Output.all(dynamodb_table_name)
            .apply(
                lambda args: {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "dynamodb:GetItem",
                                "dynamodb:BatchGetItem",
                                "dynamodb:DescribeTable",
                                "dynamodb:Query",
                            ],
                            "Resource": [
                                f"arn:aws:dynamodb:{aws.config.region}:{aws.get_caller_identity().account_id}:table/{args[0]}",
                                f"arn:aws:dynamodb:{aws.config.region}:{aws.get_caller_identity().account_id}:table/{args[0]}/index/*",
                            ],
                        }
                    ],
                }
            )
            .apply(json.dumps),
            opts=ResourceOptions(parent=self),
        )

        # Lambda function that queries Chroma by IDs (zip-based)
        self.query_words = aws.lambda_.Function(
            f"{name}-query-words",
            runtime="python3.12",
            architectures=["arm64"],
            role=self.role.arn,
            code=AssetArchive(
                {
                    ".": FileArchive(str(handler_dir)),
                }
            ),
            handler="index.lambda_handler",
            layers=[dynamo_layer.arn, label_layer.arn],
            timeout=60,
            memory_size=512,
            environment={
                "variables": {
                    "CHROMA_HTTP_ENDPOINT": chroma_service_dns,
                    "DYNAMO_TABLE_NAME": dynamodb_table_name,
                }
            },
            vpc_config=aws.lambda_.FunctionVpcConfigArgs(
                security_group_ids=[security_group_id], subnet_ids=subnets
            ),
            opts=ResourceOptions(parent=self),
        )

        self.register_outputs(
            {
                "query_words_arn": self.query_words.arn,
            }
        )
