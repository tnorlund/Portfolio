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
    get_authorization_token,
)
from pulumi_docker import DockerBuildArgs, Image, RegistryArgs


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
            commit = subprocess.check_output(
                ['git', 'rev-parse', '--short', 'HEAD'],
                cwd=str(package_dir.parent),
                stderr=subprocess.DEVNULL
            ).decode().strip()
            # Also get the status to see if there are uncommitted changes
            status = subprocess.check_output(
                ['git', 'status', '--porcelain', str(package_dir)],
                cwd=str(package_dir.parent),
                stderr=subprocess.DEVNULL
            ).decode().strip()
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

        # Get ECR authorization token
        ecr_auth_token = get_authorization_token()

        # Build context path (project root)
        build_context_path = Path(__file__).parent.parent.parent
        # Get tags for each package
        dynamo_package_dir = build_context_path / "receipt_dynamo"
        label_package_dir = build_context_path / "receipt_label"
        dynamo_tag = self.get_image_tag("receipt_dynamo", dynamo_package_dir)
        label_tag = self.get_image_tag("receipt_label", label_package_dir)

        # Build receipt_dynamo base image with content-based tag
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
                args={
                    "PYTHON_VERSION": "3.12",
                    "BUILDKIT_INLINE_CACHE": "1",
                },
                # TODO: Re-enable cache_from once Pulumi serialization issue
                # is resolved. The issue: Pulumi's type serialization expects
                # a list but gets CacheFromArgs. Workaround: Use manual cache
                # warming with ./pull_base_images.sh
            ),
            image_name=self.dynamo_base_repo.repository_url.apply(
                lambda url: f"{url}:{dynamo_tag}"
            ),
            registry=RegistryArgs(
                server=self.dynamo_base_repo.repository_url.apply(
                    lambda url: url.split("/")[0]
                ),
                username="AWS",
                password=ecr_auth_token.password,
            ),
            skip_push=False,
            opts=ResourceOptions(
                parent=self, depends_on=[self.dynamo_base_repo]
            ),
        )

        # Build receipt_label base image (depends on dynamo base image
        # and label ECR repo)
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
                    "BUILDKIT_INLINE_CACHE": "1",
                },
                # TODO: Re-enable cache_from once Pulumi serialization issue
                # is resolved. The issue: Pulumi's type serialization expects
                # a list but gets CacheFromArgs. Workaround: Use manual cache
                # warming with ./pull_base_images.sh
            ),
            image_name=self.label_base_repo.repository_url.apply(
                lambda url: f"{url}:{label_tag}"
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
