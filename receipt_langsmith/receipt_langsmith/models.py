"""Data models for LangSmith query results."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class StatusCounts:
    """Counts of runs by status."""

    success: int = 0
    error: int = 0
    pending: int = 0

    @property
    def total(self) -> int:
        """Total number of runs."""
        return self.success + self.error + self.pending

    @property
    def success_rate(self) -> float:
        """Success rate as percentage."""
        completed = self.success + self.error
        if completed == 0:
            return 0.0
        return (self.success / completed) * 100

    @property
    def error_rate(self) -> float:
        """Error rate as percentage."""
        completed = self.success + self.error
        if completed == 0:
            return 0.0
        return (self.error / completed) * 100

    def __str__(self) -> str:
        return (
            f"StatusCounts(success={self.success}, error={self.error}, "
            f"pending={self.pending}, success_rate={self.success_rate:.1f}%)"
        )


@dataclass
class RunSummary:
    """Summary of a single run."""

    id: str
    name: str
    status: str
    run_type: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    parent_run_id: Optional[str] = None
    error: Optional[str] = None
    inputs: Optional[Dict[str, Any]] = None
    outputs: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    tags: List[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> Optional[float]:
        """Duration in seconds, or None if not completed."""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None

    @property
    def is_root(self) -> bool:
        """Whether this is a root trace (no parent)."""
        return self.parent_run_id is None


@dataclass
class ProjectSummary:
    """Summary of a LangSmith project."""

    project_name: str
    total_runs: int
    status_counts: StatusCounts
    runs_by_name: Dict[str, StatusCounts] = field(default_factory=dict)
    root_traces: List[RunSummary] = field(default_factory=list)
    errors: List[RunSummary] = field(default_factory=list)
    query_time: datetime = field(default_factory=datetime.now)

    def __str__(self) -> str:
        lines = [
            "=" * 60,
            f"Project: {self.project_name}",
            "=" * 60,
            f"Total runs: {self.total_runs}",
            "",
            "Status breakdown:",
            f"  ✅ Success: {self.status_counts.success} "
            f"({self.status_counts.success_rate:.1f}%)",
            f"  ❌ Error:   {self.status_counts.error} "
            f"({self.status_counts.error_rate:.1f}%)",
            f"  ⏳ Pending: {self.status_counts.pending}",
            "",
        ]

        if self.runs_by_name:
            lines.append("Runs by name:")
            for name, counts in sorted(
                self.runs_by_name.items(),
                key=lambda x: x[1].total,
                reverse=True,
            )[:10]:
                lines.append(
                    f"  {name[:40]:<40} "
                    f"✅{counts.success:>4} "
                    f"❌{counts.error:>4} "
                    f"⏳{counts.pending:>4}"
                )
            lines.append("")

        if self.root_traces:
            lines.append(f"Root traces: {len(self.root_traces)}")
            for trace in self.root_traces[:5]:
                duration = (
                    f"{trace.duration_seconds:.1f}s"
                    if trace.duration_seconds
                    else "running"
                )
                lines.append(f"  - {trace.name}: {trace.status} ({duration})")
            lines.append("")

        if self.errors:
            lines.append(f"Recent errors: {len(self.errors)}")
            for err in self.errors[:3]:
                error_msg = (
                    err.error[:50] + "..."
                    if err.error and len(err.error) > 50
                    else err.error or "No message"
                )
                lines.append(f"  - {err.name}: {error_msg}")

        return "\n".join(lines)


@dataclass
class TraceNode:
    """A node in the trace tree."""

    run: RunSummary
    children: List["TraceNode"] = field(default_factory=list)
    depth: int = 0

    def print_tree(self, indent: str = "") -> str:
        """Print tree structure."""
        lines = []
        status_icon = (
            "✅" if self.run.status == "success"
            else "❌" if self.run.status == "error"
            else "⏳"
        )
        duration = (
            f" ({self.run.duration_seconds:.1f}s)"
            if self.run.duration_seconds
            else ""
        )
        lines.append(f"{indent}{status_icon} {self.run.name}{duration}")

        for i, child in enumerate(self.children):
            is_last = i == len(self.children) - 1
            child_indent = indent + ("    " if is_last else "│   ")
            prefix = "└── " if is_last else "├── "
            child_lines = child.print_tree(indent + prefix)
            lines.append(child_lines)

        return "\n".join(lines)


@dataclass
class TraceTree:
    """Complete trace tree for a root run."""

    root: TraceNode
    total_nodes: int = 0
    status_counts: StatusCounts = field(default_factory=StatusCounts)

    def print_tree(self) -> str:
        """Print the full trace tree."""
        header = [
            "=" * 60,
            f"Trace: {self.root.run.name}",
            f"Total nodes: {self.total_nodes}",
            f"Status: {self.status_counts}",
            "=" * 60,
            "",
        ]
        return "\n".join(header) + self.root.print_tree()

    def __str__(self) -> str:
        return self.print_tree()

