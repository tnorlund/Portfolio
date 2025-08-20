"""Docker image building component for ChromaDB compaction Lambda functions."""

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


class DockerImageComponent(ComponentResource):
    """Component for building and managing Docker images for ChromaDB compaction Lambda."""

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
            "chromadb:compaction:DockerImage",
            name,
            None,
            opts,
        )

        self.base_images = base_images

        # Get stack for naming
        stack = pulumi.get_stack()

        # Create ECR repository
        self.ecr_repo = Repository(
            f"{name}-repo",
            image_scanning_configuration=(
                RepositoryImageScanningConfigurationArgs(
                    scan_on_push=True,
                )
            ),
            force_delete=True,  # Allow deletion in development
            tags={
                "Project": "ChromaDB",
                "Component": "CompactionContainer",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        # Generate content hash for versioning
        handler_dir = Path(__file__).parent.parent / "lambdas"
        content_tag = self.get_handler_content_hash(handler_dir)

        # Build and push Docker image
        self.docker_image = docker_build.Image(
            f"{name}-image",
            context=docker_build.BuildContextArgs(
                location="../../../",  # Root of repo for COPY context
            ),
            dockerfile=docker_build.DockerfileArgs(
                location="infra/chromadb_compaction/Dockerfile",
            ),
            build_args={
                "BASE_IMAGE": (
                    base_images.receipt_label_image
                    if base_images
                    else "public.ecr.aws/lambda/python:3.12"
                )
            },
            tags=[
                self.ecr_repo.repository_url.apply(lambda url: f"{url}:latest"),
                self.ecr_repo.repository_url.apply(lambda url: f"{url}:{content_tag}"),
            ],
            platforms=[docker_build.Platform.LINUX_ARM64],
            push=True,
            opts=ResourceOptions(
                parent=self,
                depends_on=[self.ecr_repo],
                replace_on_changes=["build_args", "dockerfile"],
            ),
        )

        # Export image URI for Lambda function
        self.image_uri = self.docker_image.ref.apply(
            lambda ref: f"{self.ecr_repo.repository_url.apply(lambda url: url.split(':')[0])}@{ref.split('@')[1]}"
            if "@" in ref
            else f"{self.ecr_repo.repository_url.apply(lambda url: url)}:latest"
        )

        # Register outputs
        self.register_outputs(
            {
                "repository_url": self.ecr_repo.repository_url,
                "image_uri": self.image_uri,
                "digest": self.docker_image.digest,
                "content_tag": content_tag,
            }
        )