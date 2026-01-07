"""Trace tree builder for reconstructing parent-child hierarchy.

This module provides utilities for building trace trees from flat
lists of LangSmithRun objects.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, Optional

from receipt_langsmith.entities.base import LangSmithRun


class TraceTreeBuilder:
    """Reconstruct parent-child trace hierarchy.

    This class takes a flat list of runs and builds a tree structure
    where each run has its children populated.

    Args:
        runs: List of LangSmithRun objects.

    Example:
        ```python
        reader = ParquetReader(bucket="...")
        runs = reader.read_all_traces()

        builder = TraceTreeBuilder(runs)

        # Get root traces by name
        roots = builder.get_root_runs(name_filter="ReceiptEvaluation")

        # Build full tree for each root
        for root in roots:
            tree = builder.build_tree(root)
            print(f"{tree.name} has {len(tree.children)} children")
        ```
    """

    def __init__(self, runs: list[LangSmithRun]):
        self.runs = runs
        self._by_id: dict[str, LangSmithRun] = {}
        self._children: dict[str, list[LangSmithRun]] = defaultdict(list)
        self._build_index()

    def _build_index(self) -> None:
        """Build lookup indices for efficient tree construction."""
        for run in self.runs:
            self._by_id[run.id] = run
            if run.parent_run_id:
                self._children[run.parent_run_id].append(run)

    def get_run_by_id(self, run_id: str) -> Optional[LangSmithRun]:
        """Get a run by its ID.

        Args:
            run_id: The run UUID.

        Returns:
            The run if found, None otherwise.
        """
        return self._by_id.get(run_id)

    def get_root_runs(
        self,
        name_filter: Optional[str] = None,
        run_type_filter: Optional[str] = None,
    ) -> list[LangSmithRun]:
        """Get root (parent-less) runs.

        Args:
            name_filter: Only return roots with this exact name.
            run_type_filter: Only return roots with this run type.

        Returns:
            List of root runs matching filters.
        """
        roots = [r for r in self.runs if r.parent_run_id is None]

        if name_filter:
            roots = [r for r in roots if r.name == name_filter]

        if run_type_filter:
            roots = [r for r in roots if r.run_type == run_type_filter]

        return roots

    def get_children(self, run_id: str) -> list[LangSmithRun]:
        """Get direct children of a run.

        Args:
            run_id: Parent run ID.

        Returns:
            List of direct children.
        """
        return self._children.get(run_id, [])

    def get_children_by_name(self, run_id: str) -> dict[str, LangSmithRun]:
        """Get children of a run, mapped by name.

        Args:
            run_id: Parent run ID.

        Returns:
            Dict mapping child name to run.
            Note: If multiple children have the same name, only one is returned.
        """
        return {c.name: c for c in self.get_children(run_id)}

    def get_child_by_name(
        self,
        run_id: str,
        name: str,
    ) -> Optional[LangSmithRun]:
        """Get a specific child by name.

        Args:
            run_id: Parent run ID.
            name: Child run name.

        Returns:
            The child run if found, None otherwise.
        """
        for child in self.get_children(run_id):
            if child.name == name:
                return child
        return None

    def build_tree(self, root: LangSmithRun) -> LangSmithRun:
        """Recursively build tree from root, populating children.

        This modifies the root run in-place by populating its `children`
        field with all descendants.

        Args:
            root: The root run to build tree for.

        Returns:
            The same root run with children populated.
        """
        root.children = self.get_children(root.id)
        for child in root.children:
            self.build_tree(child)
        return root

    def build_all_trees(
        self,
        name_filter: Optional[str] = None,
    ) -> list[LangSmithRun]:
        """Build trees for all root runs.

        Args:
            name_filter: Only build trees for roots with this name.

        Returns:
            List of root runs with children populated.
        """
        roots = self.get_root_runs(name_filter=name_filter)
        return [self.build_tree(root) for root in roots]

    def traverse(
        self,
        root: LangSmithRun,
        visitor: Callable[[LangSmithRun, int], None],
        depth: int = 0,
    ) -> None:
        """Traverse a tree depth-first, calling visitor for each node.

        Args:
            root: The root run to traverse from.
            visitor: Callback function(run, depth) called for each node.
            depth: Current depth (used internally).
        """
        visitor(root, depth)
        for child in root.children:
            self.traverse(child, visitor, depth + 1)

    def get_all_descendants(self, run_id: str) -> list[LangSmithRun]:
        """Get all descendants of a run (children, grandchildren, etc.).

        Args:
            run_id: Parent run ID.

        Returns:
            List of all descendant runs.
        """
        descendants: list[LangSmithRun] = []

        def collect(run: LangSmithRun, _: int) -> None:
            if run.id != run_id:  # Don't include the root itself
                descendants.append(run)

        root = self.get_run_by_id(run_id)
        if root:
            self.build_tree(root)
            self.traverse(root, collect)

        return descendants

    def get_trace_summary(self, trace_id: str) -> dict:
        """Get a summary of a trace (all runs sharing the same trace_id).

        Args:
            trace_id: The trace ID.

        Returns:
            Dict with summary statistics.
        """
        runs_in_trace = [r for r in self.runs if r.trace_id == trace_id]

        total_tokens = sum(r.total_tokens for r in runs_in_trace)
        total_duration = sum(r.duration_ms for r in runs_in_trace)
        llm_runs = [r for r in runs_in_trace if r.run_type == "llm"]

        return {
            "trace_id": trace_id,
            "run_count": len(runs_in_trace),
            "llm_run_count": len(llm_runs),
            "total_tokens": total_tokens,
            "total_duration_ms": total_duration,
            "unique_names": list({r.name for r in runs_in_trace}),
        }
