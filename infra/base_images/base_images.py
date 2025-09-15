"""
Base ECR images for receipt_dynamo and receipt_label packages.

This module creates reusable base images that contain the installed packages,
allowing Lambda containers to build faster by using these as base images.
"""

import hashlib
import os
import subprocess
from pathlib import Path

import pulumi
from pulumi import ComponentResource, ResourceOptions
from pulumi_aws import get_caller_identity, config
from pulumi_aws.ecr import (
    Repository,
    RepositoryImageScanningConfigurationArgs,
    get_authorization_token_output,  # Use output version for docker-build
    LifecyclePolicy,
)
import pulumi_docker_build as docker_build
import json


class BaseImages(ComponentResource):
    """Component for creating base ECR images for packages."""

    def get_content_hash(self, package_dir: Path) -> str:
        """Generate a hash based on package content.

        Args:
            package_dir: Path to the package directory

        Returns:
            A deterministic hash string based on package content
        """
        # Option 1: Try to use git commit SHA (fast and reliable)
        try:
            commit = (
                subprocess.check_output(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=str(package_dir.parent),
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
            # Also get the status to see if there are uncommitted changes
            status = (
                subprocess.check_output(
                    ["git", "status", "--porcelain", str(package_dir)],
                    cwd=str(package_dir.parent),
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
            if status:
                # There are uncommitted changes, add a dirty flag
                return f"git-{commit}-dirty"
            return f"git-{commit}"
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        # Option 2: Hash package files (fallback)
        package_files = list(package_dir.rglob("*.py"))
        content_hash = hashlib.sha256()
        for file_path in sorted(package_files):
            if file_path.is_file() and not file_path.name.startswith("test_"):
                # Include file path relative to package dir for consistency
                rel_path = file_path.relative_to(package_dir)
                content_hash.update(str(rel_path).encode())
                # Include file content
                content_hash.update(file_path.read_bytes())
        return f"sha-{content_hash.hexdigest()[:12]}"

    def get_combined_content_hash(self, package_dirs: list[Path]) -> str:
        """Generate a hash based on multiple package contents.

        Args:
            package_dirs: List of paths to package directories

        Returns:
            A deterministic hash string based on all package contents
        """
        # Option 1: Try to use git commit SHA (fast and reliable)
        try:
            commit = (
                subprocess.check_output(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=str(package_dirs[0].parent),
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
            # Check for uncommitted changes in any package
            has_changes = False
            for package_dir in package_dirs:
                status = (
                    subprocess.check_output(
                        ["git", "status", "--porcelain", str(package_dir)],
                        cwd=str(package_dir.parent),
                        stderr=subprocess.DEVNULL,
                    )
                    .decode()
                    .strip()
                )
                if status:
                    has_changes = True
                    break

            if has_changes:
                return f"git-{commit}-dirty"
            return f"git-{commit}"
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        # Option 2: Hash all package files (fallback)
        content_hash = hashlib.sha256()
        for package_dir in sorted(package_dirs):
            package_files = list(package_dir.rglob("*.py"))
            for file_path in sorted(package_files):
                if file_path.is_file() and not file_path.name.startswith(
                    "test_"
                ):
                    # Include package name and relative path for consistency
                    rel_path = file_path.relative_to(package_dir.parent)
                    content_hash.update(str(rel_path).encode())
                    # Include file content
                    content_hash.update(file_path.read_bytes())
        return f"sha-{content_hash.hexdigest()[:12]}"

    def get_image_tag(self, package_name: str, package_dir: Path) -> str:
        """Get the image tag for a package.

        Args:
            package_name: Name of the package (e.g., "receipt_dynamo")
            package_dir: Path to the package directory

        Returns:
            The image tag to use
        """
        # Check Pulumi config first, then environment variable as fallback
        pulumi_config = pulumi.Config("portfolio")
        use_static = pulumi_config.get_bool("use-static-base-image")
        # If not in config, check environment variable
        if use_static is None:
            use_static = os.environ.get(
                "USE_STATIC_BASE_IMAGE", ""
            ).lower() in ("true", "1", "yes")
        if use_static:
            # Use a stable tag for local development
            pulumi.log.info(
                f"Using stable tag for {package_name} (static mode enabled)"
            )
            return "stable"
        # Use content-based hash for production
        hash_tag = self.get_content_hash(package_dir)
        pulumi.log.info(
            f"Using content-based tag for {package_name}: {hash_tag}"
        )
        return hash_tag

    def get_combined_image_tag(
        self, package_name: str, package_dirs: list[Path]
    ) -> str:
        """Get the image tag for a package that includes multiple directories.

        Args:
            package_name: Name of the package (e.g., "receipt_label")
            package_dirs: List of paths to package directories

        Returns:
            The image tag to use
        """
        # Check Pulumi config first, then environment variable as fallback
        pulumi_config = pulumi.Config("portfolio")
        use_static = pulumi_config.get_bool("use-static-base-image")
        # If not in config, check environment variable
        if use_static is None:
            use_static = os.environ.get(
                "USE_STATIC_BASE_IMAGE", ""
            ).lower() in ("true", "1", "yes")
        if use_static:
            # Use a stable tag for local development
            pulumi.log.info(
                f"Using stable tag for {package_name} (static mode enabled)"
            )
            return "stable"
        # Use combined content-based hash for production
        hash_tag = self.get_combined_content_hash(package_dirs)
        pulumi.log.info(
            f"Using combined content-based tag for {package_name}: {hash_tag}"
        )
        return hash_tag

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

        # Lifecycle policy configuration (retain N, expire untagged older than X)
        portfolio_config = pulumi.Config("portfolio")
        max_images = portfolio_config.get_int("ecr-max-images") or int(
            os.environ.get("ECR_MAX_IMAGES", "10")
        )
        max_age_days = portfolio_config.get_int("ecr-max-age-days") or int(
            os.environ.get("ECR_MAX_AGE_DAYS", "30")
        )

        # Only expire images with ephemeral tags (e.g., content-hash tags),
        # preserving important tags like "latest" and "stable" by default.
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

        # Attach lifecycle policies to both base repositories
        self.dynamo_lifecycle = LifecyclePolicy(
            f"base-receipt-dynamo-lifecycle-{stack}",
            repository=self.dynamo_base_repo.name,
            policy=lifecycle_policy_doc,
            opts=ResourceOptions(
                parent=self, depends_on=[self.dynamo_base_repo]
            ),
        )

        self.label_lifecycle = LifecyclePolicy(
            f"base-receipt-label-lifecycle-{stack}",
            repository=self.label_base_repo.name,
            policy=lifecycle_policy_doc,
            opts=ResourceOptions(
                parent=self, depends_on=[self.label_base_repo]
            ),
        )

        # Get ECR authorization token (using output version for docker-build)
        ecr_auth_token = get_authorization_token_output()

        # Build context path (project root)
        build_context_path = Path(__file__).parent.parent.parent
        # Get tags for each package
        dynamo_package_dir = build_context_path / "receipt_dynamo"
        label_package_dir = build_context_path / "receipt_label"
        dynamo_tag = self.get_image_tag("receipt_dynamo", dynamo_package_dir)
        # For label image, use combined hash since it includes both packages
        label_tag = self.get_combined_image_tag(
            "receipt_label", [dynamo_package_dir, label_package_dir]
        )

        # Store Dockerfile paths once to ensure consistency
        dynamo_dockerfile = str(
            Path(__file__).parent / "dockerfiles" / "Dockerfile.receipt_dynamo"
        )
        label_dockerfile = str(
            Path(__file__).parent / "dockerfiles" / "Dockerfile.receipt_label"
        )

        # Build receipt_dynamo base image with content-based tag
        # .dockerignore at project root ensures only receipt_dynamo/ and receipt_label/ are included
        self.dynamo_base_image = docker_build.Image(
            f"base-receipt-dynamo-img-{stack}",
            context={
                "location": str(build_context_path),
            },
            dockerfile={
                "location": dynamo_dockerfile,
            },
            platforms=["linux/arm64"],
            build_args={
                "PYTHON_VERSION": "3.12",
                "BUILDKIT_INLINE_CACHE": "1",
            },
            # ECR caching configuration
            cache_from=[
                {
                    "registry": {
                        "ref": self.dynamo_base_repo.repository_url.apply(
                            lambda url: f"{url}:cache"
                        ),
                    },
                },
            ],
            cache_to=[
                {
                    "registry": {
                        "imageManifest": True,
                        "ociMediaTypes": True,
                        "ref": self.dynamo_base_repo.repository_url.apply(
                            lambda url: f"{url}:cache"
                        ),
                    },
                },
            ],
            # Registry configuration for pushing
            push=True,
            registries=[
                {
                    "address": self.dynamo_base_repo.repository_url.apply(
                        lambda url: url.split("/")[
                            0
                        ]  # Extract registry address
                    ),
                    "password": ecr_auth_token.password,
                    "username": ecr_auth_token.user_name,
                },
            ],
            # Tags for the image
            tags=[
                self.dynamo_base_repo.repository_url.apply(
                    lambda url: f"{url}:{dynamo_tag}"
                ),
            ],
            opts=ResourceOptions(
                parent=self, depends_on=[self.dynamo_base_repo]
            ),
        )

        # Build receipt_label base image IN PARALLEL (no dependency on dynamo image)
        # This is now self-contained with both packages
        # .dockerignore at project root ensures only receipt_dynamo/ and receipt_label/ are included
        self.label_base_image = docker_build.Image(
            f"base-receipt-label-img-{stack}",
            context={
                "location": str(build_context_path),
            },
            dockerfile={
                "location": label_dockerfile,
            },
            platforms=["linux/arm64"],
            build_args={
                "PYTHON_VERSION": "3.12",
                # No BASE_IMAGE needed anymore - self-contained build
                "BUILDKIT_INLINE_CACHE": "1",
            },
            # ECR caching configuration
            cache_from=[
                {
                    "registry": {
                        "ref": self.label_base_repo.repository_url.apply(
                            lambda url: f"{url}:cache"
                        ),
                    },
                },
            ],
            cache_to=[
                {
                    "registry": {
                        "imageManifest": True,
                        "ociMediaTypes": True,
                        "ref": self.label_base_repo.repository_url.apply(
                            lambda url: f"{url}:cache"
                        ),
                    },
                },
            ],
            # Registry configuration for pushing
            push=True,
            registries=[
                {
                    "address": self.label_base_repo.repository_url.apply(
                        lambda url: url.split("/")[
                            0
                        ]  # Extract registry address
                    ),
                    "password": ecr_auth_token.password,
                    "username": ecr_auth_token.user_name,
                },
            ],
            # Tags for the image
            tags=[
                self.label_base_repo.repository_url.apply(
                    lambda url: f"{url}:{label_tag}"
                ),
            ],
            opts=ResourceOptions(
                parent=self,
                depends_on=[
                    self.label_base_repo
                ],  # Only depends on ECR repo, not dynamo image
            ),
        )

        # Register outputs
        self.register_outputs(
            {
                "dynamo_base_image_name": self.dynamo_base_image.tags[0],
                "label_base_image_name": self.label_base_image.tags[0],
                "dynamo_base_repo_url": self.dynamo_base_repo.repository_url,
                "label_base_repo_url": self.label_base_repo.repository_url,
            }
        )
