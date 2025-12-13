"""
Enhanced base images component using dependency graph.

This extends the base images functionality to automatically create
base images for all packages based on the dependency graph.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

import pulumi
import pulumi_docker_build as docker_build
from pulumi import ComponentResource, ResourceOptions
from pulumi_aws import config, get_authorization_token_output
from pulumi_aws.ecr import (
    LifecyclePolicy,
    Repository,
    RepositoryImageScanningConfigurationArgs,
)

from infra.base_images.dockerfile_generator import generate_dockerfile
from infra.shared.package_dependencies import DependencyGraph


class EnhancedBaseImages(ComponentResource):
    """Component for creating base ECR images for all packages using dependency graph."""

    def __init__(
        self,
        name: str,
        stack: str,
        packages_to_build: Optional[list[str]] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        """Initialize enhanced base images component.

        Args:
            name: Component name
            stack: Pulumi stack name
            packages_to_build: Optional list of package names to build base images for.
                             If None, builds for all packages in dependency graph.
            opts: Pulumi resource options
        """
        super().__init__(
            "custom:base:EnhancedBaseImages",
            name,
            None,
            opts,
        )

        # Build dependency graph
        build_context_path = Path(__file__).parent.parent.parent
        self.dependency_graph = DependencyGraph.from_project_root(
            build_context_path
        )

        # Validate no cycles
        cycles = self.dependency_graph.detect_cycles()
        if cycles:
            raise ValueError(f"Dependency graph contains cycles: {cycles}")

        # Determine which packages to build base images for
        if packages_to_build is None:
            # Build for all packages
            packages_to_build = list(
                self.dependency_graph.package_paths.keys()
            )
        else:
            # Validate packages exist
            for pkg in packages_to_build:
                if pkg not in self.dependency_graph.package_paths:
                    raise ValueError(
                        f"Package {pkg} not found in dependency graph"
                    )

        # Get ECR authorization token
        ecr_auth_token = get_authorization_token_output()

        # Create ECR repositories and base images for each package
        self.base_images: Dict[str, pulumi.Output[str]] = {}
        self.base_repos: Dict[str, Repository] = {}

        for package_name in packages_to_build:
            # Create ECR repository
            repo_name = f"base-{package_name.replace('-', '-').replace('_', '-')}-{stack}"
            repo = Repository(
                f"{repo_name}-ecr",
                name=repo_name,
                image_scanning_configuration=RepositoryImageScanningConfigurationArgs(
                    scan_on_push=True,
                ),
                force_delete=True,
                opts=ResourceOptions(parent=self),
            )
            self.base_repos[package_name] = repo

            # Get packages to include in base image
            base_packages = self.dependency_graph.get_base_image_packages(
                package_name
            )

            # Generate Dockerfile
            dockerfiles_dir = Path(__file__).parent / "dockerfiles"
            dockerfile_path = (
                dockerfiles_dir
                / f"Dockerfile.{package_name.replace('-', '_')}"
            )
            generate_dockerfile(base_packages, dockerfile_path)

            # Get content hash for tagging
            package_dir = self.dependency_graph.package_paths[package_name]
            if len(base_packages) == 1:
                # Single package - use simple hash
                tag = self._get_image_tag(package_name, package_dir)
            else:
                # Multiple packages - use combined hash
                package_dirs = [
                    self.dependency_graph.package_paths[pkg]
                    for pkg in base_packages
                ]
                tag = self._get_combined_image_tag(package_name, package_dirs)

            # Build base image
            base_image = docker_build.Image(
                f"base-{package_name.replace('-', '-').replace('_', '-')}-img-{stack}",
                context={"location": str(build_context_path)},
                dockerfile={"location": str(dockerfile_path)},
                platforms=["linux/arm64"],
                build_args={
                    "PYTHON_VERSION": "3.12",
                    "BUILDKIT_INLINE_CACHE": "1",
                },
                cache_from=[
                    {
                        "registry": {
                            "ref": repo.repository_url.apply(
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
                            "ref": repo.repository_url.apply(
                                lambda url: f"{url}:cache"
                            ),
                        },
                    },
                ],
                push=True,
                registries=[
                    {
                        "address": repo.repository_url.apply(
                            lambda url: url.split("/")[0]
                        ),
                        "password": ecr_auth_token.password,
                        "username": ecr_auth_token.user_name,
                    },
                ],
                tags=[
                    repo.repository_url.apply(lambda url: f"{url}:{tag}"),
                ],
                opts=ResourceOptions(parent=self, depends_on=[repo]),
            )

            # Store base image URI
            self.base_images[package_name] = base_image.tags[0]

        # Create lifecycle policies
        self._create_lifecycle_policies(stack)

        # Register outputs
        outputs = {
            f"{pkg.replace('-', '_')}_base_image_uri": uri
            for pkg, uri in self.base_images.items()
        }
        self.register_outputs(outputs)

    def _get_image_tag(self, package_name: str, package_dir: Path) -> str:
        """Get image tag for a package."""
        # Check for static mode
        pulumi_config = pulumi.Config("portfolio")
        use_static = pulumi_config.get_bool("use-static-base-image")
        if use_static is None:
            use_static = os.environ.get(
                "USE_STATIC_BASE_IMAGE", ""
            ).lower() in (
                "true",
                "1",
                "yes",
            )

        if use_static:
            pulumi.log.info(
                f"Using stable tag for {package_name} (static mode enabled)"
            )
            return "stable"

        # Use content-based hash
        return self.dependency_graph.get_content_hash(package_dir)

    def _get_combined_image_tag(
        self, package_name: str, package_dirs: list[Path]
    ) -> str:
        """Get combined image tag for multiple packages."""
        # Check for static mode
        pulumi_config = pulumi.Config("portfolio")
        use_static = pulumi_config.get_bool("use-static-base-image")
        if use_static is None:
            use_static = os.environ.get(
                "USE_STATIC_BASE_IMAGE", ""
            ).lower() in (
                "true",
                "1",
                "yes",
            )

        if use_static:
            pulumi.log.info(
                f"Using stable tag for {package_name} (static mode enabled)"
            )
            return "stable"

        # Use combined content-based hash
        return self.dependency_graph.get_combined_content_hash(package_dirs)

    def _create_lifecycle_policies(self, stack: str) -> None:
        """Create lifecycle policies for all ECR repositories."""
        portfolio_config = pulumi.Config("portfolio")
        max_images = portfolio_config.get_int("ecr-max-images") or int(
            os.environ.get("ECR_MAX_IMAGES", "10")
        )
        max_age_days = portfolio_config.get_int("ecr-max-age-days") or int(
            os.environ.get("ECR_MAX_AGE_DAYS", "30")
        )

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

        # Create lifecycle policy for each repository
        for package_name, repo in self.base_repos.items():
            LifecyclePolicy(
                f"base-{package_name.replace('-', '-').replace('_', '-')}-lifecycle-{stack}",
                repository=repo.name,
                policy=lifecycle_policy_doc,
                opts=ResourceOptions(parent=self, depends_on=[repo]),
            )

    def get_base_image_uri(
        self, package_name: str
    ) -> Optional[pulumi.Output[str]]:
        """Get base image URI for a package.

        Args:
            package_name: Name of the package

        Returns:
            Base image URI (Pulumi Output) or None if not found
        """
        return self.base_images.get(package_name)
