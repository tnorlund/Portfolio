"""Docker image building component for embedding Lambda functions."""

import hashlib
import subprocess
from pathlib import Path
from typing import Optional

import pulumi
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

    def get_handler_content_hash(self, handler_dir: Path) -> str:
        """Generate hash for handler code.
        
        Args:
            handler_dir: Path to handler directory
            
        Returns:
            Content-based hash string
        """
        # Try git first for speed
        try:
            commit = (
                subprocess.check_output(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=str(handler_dir.parent.parent.parent),
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
            # Check for uncommitted changes in handler directory
            status = (
                subprocess.check_output(
                    ["git", "status", "--porcelain", str(handler_dir)],
                    cwd=str(handler_dir.parent.parent.parent),
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
            if status:
                return f"git-{commit}-dirty"
            return f"git-{commit}"
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        
        # Fallback to file hashing
        content_hash = hashlib.sha256()
        for file_path in sorted(handler_dir.rglob("*.py")):
            if file_path.is_file():
                rel_path = file_path.relative_to(handler_dir)
                content_hash.update(str(rel_path).encode())
                content_hash.update(file_path.read_bytes())
        return f"sha-{content_hash.hexdigest()[:12]}"

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
        handler_dir = Path(__file__).parent.parent / "unified_embedding"
        
        # Get content-based tag for the image
        image_tag = self.get_handler_content_hash(handler_dir)
        pulumi.log.info(
            f"Using content-based tag for embedding image: {image_tag}"
        )

        # Get base image URI if available
        base_image_uri = None
        if base_images and hasattr(base_images, 'label_base_image'):
            # Use the base image tag from the base images component
            base_image_uri = base_images.label_base_image.tags[0]
            pulumi.log.info(
                "Using base-receipt-label image for embedding container"
            )
        else:
            # Fallback to public image if base images not available
            base_image_uri = "public.ecr.aws/lambda/python:3.12"
            pulumi.log.warn(
                "Base images not available, using public Lambda image"
            )
        
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
                "BASE_IMAGE": base_image_uri,
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
                    lambda url: f"{url}:{image_tag}"
                ),
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
