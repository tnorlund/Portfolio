# receipt-langsmith

A Python interface for querying and analyzing LangSmith traces for receipt processing workflows.

## Installation

```bash
pip install -e ./receipt_langsmith
```

## Quick Start

```python
from receipt_langsmith import LangSmithClient, summarize_project

# Initialize client (auto-loads API key from env or Pulumi)
client = LangSmithClient()

# Get project summary
summary = client.get_project_summary("label-harmonizer-v2-test-batch")
print(summary)
```

Output:
```
============================================================
Project: label-harmonizer-v2-test-batch
============================================================
Total runs: 500
Status breakdown:
  ✅ Success: 200 (50.0%)
  ❌ Error:   150 (37.5%)
  ⏳ Pending: 150

Runs by name:
  ChatOllama                               ✅  50 ❌  30 ⏳  20
  _llm_determine_outlier                   ✅  40 ❌  25 ⏳  15
  ...
```

## API Reference

### LangSmithClient

The main client for querying LangSmith.

```python
from receipt_langsmith import LangSmithClient

client = LangSmithClient()

# List runs with filtering
runs = client.list_runs(
    project_name="my-project",
    limit=100,
    status="error",  # Filter by status
    name="ChatOllama",  # Filter by run name
)

# Get a single run
run = client.get_run("run-id-here")

# Get project summary
summary = client.get_project_summary("my-project")

# Get trace tree
tree = client.get_trace_tree("trace-id-here")
print(tree)  # Prints tree structure
```

### Query Functions

Convenience functions for common queries:

```python
from receipt_langsmith import (
    summarize_project,
    get_error_summary,
    get_llm_stats,
)

# Project summary
summary = summarize_project("my-project")

# Error summary grouped by pattern
errors = get_error_summary("my-project")
# Returns: {"too many concurrent requests": ["ChatOllama", ...]}

# LLM-specific stats
stats = get_llm_stats("my-project", llm_name="ChatOllama")
# Returns: {"success_rate": 85.2, "avg_duration_seconds": 5.3, ...}
```

### Models

Data classes for query results:

- `ProjectSummary` - Overall project statistics
- `RunSummary` - Summary of a single run
- `StatusCounts` - Counts by status (success/error/pending)
- `TraceTree` - Full trace tree structure
- `TraceNode` - Node in the trace tree

## Authentication

The client loads the API key in order:

1. `LANGCHAIN_API_KEY` environment variable
2. Pulumi config (`portfolio:LANGCHAIN_API_KEY`)

Or provide directly:
```python
client = LangSmithClient(api_key="lsv2_pt_...")
```

## CLI Usage

```bash
# Install
pip install -e ./receipt_langsmith

# Use from Python
python -c "
from receipt_langsmith import summarize_project
print(summarize_project('label-harmonizer-v2-test-batch'))
"
```

