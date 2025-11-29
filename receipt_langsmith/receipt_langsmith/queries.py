"""Common query functions for LangSmith analysis."""

from collections import defaultdict
from typing import Dict, List, Optional

from .client import LangSmithClient
from .models import ProjectSummary, StatusCounts


def summarize_project(
    project_name: str,
    limit: int = 500,
    client: Optional[LangSmithClient] = None,
) -> ProjectSummary:
    """
    Get a summary of a LangSmith project.

    Args:
        project_name: Name of the project
        limit: Max runs to analyze
        client: Optional client instance

    Returns:
        ProjectSummary with status counts, root traces, and errors
    """
    if client is None:
        client = LangSmithClient()

    return client.get_project_summary(project_name, limit)


def get_error_summary(
    project_name: str,
    limit: int = 100,
    client: Optional[LangSmithClient] = None,
) -> Dict[str, List[str]]:
    """
    Get a summary of errors grouped by error type.

    Args:
        project_name: Name of the project
        limit: Max errors to analyze
        client: Optional client instance

    Returns:
        Dict mapping error patterns to list of run names
    """
    if client is None:
        client = LangSmithClient()

    runs = client.list_runs(project_name, limit=limit, status="error")

    errors_by_pattern: Dict[str, List[str]] = defaultdict(list)
    for run in runs:
        if run.error:
            # Extract error pattern (first line or first 50 chars)
            pattern = run.error.split("\n")[0][:50]
            errors_by_pattern[pattern].append(run.name)

    return dict(errors_by_pattern)


def get_llm_stats(
    project_name: str,
    llm_name: str = "ChatOllama",
    limit: int = 500,
    client: Optional[LangSmithClient] = None,
) -> Dict[str, any]:
    """
    Get statistics for LLM calls in a project.

    Args:
        project_name: Name of the project
        llm_name: Name of LLM run type to analyze
        limit: Max runs to analyze
        client: Optional client instance

    Returns:
        Dict with success_rate, error_rate, avg_duration, etc.
    """
    if client is None:
        client = LangSmithClient()

    runs = client.list_runs(project_name, limit=limit, name=llm_name)

    if not runs:
        return {
            "llm_name": llm_name,
            "total": 0,
            "success": 0,
            "error": 0,
            "success_rate": 0.0,
            "error_rate": 0.0,
            "avg_duration_seconds": None,
        }

    success = sum(1 for r in runs if r.status == "success")
    error = sum(1 for r in runs if r.status == "error")
    total = len(runs)

    durations = [
        r.duration_seconds
        for r in runs
        if r.duration_seconds is not None
    ]
    avg_duration = sum(durations) / len(durations) if durations else None

    return {
        "llm_name": llm_name,
        "total": total,
        "success": success,
        "error": error,
        "pending": total - success - error,
        "success_rate": (success / (success + error) * 100)
        if (success + error) > 0
        else 0.0,
        "error_rate": (error / (success + error) * 100)
        if (success + error) > 0
        else 0.0,
        "avg_duration_seconds": avg_duration,
    }


def compare_runs(
    project_name: str,
    name_a: str,
    name_b: str,
    limit: int = 500,
    client: Optional[LangSmithClient] = None,
) -> Dict[str, Dict[str, any]]:
    """
    Compare statistics between two run types.

    Args:
        project_name: Name of the project
        name_a: First run name to compare
        name_b: Second run name to compare
        limit: Max runs to analyze
        client: Optional client instance

    Returns:
        Dict with stats for each run type
    """
    if client is None:
        client = LangSmithClient()

    summary = client.get_project_summary(project_name, limit)

    result = {}
    for name in [name_a, name_b]:
        counts = summary.runs_by_name.get(name, StatusCounts())
        result[name] = {
            "total": counts.total,
            "success": counts.success,
            "error": counts.error,
            "pending": counts.pending,
            "success_rate": counts.success_rate,
            "error_rate": counts.error_rate,
        }

    return result

