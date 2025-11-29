"""LangSmith client wrapper with automatic authentication."""

import os
import subprocess
import json
from typing import Any, Dict, Iterator, List, Optional

from langsmith import Client
from langsmith.schemas import Run

from .models import (
    ProjectSummary,
    RunSummary,
    StatusCounts,
    TraceNode,
    TraceTree,
)


def _load_api_key_from_pulumi() -> Optional[str]:
    """Load LANGCHAIN_API_KEY from Pulumi config."""
    try:
        # Try to get from Pulumi
        result = subprocess.run(
            [
                "pulumi",
                "config",
                "--json",
                "--show-secrets",
                "--stack",
                "dev",
            ],
            capture_output=True,
            text=True,
            cwd=os.path.expanduser("~/Portfolio/infra"),
            timeout=10,
        )
        if result.returncode == 0:
            config = json.loads(result.stdout)
            key_config = config.get("portfolio:LANGCHAIN_API_KEY", {})
            return key_config.get("value")
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    return None


class LangSmithClient:
    """
    Wrapper around LangSmith client with automatic authentication.

    Attempts to load API key in order:
    1. LANGCHAIN_API_KEY environment variable
    2. Pulumi config (portfolio:LANGCHAIN_API_KEY)
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize LangSmith client.

        Args:
            api_key: Optional API key. If not provided, loads from env/Pulumi.
        """
        self.api_key = api_key or self._get_api_key()
        if not self.api_key:
            raise ValueError(
                "LangSmith API key not found. Set LANGCHAIN_API_KEY env var "
                "or configure in Pulumi."
            )

        # Set env var so langsmith client picks it up
        os.environ["LANGCHAIN_API_KEY"] = self.api_key
        self._client = Client()

    def _get_api_key(self) -> Optional[str]:
        """Get API key from environment or Pulumi."""
        # First try environment
        key = os.environ.get("LANGCHAIN_API_KEY")
        if key:
            return key

        # Then try Pulumi
        return _load_api_key_from_pulumi()

    def list_runs(
        self,
        project_name: str,
        limit: int = 100,  # LangSmith API max is 100
        status: Optional[str] = None,
        run_type: Optional[str] = None,
        name: Optional[str] = None,
        parent_run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> List[RunSummary]:
        """
        List runs from a project with optional filtering.

        Args:
            project_name: Name of the LangSmith project
            limit: Maximum number of runs to return
            status: Filter by status (success, error, pending)
            run_type: Filter by type (chain, llm, etc.)
            name: Filter by run name
            parent_run_id: Filter by parent run ID
            trace_id: Filter by trace ID

        Returns:
            List of RunSummary objects
        """
        kwargs: Dict[str, Any] = {
            "project_name": project_name,
            "limit": limit,
        }

        if trace_id:
            kwargs["trace_id"] = trace_id

        runs = list(self._client.list_runs(**kwargs))

        # Apply client-side filters (LangSmith API doesn't support all filters)
        if status:
            runs = [r for r in runs if r.status == status]
        if run_type:
            runs = [r for r in runs if r.run_type == run_type]
        if name:
            runs = [r for r in runs if r.name == name]
        if parent_run_id:
            runs = [r for r in runs if str(r.parent_run_id) == parent_run_id]

        return [self._run_to_summary(r) for r in runs]

    def get_run(self, run_id: str) -> RunSummary:
        """
        Get a single run by ID.

        Args:
            run_id: The run ID

        Returns:
            RunSummary object
        """
        run = self._client.read_run(run_id)
        return self._run_to_summary(run)

    def get_project_summary(
        self,
        project_name: str,
        limit: int = 100,
    ) -> ProjectSummary:
        """
        Get a summary of a project's runs.

        Args:
            project_name: Name of the LangSmith project
            limit: Maximum number of runs to analyze

        Returns:
            ProjectSummary object
        """
        runs = list(self._client.list_runs(
            project_name=project_name,
            limit=limit,
        ))

        # Calculate status counts
        status_counts = StatusCounts()
        runs_by_name: Dict[str, StatusCounts] = {}
        root_traces: List[RunSummary] = []
        errors: List[RunSummary] = []

        for run in runs:
            # Overall counts
            if run.status == "success":
                status_counts.success += 1
            elif run.status == "error":
                status_counts.error += 1
            else:
                status_counts.pending += 1

            # By name
            if run.name not in runs_by_name:
                runs_by_name[run.name] = StatusCounts()

            name_counts = runs_by_name[run.name]
            if run.status == "success":
                name_counts.success += 1
            elif run.status == "error":
                name_counts.error += 1
            else:
                name_counts.pending += 1

            # Root traces
            if run.parent_run_id is None:
                root_traces.append(self._run_to_summary(run))

            # Errors
            if run.status == "error":
                errors.append(self._run_to_summary(run))

        return ProjectSummary(
            project_name=project_name,
            total_runs=len(runs),
            status_counts=status_counts,
            runs_by_name=runs_by_name,
            root_traces=root_traces[:10],  # Limit to 10
            errors=errors[:10],  # Limit to 10
        )

    def get_trace_tree(self, trace_id: str) -> TraceTree:
        """
        Get the full trace tree for a root run.

        Args:
            trace_id: The root trace ID

        Returns:
            TraceTree object
        """
        # Get all runs in the trace
        runs = list(self._client.list_runs(
            trace_id=trace_id,
            limit=1000,
        ))

        if not runs:
            raise ValueError(f"No runs found for trace {trace_id}")

        # Build tree
        runs_by_id = {str(r.id): r for r in runs}
        children_by_parent: Dict[str, List[Run]] = {}

        for run in runs:
            parent_id = str(run.parent_run_id) if run.parent_run_id else "ROOT"
            if parent_id not in children_by_parent:
                children_by_parent[parent_id] = []
            children_by_parent[parent_id].append(run)

        # Find root
        root_run = None
        for run in runs:
            if run.parent_run_id is None or str(run.id) == trace_id:
                root_run = run
                break

        if not root_run:
            # Use the trace_id run as root
            root_run = self._client.read_run(trace_id)

        def build_node(run: Run, depth: int = 0) -> TraceNode:
            run_id = str(run.id)
            children = children_by_parent.get(run_id, [])
            return TraceNode(
                run=self._run_to_summary(run),
                children=[build_node(c, depth + 1) for c in children],
                depth=depth,
            )

        root_node = build_node(root_run)

        # Calculate counts
        status_counts = StatusCounts()
        for run in runs:
            if run.status == "success":
                status_counts.success += 1
            elif run.status == "error":
                status_counts.error += 1
            else:
                status_counts.pending += 1

        return TraceTree(
            root=root_node,
            total_nodes=len(runs),
            status_counts=status_counts,
        )

    def _run_to_summary(self, run: Run) -> RunSummary:
        """Convert a LangSmith Run to RunSummary."""
        return RunSummary(
            id=str(run.id),
            name=run.name,
            status=run.status,
            run_type=run.run_type,
            start_time=run.start_time,
            end_time=run.end_time,
            parent_run_id=(
                str(run.parent_run_id) if run.parent_run_id else None
            ),
            error=run.error,
            inputs=run.inputs,
            outputs=run.outputs,
            metadata=(
                run.extra.get("metadata") if run.extra else None
            ),
            tags=run.tags or [],
        )

