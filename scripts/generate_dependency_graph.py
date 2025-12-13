#!/usr/bin/env python3
"""
Generate and output the package dependency graph as JSON.

This script can be used to visualize the dependency graph and understand
which packages depend on which other packages.
"""

import json
import sys
from pathlib import Path

# Add infra to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from infra.shared.package_dependencies import DependencyGraph


def main():
    """Generate and print dependency graph as JSON."""
    try:
        graph = DependencyGraph.from_project_root(project_root)

        # Check for cycles
        cycles = graph.detect_cycles()
        if cycles:
            print(
                "WARNING: Dependency graph contains cycles:", file=sys.stderr
            )
            for cycle in cycles:
                print(f"  {' -> '.join(cycle)}", file=sys.stderr)
            sys.exit(1)

        # Convert to dict and print as JSON
        graph_dict = graph.to_dict()

        # Add additional metadata
        graph_dict["metadata"] = {
            "total_packages": len(graph.package_paths),
            "topological_order": graph.topological_sort(),
        }

        # Print as formatted JSON
        print(json.dumps(graph_dict, indent=2, sort_keys=True))

    except Exception as e:
        print(f"Error generating dependency graph: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
