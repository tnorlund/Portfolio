"""Pulumi component for Chroma ECS service (scale-to-zero)."""

from __future__ import annotations

from typing import Optional
from pathlib import Path

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, ResourceOptions, Output
from pulumi_aws.ecr import (
    Repository,
    RepositoryImageScanningConfigurationArgs,
)

# Import the CodeBuildDockerImage component
from codebuild_docker_image import CodeBuildDockerImage


class ChromaEcsService(ComponentResource):
    def __init__(
        self,
        name: str,
        *,
        vpc_id: pulumi.Input[str],
        public_subnet_ids: pulumi.Input[list[str]],
        security_group_id: pulumi.Input[str],
        task_role_arn: pulumi.Input[str],
        task_role_name: pulumi.Input[str],
        execution_role_arn: pulumi.Input[str],
        chromadb_bucket_name: pulumi.Input[str],
        collection: str = "words",
        cpu: int = 1024,
        memory: int = 2048,
        port: int = 8000,
        desired_count: int = 0,
        opts: Optional[ResourceOptions] = None,
    ) -> None:
        super().__init__("custom:chroma:EcsService", name, None, opts)

        stack = pulumi.get_stack()

        # Use CodeBuildDockerImage for AWS-based builds
        # Note: ECS doesn't need Lambda config
        self.docker_image = CodeBuildDockerImage(
            f"{name}-image",
            dockerfile_path="infra/chroma/lambdas/Dockerfile",
            build_context_path="infra/chroma/lambdas",  # Use local context for Chroma
            source_paths=None,  # Use default rsync with exclusions
            lambda_function_name=None,  # Not a Lambda function
            lambda_config=None,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self),
        )

        # Export ECR repository and image
        self.repo = self.docker_image.ecr_repo
        self.image = self.docker_image.docker_image

        # ECS cluster
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

        # Cloud Map namespace
        self.ns = aws.servicediscovery.PrivateDnsNamespace(
            f"{name}-ns",
            name=f"{name}.svc.local",
            vpc=vpc_id,
            description="Chroma service discovery",
            opts=ResourceOptions(parent=self),
        )

        self.sd_service = aws.servicediscovery.Service(
            f"{name}-sd",
            name=f"{name}",
            dns_config=aws.servicediscovery.ServiceDnsConfigArgs(
                namespace_id=self.ns.id,
                dns_records=[
                    aws.servicediscovery.ServiceDnsConfigDnsRecordArgs(
                        ttl=10,
                        type="A",
                    )
                ],
                routing_policy="MULTIVALUE",
            ),
            health_check_custom_config=aws.servicediscovery.ServiceHealthCheckCustomConfigArgs(
                failure_threshold=1
            ),
            opts=ResourceOptions(parent=self),
        )

        # Task execution role provided by caller
        exec_role_arn = execution_role_arn

        # Grant task S3 read
        aws.iam.RolePolicy(
            f"{name}-s3-read",
            role=task_role_name,
            policy=Output.all(chromadb_bucket_name).apply(
                lambda args: (
                    '{"Version":"2012-10-17","Statement":[{'
                    '"Effect":"Allow","Action":["s3:GetObject","s3:ListBucket"],'
                    f'"Resource":["arn:aws:s3:::{args[0]}","arn:aws:s3:::{args[0]}/*"]'  # noqa: E501
                    "}]}"
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Log group
        self.lg = aws.cloudwatch.LogGroup(
            f"{name}-logs",
            retention_in_days=14,
            opts=ResourceOptions(parent=self),
        )

        # Task definition
        container_def = Output.all(
            self.repo.repository_url, chromadb_bucket_name
        ).apply(
            lambda args: [
                {
                    "name": name,
                    "image": f"{args[0]}:latest",
                    "portMappings": [
                        {"containerPort": port, "hostPort": port}
                    ],
                    "environment": [
                        {"name": "CHROMADB_BUCKET", "value": args[1]},
                        {"name": "CHROMA_COLLECTION", "value": collection},
                        {"name": "CHROMA_SERVER_HOST", "value": "0.0.0.0"},
                        {
                            "name": "CHROMA_SERVER_HTTP_PORT",
                            "value": str(port),
                        },
                        # Provide parity with Lambda container envs when base images expect it
                        {"name": "LAMBDA_TASK_ROOT", "value": "/var/task"},
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

        self.task = aws.ecs.TaskDefinition(
            f"{name}-task",
            family=f"{name}-task",
            cpu=str(cpu),
            memory=str(memory),
            network_mode="awsvpc",
            requires_compatibilities=["FARGATE"],
            execution_role_arn=exec_role_arn,
            task_role_arn=task_role_arn,
            runtime_platform=aws.ecs.TaskDefinitionRuntimePlatformArgs(
                cpu_architecture="ARM64",
                operating_system_family="LINUX",
            ),
            container_definitions=container_def.apply(
                lambda d: pulumi.Output.secret(pulumi.Output.json_dumps(d))
            ),
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["container_definitions"],  # CodeBuild updates image
            ),
        )

        # ECS Service with Cloud Map registry
        self.svc = aws.ecs.Service(
            f"{name}-svc",
            cluster=self.cluster.arn,
            task_definition=self.task.arn,
            desired_count=desired_count,
            launch_type="FARGATE",
            network_configuration=aws.ecs.ServiceNetworkConfigurationArgs(
                subnets=public_subnet_ids,
                security_groups=[security_group_id],
                assign_public_ip=True,
            ),
            service_registries=aws.ecs.ServiceServiceRegistriesArgs(
                registry_arn=self.sd_service.arn
            ),
            opts=ResourceOptions(
                parent=self, depends_on=[self.ns, self.sd_service]
            ),
        )

        # Outputs
        self.endpoint_dns = pulumi.Output.all(
            self.sd_service.name, self.ns.name
        ).apply(lambda args: f"{args[0]}.{args[1]}:{port}")

        self.register_outputs(
            {
                "cluster_arn": self.cluster.arn,
                "task_arn": self.task.arn,
                "service_arn": self.svc.arn,
                "repository_url": self.repo.repository_url,
                "endpoint_dns": self.endpoint_dns,
            }
        )
