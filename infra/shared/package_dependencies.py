"""
Package dependency graph builder for base image optimization.

This module parses pyproject.toml files to build a dependency graph of
in-repo packages, enabling automatic base image selection and rebuild
propagation for container-based Lambda functions.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


class DependencyGraph:
    """Represents the dependency graph of in-repo packages."""

    # Map of package name (as in pyproject.toml) to directory name
    PACKAGE_NAME_TO_DIR: Dict[str, str] = {
        "receipt-dynamo": "receipt_dynamo",
        "receipt_label": "receipt_label",
        "receipt-chroma": "receipt_chroma",
        "receipt_upload": "receipt_upload",
        "receipt_places": "receipt_places",
        "receipt-agent": "receipt_agent",
    }

    # Reverse mapping: directory name to package name
    DIR_TO_PACKAGE_NAME: Dict[str, str] = {
        v: k for k, v in PACKAGE_NAME_TO_DIR.items()
    }

    def __init__(self, project_root: Path):
        """Initialize dependency graph from project root.

        Args:
            project_root: Path to the monorepo root directory
        """
        self.project_root = project_root.resolve()
        self.graph: Dict[str, Set[str]] = defaultdict(
            set
        )  # package -> set of dependencies
        self.reverse_graph: Dict[str, Set[str]] = defaultdict(
            set
        )  # package -> set of dependents
        self.package_paths: Dict[str, Path] = {}
        self._build_graph()

    def get_content_hash(self, package_dir: Path) -> str:
        """Get content hash for a package directory using per-directory git tracking.

        Args:
            package_dir: Path to package directory

        Returns:
            Content hash string
        """
        import hashlib
        import subprocess

        # Try git commit SHA for this specific directory first
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
            
            # If no commit found for this directory, fall back to HEAD
            if not commit:
                commit = (
                    subprocess.check_output(
                        ["git", "rev-parse", "--short", "HEAD"],
                        cwd=str(package_dir.parent),
                        stderr=subprocess.DEVNULL,
                    )
                    .decode()
                    .strip()
                )
            
            # Check for uncommitted changes in this specific directory
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
                return f"git-{commit}-dirty"
            return f"git-{commit}"
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        # Fallback to file hash
        package_files = list(package_dir.rglob("*.py"))
        content_hash = hashlib.sha256()
        for file_path in sorted(package_files):
            if file_path.is_file() and not file_path.name.startswith("test_"):
                rel_path = file_path.relative_to(package_dir)
                content_hash.update(str(rel_path).encode())
                content_hash.update(file_path.read_bytes())
        return f"sha-{content_hash.hexdigest()[:12]}"

    def get_combined_content_hash(self, package_dirs: list[Path]) -> str:
        """Get combined content hash for multiple package directories.

        Args:
            package_dirs: List of package directory paths

        Returns:
            Combined content hash string
        """
        import hashlib
        import subprocess

        # Try git commit SHA first
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

        # Fallback to file hash
        content_hash = hashlib.sha256()
        for package_dir in sorted(package_dirs):
            package_files = list(package_dir.rglob("*.py"))
            for file_path in sorted(package_files):
                if file_path.is_file() and not file_path.name.startswith(
                    "test_"
                ):
                    rel_path = file_path.relative_to(package_dir.parent)
                    content_hash.update(str(rel_path).encode())
                    content_hash.update(file_path.read_bytes())
        return f"sha-{content_hash.hexdigest()[:12]}"

    def _parse_pyproject(
        self, pyproject_path: Path
    ) -> Optional[Tuple[str, List[str]]]:
        """Parse a pyproject.toml file to extract package name and dependencies.

        Args:
            pyproject_path: Path to pyproject.toml file

        Returns:
            Tuple of (package_name, list of in-repo dependencies) or None if invalid
        """
        if not pyproject_path.exists():
            return None

        try:
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            return None

        project = data.get("project", {})
        package_name = project.get("name")
        if not package_name:
            return None

        # Extract dependencies
        dependencies = project.get("dependencies", [])
        in_repo_deps: List[str] = []

        for dep in dependencies:
            # Handle both string and dict formats
            if isinstance(dep, str):
                dep_name = (
                    dep.split(">=")[0].split("==")[0].split("~=")[0].strip()
                )
            elif isinstance(dep, dict):
                dep_name = dep.get("name", "").strip()
            else:
                continue

            # Check if this is an in-repo package
            if dep_name in self.PACKAGE_NAME_TO_DIR:
                in_repo_deps.append(dep_name)

        return package_name, in_repo_deps

    def _detect_receipt_agent_dependencies(self) -> List[str]:
        """Detect receipt_agent dependencies by scanning imports.

        Since receipt_agent doesn't have a pyproject.toml, we scan imports
        to determine dependencies.

        Returns:
            List of in-repo package names that receipt_agent depends on
        """
        agent_dir = self.project_root / "receipt_agent" / "receipt_agent"
        if not agent_dir.exists():
            return []

        dependencies: Set[str] = set()

        # Scan Python files for imports
        for py_file in agent_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
                # Look for imports from in-repo packages (excluding receipt_agent itself)
                for (
                    package_dir,
                    package_name,
                ) in self.DIR_TO_PACKAGE_NAME.items():
                    # Skip self-imports
                    if package_dir == "receipt_agent":
                        continue
                    if (
                        f"from {package_dir}" in content
                        or f"import {package_dir}" in content
                    ):
                        dependencies.add(package_name)
            except (OSError, UnicodeDecodeError):
                continue

        return list(dependencies)

    def _build_graph(self) -> None:
        """Build the dependency graph by parsing all pyproject.toml files."""
        # Find all package directories
        for package_name, package_dir in self.PACKAGE_NAME_TO_DIR.items():
            package_path = self.project_root / package_dir
            if package_path.exists():
                self.package_paths[package_name] = package_path

        # Parse pyproject.toml files
        for package_name, package_path in self.package_paths.items():
            pyproject_path = package_path / "pyproject.toml"

            if package_name == "receipt-agent":
                # Special handling for receipt_agent (no pyproject.toml)
                deps = self._detect_receipt_agent_dependencies()
            else:
                result = self._parse_pyproject(pyproject_path)
                if result is None:
                    continue
                _, deps = result

            # Add dependencies to graph
            for dep in deps:
                if dep in self.package_paths:
                    self.graph[package_name].add(dep)
                    self.reverse_graph[dep].add(package_name)

        # Ensure all packages are in the graph (even if no dependencies)
        for package_name in self.package_paths:
            if package_name not in self.graph:
                self.graph[package_name] = set()
            if package_name not in self.reverse_graph:
                self.reverse_graph[package_name] = set()

    def get_dependencies(self, package_name: str) -> Set[str]:
        """Get direct dependencies of a package.

        Args:
            package_name: Name of the package

        Returns:
            Set of package names that this package depends on
        """
        return self.graph.get(package_name, set()).copy()

    def get_dependents(self, package_name: str) -> Set[str]:
        """Get packages that depend on this package.

        Args:
            package_name: Name of the package

        Returns:
            Set of package names that depend on this package
        """
        return self.reverse_graph.get(package_name, set()).copy()

    def get_transitive_dependencies(self, package_name: str) -> Set[str]:
        """Get all transitive dependencies (dependencies of dependencies).

        Args:
            package_name: Name of the package

        Returns:
            Set of all transitive dependency package names
        """
        visited: Set[str] = set()
        stack = [package_name]

        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)

            for dep in self.graph.get(current, set()):
                if dep not in visited:
                    stack.append(dep)

        # Remove the package itself
        visited.discard(package_name)
        return visited

    def get_transitive_dependents(self, package_name: str) -> Set[str]:
        """Get all transitive dependents (dependents of dependents).

        Args:
            package_name: Name of the package

        Returns:
            Set of all transitive dependent package names
        """
        visited: Set[str] = set()
        stack = [package_name]

        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)

            for dependent in self.reverse_graph.get(current, set()):
                if dependent not in visited:
                    stack.append(dependent)

        # Remove the package itself
        visited.discard(package_name)
        return visited

    def detect_cycles(self) -> List[List[str]]:
        """Detect cycles in the dependency graph.

        Returns:
            List of cycles, where each cycle is a list of package names
        """
        cycles: List[List[str]] = []
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def dfs(node: str, path: List[str]) -> None:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for dep in self.graph.get(node, set()):
                # Skip self-references (not a real cycle)
                if dep == node:
                    continue
                if dep not in visited:
                    dfs(dep, path)
                elif dep in rec_stack:
                    # Found a cycle (only if it's not a self-reference)
                    cycle_start = path.index(dep)
                    cycle = path[cycle_start:] + [dep]
                    # Only add if cycle has at least 2 nodes
                    if len(set(cycle)) > 1:
                        cycles.append(cycle)

            rec_stack.remove(node)
            path.pop()

        for package_name in self.package_paths:
            if package_name not in visited:
                dfs(package_name, [])

        return cycles

    def topological_sort(self) -> List[str]:
        """Perform topological sort of packages.

        Returns:
            List of package names in topological order (dependencies before dependents)

        Raises:
            ValueError: If there are cycles in the graph
        """
        cycles = self.detect_cycles()
        if cycles:
            raise ValueError(f"Dependency graph contains cycles: {cycles}")

        # Kahn's algorithm
        in_degree: Dict[str, int] = {
            pkg: len(self.graph.get(pkg, set())) for pkg in self.package_paths
        }
        queue = [pkg for pkg, degree in in_degree.items() if degree == 0]
        result: List[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)

            for dependent in self.reverse_graph.get(node, set()):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(result) != len(self.package_paths):
            raise ValueError("Topological sort failed - graph may have cycles")

        return result

    def get_base_image_packages(self, package_name: str) -> List[str]:
        """Get the list of packages that should be included in the base image.

        This includes the package itself and all its transitive dependencies,
        sorted in topological order.

        Args:
            package_name: Name of the package

        Returns:
            List of package names to include in base image (in build order)
        """
        deps = self.get_transitive_dependencies(package_name)
        deps.add(package_name)

        # Sort dependencies topologically
        subgraph = {pkg: self.graph.get(pkg, set()) & deps for pkg in deps}
        sorted_deps = self._topological_sort_subgraph(subgraph)

        return sorted_deps

    def _topological_sort_subgraph(
        self, subgraph: Dict[str, Set[str]]
    ) -> List[str]:
        """Topologically sort a subgraph.

        Args:
            subgraph: Dictionary mapping package names to their dependencies

        Returns:
            List of package names in topological order
        """
        in_degree: Dict[str, int] = {
            pkg: len(deps) for pkg, deps in subgraph.items()
        }
        queue = [pkg for pkg, degree in in_degree.items() if degree == 0]
        result: List[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)

            # Find dependents in the subgraph
            for pkg, deps in subgraph.items():
                if node in deps and pkg not in result:
                    in_degree[pkg] -= 1
                    if in_degree[pkg] == 0:
                        queue.append(pkg)

        return result

    def to_dict(self) -> Dict:
        """Convert graph to dictionary for serialization.

        Returns:
            Dictionary representation of the graph
        """
        return {
            "packages": {
                name: str(path) for name, path in self.package_paths.items()
            },
            "dependencies": {
                pkg: list(deps) for pkg, deps in self.graph.items()
            },
            "dependents": {
                pkg: list(deps) for pkg, deps in self.reverse_graph.items()
            },
        }

    @classmethod
    def from_project_root(
        cls, project_root: Optional[Path] = None
    ) -> DependencyGraph:
        """Create a DependencyGraph from the project root.

        Args:
            project_root: Optional path to project root. If None, auto-detects.

        Returns:
            DependencyGraph instance
        """
        if project_root is None:
            # Try to find project root
            current = Path.cwd()
            while current != current.parent:
                if (current / "receipt_dynamo").exists() and (
                    current / "infra"
                ).exists():
                    project_root = current
                    break
                current = current.parent
            else:
                raise ValueError("Could not find project root")

        return cls(project_root)
