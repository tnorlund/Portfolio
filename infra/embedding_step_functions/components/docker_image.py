"""Docker image building component for embedding Lambda functions."""

from pathlib import Path
from typing import Optional

from pulumi import ComponentResource, ResourceOptions
from pulumi_aws.ecr import (
    Repository,
    RepositoryImageScanningConfigurationArgs,
    get_authorization_token_output,
)

# pylint: disable=import-error
import pulumi_docker_build as docker_build  # type: ignore[import-not-found]

# pylint: enable=import-error

from .base import stack


class DockerImageComponent(ComponentResource):
    """Component for building and managing Docker images for Lambda."""

    def __init__(
        self,
        name: str,
        base_images=None,
        opts: Optional[ResourceOptions] = None,
    ):
        """Initialize Docker image component.

        Args:
            name: Component name
            base_images: Optional base images dependency
            opts: Pulumi resource options
        """
        super().__init__(
            "custom:embedding:DockerImage",
            name,
            None,
            opts,
        )

        self.base_images = base_images

        # Create ECR repository
        self.ecr_repo = Repository(
            f"{name}-repo",
            image_scanning_configuration=(
                RepositoryImageScanningConfigurationArgs(
                    scan_on_push=True,
                )
            ),
            force_delete=True,
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        # Get ECR auth token
        ecr_auth_token = get_authorization_token_output()

        # Build context path (repository root)
        # Go up 4 levels from components/docker_image.py
        build_context_path = Path(__file__).parent.parent.parent.parent

        # Build Docker image
        self.docker_image = docker_build.Image(
            f"{name}-image",
            context={
                "location": str(build_context_path.resolve()),
            },
            dockerfile={
                "location": str(
                    (
                        Path(__file__).parent.parent
                        / "unified_embedding"
                        / "Dockerfile"
                    ).resolve()
                ),
            },
            platforms=["linux/arm64"],
            build_args={
                "PYTHON_VERSION": "3.12",
            },
            push=True,
            registries=[
                {
                    "address": self.ecr_repo.repository_url.apply(
                        lambda url: url.split("/")[0]
                    ),
                    "password": ecr_auth_token.password,
                    "username": ecr_auth_token.user_name,
                }
            ],
            tags=[
                self.ecr_repo.repository_url.apply(
                    lambda url: f"{url}:latest"
                ),
            ],
            opts=ResourceOptions(
                parent=self,
                depends_on=[self.ecr_repo]
                + ([base_images] if base_images else []),
            ),
        )

        # Register outputs
        self.register_outputs(
            {
                "ecr_repo_url": self.ecr_repo.repository_url,
                "docker_image_digest": self.docker_image.digest,
            }
        )
