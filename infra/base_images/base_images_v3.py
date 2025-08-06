"""
Base ECR images with SCOPED build contexts for better performance.

This version uses package directories as build contexts instead of the
entire repo, preventing unnecessary rebuilds when unrelated files change.
"""

import hashlib
import os
import subprocess
from pathlib import Path

import pulumi
from pulumi import ComponentResource, ResourceOptions
from pulumi_aws.ecr import (
    Repository,
    RepositoryImageScanningConfigurationArgs,
    get_authorization_token_output,
)

# Import docker-build provider
import pulumi_docker_build as docker_build


class BaseImages(ComponentResource):
    """Component for creating base ECR images with scoped build contexts."""

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
                cwd=str(package_dir),
                stderr=subprocess.DEVNULL
            ).decode().strip()

            # Check if this specific package has changes
            status = subprocess.check_output(
                ['git', 'status', '--porcelain', '.'],
                cwd=str(package_dir),
                stderr=subprocess.DEVNULL
            ).decode().strip()

            if status:
                # There are uncommitted changes in this package
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
        pconfig = pulumi.Config("portfolio")
        use_static = pconfig.get_bool("use-static-base-image")

        # If not in config, check environment variable
        if use_static is None:
            use_static = os.environ.get(
                "USE_STATIC_BASE_IMAGE", ""
            ).lower() in ("true", "1", "yes")

        if use_static:
            # Use a stable tag for local development
            msg = f"Using stable tag for {package_name} (static mode enabled)"
            pulumi.log.info(msg)
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

        # Get AWS account details (unused but may be needed in future)
        # account_id = get_caller_identity().account_id
        # region = config.region

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

        # Get ECR authorization token (using output version for docker-build)
        ecr_auth_token = get_authorization_token_output()

        # Get package directories
        repo_root = Path(__file__).parent.parent.parent
        dynamo_package_dir = repo_root / "receipt_dynamo"
        label_package_dir = repo_root / "receipt_label"

        # Get tags for each package
        dynamo_tag = self.get_image_tag("receipt_dynamo", dynamo_package_dir)
        label_tag = self.get_image_tag("receipt_label", label_package_dir)

        # Build receipt_dynamo base image with SCOPED context
        self.dynamo_base_image = docker_build.Image(
            f"base-receipt-dynamo-img-{stack}",
            context=docker_build.ContextArgs(
                location=str(dynamo_package_dir),  # SCOPED to just the package!
            ),
            dockerfile=docker_build.DockerfileArgs(
                # Use the scoped Dockerfile that expects package as context
                location=str(
                    Path(__file__).parent
                    / "dockerfiles"
                    / "Dockerfile.receipt_dynamo.scoped"
                ),
            ),
            platforms=["linux/arm64"],
            build_args={
                "PYTHON_VERSION": "3.12",
                "BUILDKIT_INLINE_CACHE": "1",
            },
            # Cache configuration
            cache_from=[
                docker_build.CacheFromArgs(
                    registry=docker_build.CacheFromRegistryArgs(
                        ref=self.dynamo_base_repo.repository_url.apply(
                            lambda url: f"{url}:cache"
                        ),
                    ),
                ),
            ],
            cache_to=[
                docker_build.CacheToArgs(
                    registry=docker_build.CacheToRegistryArgs(
                        image_manifest=True,
                        oci_media_types=True,
                        ref=self.dynamo_base_repo.repository_url.apply(
                            lambda url: f"{url}:cache"
                        ),
                    ),
                ),
            ],
            # Registry configuration for pushing
            push=True,
            registries=[
                docker_build.RegistryArgs(
                    address=self.dynamo_base_repo.repository_url,
                    password=ecr_auth_token.password,
                    username=ecr_auth_token.user_name,
                ),
            ],
            # Tags for the image
            tags=[
                self.dynamo_base_repo.repository_url.apply(
                    lambda url: f"{url}:{dynamo_tag}"
                ),
                self.dynamo_base_repo.repository_url.apply(
                    lambda url: f"{url}:latest"
                ),
            ],
            opts=ResourceOptions(parent=self, depends_on=[self.dynamo_base_repo]),
        )

        # Build receipt_label base image with SCOPED context
        # It doesn't need receipt_dynamo in context since it uses it as base image
        self.label_base_image = docker_build.Image(
            f"base-receipt-label-img-{stack}",
            context=docker_build.ContextArgs(
                location=str(label_package_dir),  # SCOPED to just receipt_label!
            ),
            dockerfile=docker_build.DockerfileArgs(
                location=str(
                    Path(__file__).parent
                    / "dockerfiles"
                    / "Dockerfile.receipt_label.scoped"
                ),
            ),
            platforms=["linux/arm64"],
            build_args={
                "PYTHON_VERSION": "3.12",
                "BASE_IMAGE": self.dynamo_base_image.ref,  # Use ref from docker-build
                "BUILDKIT_INLINE_CACHE": "1",
            },
            # Cache configuration
            cache_from=[
                docker_build.CacheFromArgs(
                    registry=docker_build.CacheFromRegistryArgs(
                        ref=self.label_base_repo.repository_url.apply(
                            lambda url: f"{url}:cache"
                        ),
                    ),
                ),
            ],
            cache_to=[
                docker_build.CacheToArgs(
                    registry=docker_build.CacheToRegistryArgs(
                        image_manifest=True,
                        oci_media_types=True,
                        ref=self.label_base_repo.repository_url.apply(
                            lambda url: f"{url}:cache"
                        ),
                    ),
                ),
            ],
            # Registry configuration for pushing
            push=True,
            registries=[
                docker_build.RegistryArgs(
                    address=self.label_base_repo.repository_url,
                    password=ecr_auth_token.password,
                    username=ecr_auth_token.user_name,
                ),
            ],
            # Tags for the image
            tags=[
                self.label_base_repo.repository_url.apply(
                    lambda url: f"{url}:{label_tag}"
                ),
                self.label_base_repo.repository_url.apply(
                    lambda url: f"{url}:latest"
                ),
            ],
            opts=ResourceOptions(
                parent=self,
                depends_on=[self.dynamo_base_image, self.label_base_repo]
            ),
        )

        # Register outputs
        # Note: docker-build uses 'ref' instead of 'image_name'
        self.register_outputs(
            {
                "dynamo_base_image_ref": self.dynamo_base_image.ref,
                "label_base_image_ref": self.label_base_image.ref,
                "dynamo_base_repo_url": self.dynamo_base_repo.repository_url,
                "label_base_repo_url": self.label_base_repo.repository_url,
            }
        )
