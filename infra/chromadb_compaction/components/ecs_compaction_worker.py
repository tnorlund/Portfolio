"""ECS Fargate worker that creates embeddings from NDJSON artifacts.

Consumes EMBED_NDJSON_QUEUE to process receipt data exported to NDJSON format,
creates embeddings, uploads delta files to S3, and writes CompactionRun records
to DynamoDB. The enhanced_compaction_handler Lambda then merges those deltas.
"""

from __future__ import annotations

from pathlib import Path
import os
import json
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


def _build_task_policy_doc(args: list[str]) -> str:
    (
        lines_queue_arn,
        words_queue_arn,
        dynamo_table_name,
        chroma_bucket_arn,
        artifacts_bucket_arn,
    ) = args
    statements = [
        {
            "Effect": "Allow",
            "Action": [
                "sqs:ReceiveMessage",
                "sqs:DeleteMessage",
                "sqs:ChangeMessageVisibility",
                "sqs:GetQueueAttributes",
                "sqs:GetQueueUrl",
            ],
            "Resource": [lines_queue_arn, words_queue_arn],
        },
        {
            "Effect": "Allow",
            "Action": [
                "dynamodb:DescribeTable",
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
                "dynamodb:BatchWriteItem",
                "dynamodb:Query",
            ],
            "Resource": f"arn:aws:dynamodb:*:*:table/{dynamo_table_name}*",
        },
        {
            "Effect": "Allow",
            "Action": [
                "s3:PutObject",
                "s3:AbortMultipartUpload",
                "s3:CreateMultipartUpload",
                "s3:ListMultipartUploadParts",
            ],
            "Resource": [chroma_bucket_arn, f"{chroma_bucket_arn}/*"],
        },
    ]
    if artifacts_bucket_arn:
        statements.append(
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:HeadObject"],
                "Resource": [f"{artifacts_bucket_arn}/*"],
            }
        )
    return json.dumps({"Version": "2012-10-17", "Statement": statements})


class ChromaCompactionWorker(ComponentResource):
    """
    ECS Fargate worker for creating embeddings from NDJSON artifacts.

    This worker ONLY consumes from EMBED_NDJSON_QUEUE. It does NOT need
    access to LINES_QUEUE or WORDS_QUEUE (those are consumed by
    enhanced_compaction_handler Lambda).
    """

    def __init__(
        self,
        name: str,
        *,
        private_subnet_ids: pulumi.Input[list[str]],
        security_group_id: pulumi.Input[str],
        task_role_arn: pulumi.Input[str],
        task_role_name: pulumi.Input[str],
        execution_role_arn: pulumi.Input[str],
        efs_file_system_id: pulumi.Input[str],
        efs_access_point_id: pulumi.Input[str],
        dynamodb_table_name: pulumi.Input[str],
        chromadb_bucket_name: pulumi.Input[str],
        chromadb_bucket_arn: pulumi.Input[str],
        embed_ndjson_queue_url: pulumi.Input[
            str
        ],  # REQUIRED - worker needs this
        embed_ndjson_queue_arn: pulumi.Input[
            str
        ],  # REQUIRED - for IAM permissions
        artifacts_bucket_arn: Optional[pulumi.Input[str]] = None,
        base_image_ref: Optional[pulumi.Input[str]] = None,
        cpu: int = 512,
        memory: int = 1024,
        desired_count: int = 1,
        opts: Optional[ResourceOptions] = None,
    ) -> None:
        super().__init__("custom:chroma:EcsCompactionWorker", name, None, opts)

        stack = pulumi.get_stack()

        # ECR repository for worker image
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
        # Mirror working pattern: use repo root as context and point Dockerfile via absolute path
        repo_root = Path(__file__).resolve().parents[3]
        dockerfile_path = (
            repo_root
            / "infra"
            / "chromadb_compaction"
            / "worker"
            / "Dockerfile"
        )

        # Resolve base image: prefer explicit, then env, then minimal fallback
        resolved_base_image = (
            base_image_ref
            or os.environ.get("LABEL_BASE_IMAGE")
            or "public.ecr.aws/docker/library/python:3.12-slim"
        )

        self.image = docker_build.Image(
            f"{name}-image",
            context=docker_build.BuildContextArgs(location=str(repo_root)),
            dockerfile=docker_build.DockerfileArgs(
                location=str(dockerfile_path)
            ),
            platforms=[docker_build.Platform.LINUX_ARM64],
            build_args={
                "BASE_IMAGE": resolved_base_image,
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
            tags=[self.repo.repository_url.apply(lambda u: f"{u}:latest")],
            opts=ResourceOptions(parent=self, depends_on=[self.repo]),
        )

        # ECS cluster dedicated to worker
        self.cluster = aws.ecs.Cluster(
            f"{name}-cluster",
            settings=[
                aws.ecs.ClusterSettingArgs(
                    name="containerInsights", value="enabled"
                )
            ],
            tags={"Environment": stack, "ManagedBy": "Pulumi"},
            opts=ResourceOptions(parent=self),
        )

        # Log group
        self.lg = aws.cloudwatch.LogGroup(
            f"{name}-logs",
            retention_in_days=14,
            opts=ResourceOptions(parent=self),
        )

        # Attach SQS, DynamoDB, and S3 permissions to task role
        # Worker only needs access to EMBED_NDJSON_QUEUE (not LINES/WORDS queues)
        policy_inputs = [
            dynamodb_table_name,
            chromadb_bucket_arn,
            embed_ndjson_queue_arn,  # Required - no conditional
        ]

        # Add artifacts bucket if provided
        if artifacts_bucket_arn is not None:
            policy_inputs.append(artifacts_bucket_arn)

        aws.iam.RolePolicy(
            f"{name}-task-iam",
            role=task_role_name,
            policy=Output.all(*policy_inputs).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            # Grant access to EMBED_NDJSON_QUEUE (required)
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "sqs:ReceiveMessage",
                                    "sqs:DeleteMessage",
                                    "sqs:ChangeMessageVisibility",
                                    "sqs:GetQueueAttributes",
                                    "sqs:GetQueueUrl",
                                ],
                                "Resource": [
                                    args[2]
                                ],  # embed_ndjson_queue_arn
                            },
                            # DynamoDB access
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:DescribeTable",
                                    "dynamodb:GetItem",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:BatchWriteItem",
                                    "dynamodb:Query",
                                ],
                                "Resource": f"arn:aws:dynamodb:*:*:table/{args[0]}*",
                            },
                            # ChromaDB S3 bucket - read/write for deltas and snapshots
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:PutObject",
                                    "s3:AbortMultipartUpload",
                                    "s3:CreateMultipartUpload",
                                    "s3:ListMultipartUploadParts",
                                    "s3:GetObject",
                                    "s3:HeadObject",
                                ],
                                "Resource": [f"{args[1]}/*"],
                            },
                            {
                                "Effect": "Allow",
                                "Action": ["s3:ListBucket"],
                                "Resource": f"{args[1]}",
                            },
                            # Artifacts bucket - read-only (if provided)
                            *(
                                [
                                    {
                                        "Effect": "Allow",
                                        "Action": [
                                            "s3:GetObject",
                                            "s3:HeadObject",
                                        ],
                                        "Resource": [f"{args[3]}/*"],
                                    }
                                ]
                                if len(args) > 3
                                and artifacts_bucket_arn is not None
                                else []
                            ),
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Define container with EFS mount and environment
        # Worker only consumes from EMBED_NDJSON_QUEUE (required parameter)
        container_def = Output.all(
            self.repo.repository_url,
            self.image.tags[0],
            dynamodb_table_name,
            chromadb_bucket_name,
            embed_ndjson_queue_url,  # Required - no conditional
        ).apply(
            lambda args: [
                {
                    "name": name,
                    "image": f"{args[0]}:latest",
                    "essential": True,
                    "environment": [
                        {"name": "DYNAMODB_TABLE_NAME", "value": args[2]},
                        {"name": "CHROMA_ROOT", "value": "/mnt/chroma"},
                        {"name": "CHROMADB_BUCKET", "value": args[3]},
                        {"name": "DIRECT_CHROMA_WRITE", "value": "false"},
                        {"name": "EMBED_NDJSON_QUEUE_URL", "value": args[4]},
                        {"name": "LOG_LEVEL", "value": "INFO"},
                    ],
                    "mountPoints": [
                        {
                            "sourceVolume": "chroma-efs",
                            "containerPath": "/mnt/chroma",
                            "readOnly": False,
                        }
                    ],
                    "logConfiguration": {
                        "logDriver": "awslogs",
                        "options": {
                            "awslogs-group": self.lg.name,
                            "awslogs-region": aws.config.region,
                            "awslogs-stream-prefix": name,
                        },
                    },
                }
            ]
        )

        # Task definition with EFS volume
        self.task = aws.ecs.TaskDefinition(
            f"{name}-task",
            family=f"{name}-task",
            cpu=str(cpu),
            memory=str(memory),
            network_mode="awsvpc",
            requires_compatibilities=["FARGATE"],
            execution_role_arn=execution_role_arn,
            task_role_arn=task_role_arn,
            runtime_platform=aws.ecs.TaskDefinitionRuntimePlatformArgs(
                cpu_architecture="ARM64",
                operating_system_family="LINUX",
            ),
            volumes=[
                aws.ecs.TaskDefinitionVolumeArgs(
                    name="chroma-efs",
                    efs_volume_configuration=aws.ecs.TaskDefinitionVolumeEfsVolumeConfigurationArgs(
                        file_system_id=efs_file_system_id,
                        transit_encryption="ENABLED",
                        authorization_config=aws.ecs.TaskDefinitionVolumeEfsVolumeConfigurationAuthorizationConfigArgs(
                            access_point_id=efs_access_point_id,
                            iam="DISABLED",
                        ),
                    ),
                )
            ],
            container_definitions=container_def.apply(
                lambda d: pulumi.Output.secret(pulumi.Output.json_dumps(d))
            ),
            opts=ResourceOptions(parent=self),
        )

        # Service in private subnets, no public IP
        self.svc = aws.ecs.Service(
            f"{name}-svc",
            cluster=self.cluster.arn,
            task_definition=self.task.arn,
            desired_count=desired_count,
            launch_type="FARGATE",
            network_configuration=aws.ecs.ServiceNetworkConfigurationArgs(
                subnets=private_subnet_ids,
                security_groups=[security_group_id],
                assign_public_ip=False,
            ),
            opts=ResourceOptions(parent=self),
        )

        self.register_outputs(
            {
                "cluster_arn": self.cluster.arn,
                "service_arn": self.svc.arn,
                "task_arn": self.task.arn,
                "repository_url": self.repo.repository_url,
            }
        )
