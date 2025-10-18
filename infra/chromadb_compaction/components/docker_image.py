"""Docker image building component for ChromaDB compaction Lambda functions."""

import json
import hashlib
import subprocess
from pathlib import Path
from typing import Optional

import pulumi
from pulumi import ComponentResource, Output, ResourceOptions
from pulumi_aws.ecr import (
    Repository,
    RepositoryImageScanningConfigurationArgs,
    get_authorization_token_output,
    LifecyclePolicy,
)

try:
    import pulumi_docker_build as docker_build  # pylint: disable=import-error
except ImportError:
    # For testing environments, create a mock
    from unittest.mock import MagicMock

    docker_build = MagicMock()


class DockerImageComponent(ComponentResource):
    """
    Component for building and managing Docker images for ChromaDB compaction
    Lambda.
    """

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
        opts: Optional[ResourceOptions] = None,
    ):
        """Initialize Docker image component.

        Args:
            name: Component name
            opts: Pulumi resource options
        """
        super().__init__(
            "chromadb:compaction:DockerImage",
            name,
            None,
            opts,
        )

        # Get stack for naming
        stack = pulumi.get_stack()

        # Get ECR auth token for pulling base images
        ecr_auth_token = get_authorization_token_output()

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

        # Attach ECR lifecycle policy (retain N, expire untagged older than X)
        portfolio_config = pulumi.Config("portfolio")
        import os

        max_images = portfolio_config.get_int("ecr-max-images") or int(
            os.environ.get("ECR_MAX_IMAGES", "10")
        )
        max_age_days = portfolio_config.get_int("ecr-max-age-days") or int(
            os.environ.get("ECR_MAX_AGE_DAYS", "30")
        )
        # Protect important tags (e.g., latest, stable) by scoping pruning to
        # ephemeral tag prefixes like git-/sha- (configurable).
        ephemeral_prefixes_str = portfolio_config.get(
            "ecr-ephemeral-tag-prefixes"
        ) or os.environ.get("ECR_EPHEMERAL_TAG_PREFIXES", "git-,sha-")
        ephemeral_prefixes = [
            p.strip() for p in ephemeral_prefixes_str.split(",") if p.strip()
        ] or ["git-", "sha-"]

        lifecycle_policy_doc = json.dumps(
            {
                "rules": [
                    {
                        "rulePriority": 1,
                        "description": (
                            f"Keep only the {max_images} most recent ephemeral images"
                        ),
                        "selection": {
                            "tagStatus": "tagged",
                            "tagPrefixList": ephemeral_prefixes,
                            "countType": "imageCountMoreThan",
                            "countNumber": max_images,
                        },
                        "action": {"type": "expire"},
                    },
                    {
                        "rulePriority": 2,
                        "description": (
                            f"Expire untagged images older than {max_age_days} days"
                        ),
                        "selection": {
                            "tagStatus": "untagged",
                            "countType": "sinceImagePushed",
                            "countUnit": "days",
                            "countNumber": max_age_days,
                        },
                        "action": {"type": "expire"},
                    },
                ]
            }
        )

        self.ecr_lifecycle = LifecyclePolicy(
            f"{name}-repo-lifecycle",
            repository=self.ecr_repo.name,
            policy=lifecycle_policy_doc,
            opts=ResourceOptions(parent=self, depends_on=[self.ecr_repo]),
        )

        # Add ECR repository policy to allow Lambda to pull images
        from pulumi_aws.ecr import RepositoryPolicy

        RepositoryPolicy(
            f"{name}-repo-policy",
            repository=self.ecr_repo.name,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "LambdaECRImageRetrievalPolicy",
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": [
                                "ecr:BatchGetImage",
                                "ecr:GetDownloadUrlForLayer",
                            ],
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self, depends_on=[self.ecr_repo]),
        )

        # Generate content hash for versioning
        handler_dir = Path(__file__).parent.parent / "lambdas"
        content_tag = self.get_handler_content_hash(handler_dir)

        # Build and push Docker image
        self.docker_image = docker_build.Image(
            f"{name}-image",
            context=docker_build.BuildContextArgs(
                location=str(handler_dir.parent.parent.parent),  # Use project root as build context
            ),
            dockerfile=docker_build.DockerfileArgs(
                location=str(handler_dir / "Dockerfile"),
            ),
            build_args={},
            tags=[
                self.ecr_repo.repository_url.apply(
                    lambda url: f"{url}:latest"
                ),
                self.ecr_repo.repository_url.apply(
                    lambda url: f"{url}:{content_tag}"
                ),
            ],
            platforms=[docker_build.Platform.LINUX_ARM64],
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
            opts=ResourceOptions(
                parent=self,
                depends_on=[self.ecr_repo],
                replace_on_changes=["build_args", "dockerfile"],
            ),
        )

        # Export image URI for Lambda function (match embedding infrastructure pattern)
        self.image_uri = Output.all(
            self.ecr_repo.repository_url,
            self.docker_image.digest,
        ).apply(lambda args: f"{args[0].split(':')[0]}@{args[1]}")

        # Register outputs
        self.register_outputs(
            {
                "repository_url": self.ecr_repo.repository_url,
                "image_uri": self.image_uri,
                "digest": self.docker_image.digest,
                "content_tag": content_tag,
            }
        )
