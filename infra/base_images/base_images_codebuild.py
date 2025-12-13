"""
Base ECR images for receipt packages using CodeBuildDockerImage.

This module creates reusable base images that contain installed packages,
using CodeBuildDockerImage for async builds on AWS (no local Docker required).
"""

import hashlib
import subprocess
from pathlib import Path
from typing import Optional

import pulumi
from pulumi import ComponentResource, ResourceOptions

from infra.components.codebuild_docker_image import CodeBuildDockerImage


class BaseImagesCodeBuild(ComponentResource):
    """Component for creating base ECR images using CodeBuildDockerImage (async, no local Docker)."""

    def get_content_hash(self, package_dir: Path) -> str:
        """Generate a hash based on package content using per-directory git tracking.

        Args:
            package_dir: Path to the package directory

        Returns:
            A deterministic hash string based on package content
        """
        # Option 1: Try to use git commit SHA for this specific directory (fast and reliable)
        try:
            # Get the last commit that touched this specific directory
            commit = (
                subprocess.check_output(
                    ["git", "log", "-1", "--format=%h", "--", str(package_dir)],
                    cwd=str(package_dir.parent),
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )

            # If no commit found for this directory, it might be new or untracked
            if not commit:
                # Fall back to current HEAD
                commit = (
                    subprocess.check_output(
                        ["git", "rev-parse", "--short", "HEAD"],
                        cwd=str(package_dir.parent),
                        stderr=subprocess.DEVNULL,
                    )
                    .decode()
                    .strip()
                )

            # Check if there are uncommitted changes in this specific directory
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
        """Generate a hash based on multiple package contents using per-directory git tracking.

        Args:
            package_dirs: List of paths to package directories

        Returns:
            A deterministic hash string based on all package contents
        """
        # Option 1: Try to use git commit SHA for these specific directories (fast and reliable)
        try:
            # Get the last commit that touched ANY of these directories
            # Build the path arguments for git log
            dir_paths = " ".join([str(d) for d in package_dirs])
            commit = (
                subprocess.check_output(
                    f'git log -1 --format=%h -- {dir_paths}',
                    cwd=str(package_dirs[0].parent),
                    stderr=subprocess.DEVNULL,
                    shell=True,
                )
                .decode()
                .strip()
            )

            # If no commit found for these directories, fall back to HEAD
            if not commit:
                commit = (
                    subprocess.check_output(
                        ["git", "rev-parse", "--short", "HEAD"],
                        cwd=str(package_dirs[0].parent),
                        stderr=subprocess.DEVNULL,
                    )
                    .decode()
                    .strip()
                )

            # Check for uncommitted changes in any of these specific directories
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

    def __init__(
        self,
        name: str,
        stack: str,
        opts: ResourceOptions = None,
    ):
        super().__init__(
            "custom:base:BaseImagesCodeBuild",
            name,
            None,
            opts,
        )

        # Build context path (project root)
        build_context_path = Path(__file__).parent.parent.parent

        # Get package directories
        dynamo_package_dir = build_context_path / "receipt_dynamo"
        chroma_package_dir = build_context_path / "receipt_chroma"
        upload_package_dir = build_context_path / "receipt_upload"
        places_package_dir = build_context_path / "receipt_places"
        label_package_dir = build_context_path / "receipt_label"

        # Calculate content-based tags for each base image
        dynamo_tag = self.get_content_hash(dynamo_package_dir)
        label_tag = self.get_combined_content_hash(
            [dynamo_package_dir, label_package_dir]
        )
        agent_tag = self.get_combined_content_hash(
            [
                dynamo_package_dir,
                chroma_package_dir,
                upload_package_dir,
                places_package_dir,
                label_package_dir,
            ]
        )

        pulumi.log.info(f"Base image tags: dynamo={dynamo_tag}, label={label_tag}, agent={agent_tag}")

        # ============================================================
        # All base images are SELF-CONTAINED (don't depend on each other)
        # They can all build IN PARALLEL! 🚀
        # ============================================================
        
        # base-receipt-dynamo (self-contained)
        self.dynamo_base_image = CodeBuildDockerImage(
            f"base-receipt-dynamo-{stack}",
            dockerfile_path="infra/base_images/dockerfiles/Dockerfile.receipt_dynamo",
            build_context_path=".",
            source_paths=["receipt_dynamo"],  # Only include receipt_dynamo
            image_tag=dynamo_tag,  # Content-based tag
            platform="linux/arm64",
            sync_mode=False,  # Async build
            opts=ResourceOptions(parent=self),
        )

        # base-receipt-label (self-contained, includes receipt_dynamo + receipt_label)
        self.label_base_image = CodeBuildDockerImage(
            f"base-receipt-label-{stack}",
            dockerfile_path="infra/base_images/dockerfiles/Dockerfile.receipt_label",
            build_context_path=".",
            source_paths=["receipt_dynamo", "receipt_label"],  # Both packages
            image_tag=label_tag,  # Content-based tag
            platform="linux/arm64",
            sync_mode=False,  # Async build
            opts=ResourceOptions(parent=self),
        )

        # base-receipt-agent (self-contained, includes all packages)
        self.agent_base_image = CodeBuildDockerImage(
            f"base-receipt-agent-{stack}",
            dockerfile_path="infra/base_images/dockerfiles/Dockerfile.receipt_agent",
            build_context_path=".",
            source_paths=[
                "receipt_dynamo",
                "receipt_chroma",
                "receipt_upload",
                "receipt_places",
                "receipt_label",
            ],
            image_tag=agent_tag,  # Content-based tag
            platform="linux/arm64",
            sync_mode=False,  # Async build
            opts=ResourceOptions(parent=self),
        )

        # Build dependency graph for base image resolution
        from infra.shared.package_dependencies import DependencyGraph

        self.dependency_graph = DependencyGraph.from_project_root(
            build_context_path
        )

        # Expose repository URLs for reference
        self.dynamo_base_repo = self.dynamo_base_image.ecr_repo
        self.label_base_repo = self.label_base_image.ecr_repo
        self.agent_base_repo = self.agent_base_image.ecr_repo

        # Register outputs
        self.register_outputs(
            {
                "dynamo_base_image_uri": self.dynamo_base_image.image_uri,
                "label_base_image_uri": self.label_base_image.image_uri,
                "agent_base_image_uri": self.agent_base_image.image_uri,
                "dynamo_base_repo_url": self.dynamo_base_image.repository_url,
                "label_base_repo_url": self.label_base_image.repository_url,
                "agent_base_repo_url": self.agent_base_image.repository_url,
            }
        )

    def get_base_image_uri_for_package(
        self, package_name: str
    ) -> Optional[pulumi.Output[str]]:
        """Get the appropriate base image URI for a package.

        Uses the dependency graph to determine which existing base image
        contains the package and its dependencies.

        Args:
            package_name: Name of the package (e.g., "receipt-dynamo", "receipt_label", "receipt-agent")

        Returns:
            Base image URI (Pulumi Output) or None if no suitable base image exists
        """
        # Normalize package name
        normalized = package_name.replace("_", "-")

        # Check if we have a direct base image for this package
        if normalized == "receipt-dynamo":
            return self.dynamo_base_image.image_uri
        if normalized == "receipt-label":
            return self.label_base_image.image_uri
        if normalized == "receipt-agent":
            return self.agent_base_image.image_uri

        # Check if package is included in agent base image (most comprehensive)
        # Agent base includes: receipt_dynamo, receipt_chroma, receipt_upload, receipt_places, receipt_label
        if normalized in ["receipt-chroma", "receipt-upload", "receipt-places"]:
            return self.agent_base_image.image_uri

        # Check if package is included in label base image
        label_packages = self.dependency_graph.get_base_image_packages(
            "receipt_label"
        )
        if normalized in [pkg.replace("_", "-") for pkg in label_packages]:
            return self.label_base_image.image_uri

        # Check if package is included in dynamo base image
        dynamo_packages = self.dependency_graph.get_base_image_packages(
            "receipt-dynamo"
        )
        if normalized in [pkg.replace("_", "-") for pkg in dynamo_packages]:
            return self.dynamo_base_image.image_uri

        # No suitable base image found
        pulumi.log.warn(
            f"No base image found for package {package_name}. "
            "Consider creating a base image for this package."
        )
        return None

