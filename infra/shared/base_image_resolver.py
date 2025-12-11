"""
Base image resolver for container Lambda functions.

This module provides utilities to resolve the correct base image URI
for a given package based on the dependency graph.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import pulumi

from infra.shared.package_dependencies import DependencyGraph


class BaseImageResolver:
    """Resolves base image URIs for packages based on dependency graph."""

    def __init__(
        self,
        base_images: Dict[str, pulumi.Output[str]],
        dependency_graph: DependencyGraph,
    ):
        """Initialize resolver with base images and dependency graph.

        Args:
            base_images: Dictionary mapping package names to their base image URIs
            dependency_graph: DependencyGraph instance
        """
        self.base_images = base_images
        self.dependency_graph = dependency_graph

    def get_base_image_uri(
        self, package_name: str
    ) -> Optional[pulumi.Output[str]]:
        """Get the base image URI for a package.

        This returns the base image that contains the package and all its
        transitive dependencies.

        Args:
            package_name: Name of the package (e.g., "receipt-dynamo")

        Returns:
            Base image URI (Pulumi Output) or None if no base image exists
        """
        # Normalize package name
        normalized_name = self._normalize_package_name(package_name)

        # Check if we have a base image for this package
        if normalized_name in self.base_images:
            return self.base_images[normalized_name]

        # Try to find a base image that includes this package
        # (e.g., if receipt_label depends on receipt_dynamo, use receipt_label base)
        for base_pkg_name, base_image_uri in self.base_images.items():
            base_packages = self.dependency_graph.get_base_image_packages(
                base_pkg_name
            )
            if normalized_name in base_packages:
                return base_image_uri

        return None

    def _normalize_package_name(self, package_name: str) -> str:
        """Normalize package name to match graph format.

        Args:
            package_name: Package name (may be directory name or package name)

        Returns:
            Normalized package name
        """
        # Check if it's already a package name
        if package_name in self.dependency_graph.package_paths:
            return package_name

        # Check if it's a directory name
        if package_name in DependencyGraph.DIR_TO_PACKAGE_NAME:
            return DependencyGraph.DIR_TO_PACKAGE_NAME[package_name]

        # Try to match by removing underscores/hyphens
        normalized = package_name.replace("_", "-")
        if normalized in self.dependency_graph.package_paths:
            return normalized

        return package_name

    def get_packages_for_base_image(self, package_name: str) -> list[str]:
        """Get the list of packages included in a base image.

        Args:
            package_name: Name of the package

        Returns:
            List of package names included in the base image
        """
        normalized_name = self._normalize_package_name(package_name)
        return self.dependency_graph.get_base_image_packages(normalized_name)
