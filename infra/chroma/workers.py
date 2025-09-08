"""Task 7: Workers - Lambda Functions for ChromaDB Queries."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, ResourceOptions, Output
from pulumi_aws.ecr import (
    Repository,
    RepositoryImageScanningConfigurationArgs,
    get_authorization_token_output,
)
import pulumi_docker_build as docker_build


class ChromaWorkers(ComponentResource):
    def __init__(
        self,
        name: str,
        *,
        vpc_id: pulumi.Input[str],
        subnets: pulumi.Input[list[str]],
        security_group_id: pulumi.Input[str],
        dynamodb_table_name: pulumi.Input[str],
        chroma_service_dns: pulumi.Input[str],
        base_image_ref: Optional[pulumi.Input[str]] = None,
        opts: Optional[ResourceOptions] = None,
    ) -> None:
        super().__init__("custom:chroma:Workers", name, None, opts)

        stack = pulumi.get_stack()

        # ECR repo and image for worker Lambda
        self.repo = Repository(
            f"{name}-repo",
            image_scanning_configuration=RepositoryImageScanningConfigurationArgs(
                scan_on_push=True
            ),
            force_delete=True,
            tags={"Environment": stack, "ManagedBy": "Pulumi"},
            opts=ResourceOptions(parent=self),
        )

        ecr_auth = get_authorization_token_output()

        repo_root = Path(__file__).resolve().parents[2]
        lambdas_dir = repo_root / "infra" / "chroma" / "lambdas"

        # Reuse the same minimal context and Dockerfile as the server, but override CMD via env
        self.image = docker_build.Image(
            f"{name}-image",
            context=docker_build.BuildContextArgs(location=str(lambdas_dir)),
            dockerfile=docker_build.DockerfileArgs(
                location=str(lambdas_dir / "Dockerfile")
            ),
            platforms=["linux/arm64"],
            build_args={
                "BASE_IMAGE": base_image_ref or "chromadb/chroma:latest",
            },
            push=True,
            registries=[
                {
                    "address": self.repo.repository_url.apply(
                        lambda u: u.split("/")[0]
                    ),
                    "username": ecr_auth.user_name,
                    "password": ecr_auth.password,
                }
            ],
            tags=[self.repo.repository_url.apply(lambda u: f"{u}:workers")],
            opts=ResourceOptions(parent=self, depends_on=[self.repo]),
        )

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
        aws.iam.RolePolicy(
            f"{name}-ddb-read",
            role=self.role.name,
            policy=Output.all(dynamodb_table_name).apply(
                lambda args: (
                    '{"Version":"2012-10-17","Statement":[{'
                    '"Effect":"Allow","Action":["dynamodb:GetItem","dynamodb:BatchGetItem"],'
                    f'"Resource":["arn:aws:dynamodb:{aws.config.region}:{aws.get_caller_identity().account_id}:table/{args[0]}","arn:aws:dynamodb:{aws.config.region}:{aws.get_caller_identity().account_id}:table/{args[0]}/index/*"]'  # noqa: E501
                    "}]}"
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Lambda function that queries Chroma by IDs
        self.query_words = aws.lambda_.Function(
            f"{name}-query-words",
            package_type="Image",
            image_uri=Output.all(
                self.repo.repository_url, self.image.digest
            ).apply(lambda args: f"{args[0].split(':')[0]}@{args[1]}"),
            role=self.role.arn,
            timeout=60,
            memory_size=512,
            architectures=["arm64"],
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
                "image_repo": self.repo.repository_url,
                "query_words_arn": self.query_words.arn,
            }
        )
