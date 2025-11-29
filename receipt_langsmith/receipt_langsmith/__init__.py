"""
receipt_langsmith - LangSmith query utilities for receipt processing.

This package provides a clean, modular interface for querying and analyzing
LangSmith traces from receipt processing workflows.

Usage:
------
```python
from receipt_langsmith import LangSmithClient, ProjectSummary

# Initialize client (auto-loads API key from env or Pulumi)
client = LangSmithClient()

# Get project summary
summary = client.get_project_summary("label-harmonizer-v2-test-batch")
print(summary)

# Get traces with filtering
traces = client.list_traces(
    project_name="label-harmonizer-v2-test-batch",
    limit=100,
    status="error",
)

# Analyze trace tree
tree = client.get_trace_tree("019ac629-184b-749d-99ba-08c29ec6f64b")
tree.print_tree()
```
"""

from .client import LangSmithClient
from .models import (
    ProjectSummary,
    RunSummary,
    StatusCounts,
    TraceTree,
    TraceNode,
)
from .queries import (
    summarize_project,
    get_error_summary,
    get_llm_stats,
)

__all__ = [
    # Client
    "LangSmithClient",
    # Models
    "ProjectSummary",
    "RunSummary",
    "StatusCounts",
    "TraceTree",
    "TraceNode",
    # Query functions
    "summarize_project",
    "get_error_summary",
    "get_llm_stats",
]

__version__ = "0.1.0"

