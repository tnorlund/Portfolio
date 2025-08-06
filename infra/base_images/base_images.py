"""
Base ECR images for receipt_dynamo and receipt_label packages.

This module creates reusable base images that contain the installed packages,
allowing Lambda containers to build faster by using these as base images.
"""

import json
from pathlib import Path

import pulumi
from pulumi import ComponentResource, Output, ResourceOptions
from pulumi_aws import get_caller_identity, config
from pulumi_aws.ecr import (
    Repository,
    RepositoryImageScanningConfigurationArgs,
    get_authorization_token,
)
from pulumi_docker import DockerBuildArgs, Image, RegistryArgs


class BaseImages(ComponentResource):
    """Component for creating base ECR images for packages."""

    def __init__(
        self,
        name: str,
        stack: str,
        opts: ResourceOptions = None,
    ):
        super().__init__(
            "custom:base:BaseImages",
            name,
            None,
            opts,
        )

        # Get AWS account details
        account_id = get_caller_identity().account_id
        region = config.region

        # Create ECR repository for receipt_dynamo base image
        self.dynamo_base_repo = Repository(
            f"base-receipt-dynamo-ecr-{stack}",
            name=f"base-receipt-dynamo-{stack}",
            image_scanning_configuration=(
                RepositoryImageScanningConfigurationArgs(
                    scan_on_push=True,
                )
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )

        # Create ECR repository for receipt_label base image
        self.label_base_repo = Repository(
            f"base-receipt-label-ecr-{stack}",
            name=f"base-receipt-label-{stack}",
            image_scanning_configuration=(
                RepositoryImageScanningConfigurationArgs(
                    scan_on_push=True,
                )
            ),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )

        # Get ECR authorization token
        ecr_auth_token = get_authorization_token()

        # Build context path (project root)
        build_context_path = Path(__file__).parent.parent.parent

        # Build receipt_dynamo base image
        # Explicitly depend on ECR repository being ready
        self.dynamo_base_image = Image(
            f"base-receipt-dynamo-img-{stack}",
            build=DockerBuildArgs(
                context=str(build_context_path),
                dockerfile=str(
                    Path(__file__).parent
                    / "dockerfiles"
                    / "Dockerfile.receipt_dynamo"
                ),
                platform="linux/arm64",
                args={"PYTHON_VERSION": "3.12"},
            ),
            image_name=self.dynamo_base_repo.repository_url.apply(
                lambda url: f"{url}:latest"
            ),
            registry=RegistryArgs(
                server=self.dynamo_base_repo.repository_url.apply(
                    lambda url: url.split("/")[0]
                ),
                username="AWS",
                password=ecr_auth_token.password,
            ),
            skip_push=False,
            opts=ResourceOptions(parent=self, depends_on=[self.dynamo_base_repo]),
        )

        # Build receipt_label base image (depends on dynamo base image and label ECR repo)
        self.label_base_image = Image(
            f"base-receipt-label-img-{stack}",
            build=DockerBuildArgs(
                context=str(build_context_path),
                dockerfile=str(
                    Path(__file__).parent
                    / "dockerfiles"
                    / "Dockerfile.receipt_label"
                ),
                platform="linux/arm64",
                args={
                    "PYTHON_VERSION": "3.12",
                    "BASE_IMAGE": self.dynamo_base_image.image_name,
                },
            ),
            image_name=self.label_base_repo.repository_url.apply(
                lambda url: f"{url}:latest"
            ),
            registry=RegistryArgs(
                server=self.label_base_repo.repository_url.apply(
                    lambda url: url.split("/")[0]
                ),
                username="AWS",
                password=ecr_auth_token.password,
            ),
            skip_push=False,
            opts=ResourceOptions(
                parent=self, 
                depends_on=[self.dynamo_base_image, self.label_base_repo]
            ),
        )

        # Register outputs
        self.register_outputs(
            {
                "dynamo_base_image_name": self.dynamo_base_image.image_name,
                "label_base_image_name": self.label_base_image.image_name,
                "dynamo_base_repo_url": self.dynamo_base_repo.repository_url,
                "label_base_repo_url": self.label_base_repo.repository_url,
            }
        )