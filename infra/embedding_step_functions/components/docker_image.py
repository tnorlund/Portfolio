"""Docker image building component for embedding Lambda functions."""

import json
import hashlib
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

import pulumi
from pulumi import ComponentResource, ResourceOptions, Output
from pulumi_aws.ecr import (
    Repository,
    RepositoryImageScanningConfigurationArgs,
    LifecyclePolicy,
    RepositoryPolicy,
)

# Import the CodeBuildDockerImage component
from codebuild_docker_image import CodeBuildDockerImage

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
        opts: Optional[ResourceOptions] = None,
    ):
        """Initialize Docker image component.

        Args:
            name: Component name
            opts: Pulumi resource options
        """
        super().__init__(
            "custom:embedding:DockerImage",
            name,
            None,
            opts,
        )

        # Get content hash for versioning
        handler_dir = Path(__file__).parent.parent / "unified_embedding"
        image_tag = self.get_handler_content_hash(handler_dir)
        pulumi.log.info(
            f"Using content-based tag for embedding image: {image_tag}"
        )

        # Use CodeBuildDockerImage for AWS-based builds
        # Note: Lambda functions will be created separately by LambdaFunctionsComponent
        self.docker_image = CodeBuildDockerImage(
            f"{name}-image",
            dockerfile_path="infra/embedding_step_functions/unified_embedding/Dockerfile",
            build_context_path=".",  # Project root for monorepo access
            source_paths=None,  # Use default rsync with exclusions
            lambda_function_name=None,  # Lambdas created separately
            lambda_config=None,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self),
        )

        # Export ECR repository and image URI
        self.ecr_repo = self.docker_image.ecr_repo
        self.repository_url = self.docker_image.repository_url
        self.image_uri = self.docker_image.image_uri

        # Attach ECR lifecycle policy (retain N, expire untagged older than X)
        portfolio_config = pulumi.Config("portfolio")
        import os

        max_images = portfolio_config.get_int("ecr-max-images") or int(
            os.environ.get("ECR_MAX_IMAGES", "10")
        )
        max_age_days = portfolio_config.get_int("ecr-max-age-days") or int(
            os.environ.get("ECR_MAX_AGE_DAYS", "30")
        )
        # Protect important tags by limiting pruning to ephemeral tag prefixes
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
        self.repo_policy = RepositoryPolicy(
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

        # Register outputs
        self.register_outputs(
            {
                "ecr_repo_url": self.repository_url,
                "image_uri": self.image_uri,
                "repo_policy_id": self.repo_policy.id,
            }
        )
