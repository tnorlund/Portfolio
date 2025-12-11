"""
Helper functions for resolving base images for container Lambda functions.
"""

from __future__ import annotations

from typing import Optional

import pulumi

from infra.shared.package_dependencies import DependencyGraph


def resolve_base_image_for_packages(
    base_images: dict[str, pulumi.Output[str]],
    dependency_graph: DependencyGraph,
    required_packages: list[str],
) -> Optional[pulumi.Output[str]]:
    """Resolve the best base image for a set of required packages.

    This function finds the smallest base image that contains all required
    packages and their dependencies.

    Args:
        base_images: Dictionary mapping package names to base image URIs
        dependency_graph: DependencyGraph instance
        required_packages: List of package names required by the Lambda

    Returns:
        Base image URI (Pulumi Output) or None if no suitable base image exists
    """
    # Normalize package names
    normalized_required = [pkg.replace("_", "-") for pkg in required_packages]

    # Find all transitive dependencies
    all_required: set[str] = set()
    for pkg in normalized_required:
        all_required.add(pkg)
        deps = dependency_graph.get_transitive_dependencies(pkg)
        all_required.update(deps)

    # Find the smallest base image that contains all required packages
    best_match: Optional[tuple[str, pulumi.Output[str], int]] = None

    for base_pkg_name, base_image_uri in base_images.items():
        base_packages = set(
            dependency_graph.get_base_image_packages(base_pkg_name)
        )
        base_packages_normalized = {
            pkg.replace("_", "-") for pkg in base_packages
        }

        # Check if this base image contains all required packages
        if all_required.issubset(base_packages_normalized):
            # This base image works - prefer the smallest one
            size = len(base_packages)
            if best_match is None or size < best_match[2]:
                best_match = (base_pkg_name, base_image_uri, size)

    if best_match:
        return best_match[1]

    # No suitable base image found
    pulumi.log.warn(
        f"No base image found for packages {required_packages}. "
        "Consider creating a base image that includes these packages."
    )
    return None
