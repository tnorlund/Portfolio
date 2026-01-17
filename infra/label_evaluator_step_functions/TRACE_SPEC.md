# LangSmith Trace Architecture Specification

## Overview

This specification outlines the plan to fix and enhance LangSmith tracing for the Label Evaluator Step Function, ensuring:
1. Per-receipt traces (Phase 2) land correctly with all child steps
2. Per-merchant traces (Phase 1) are added for pattern computation
3. EMR analytics job processes both "job types" correctly
4. Local development workflow using exported Parquet files

## Current State (Baseline)

### Trace Coverage
| Component | Has Tracing | Status |
|-----------|-------------|--------|
| `discover_patterns` (Phase 1) | NO | Needs implementation |
| `compute_patterns` (Phase 1) | NO | Needs implementation |
| `unified_receipt_evaluator` (Phase 2) | YES | Has orphan bug |

### Known Issues
- 69% of `upload_results` traces are orphaned (parent doesn't exist)
- Only ~1% of traces have complete child hierarchies
- Phase 1 has zero tracing

---

## Phase 1: Verify Current Trace State

### 1.1 Deploy Current Code
```bash
cd /Users/tnorlund/portfolio_sagemaker
pulumi up --yes --stack dev
```

### 1.2 Start Test Execution with New LangSmith Project
Create a dedicated project for testing to isolate traces:
```bash
# Start step function with specific project name and limited receipts
aws stepfunctions start-execution \
  --state-machine-arn "$(pulumi stack output label_evaluator_sf_arn --stack dev)" \
  --input '{
    "langchain_project": "label-evaluator-trace-test-001",
    "merchants": [{"merchant_name": "mcdonalds", "labels": ["grand_total"], "max_receipts": 3}]
  }'
```

### 1.3 Read Traces via LangSmith API
```python
#!/usr/bin/env python3
"""Read traces from LangSmith API to verify structure."""
import os
import json
import subprocess

# Get API key from Pulumi secrets
result = subprocess.run(
    ["pulumi", "config", "get", "portfolio:LANGCHAIN_API_KEY", "--stack", "dev"],
    capture_output=True, text=True
)
API_KEY = result.stdout.strip()

import urllib.request

def list_projects():
    """List all LangSmith projects."""
    req = urllib.request.Request(
        "https://api.smith.langchain.com/api/v1/sessions",
        headers={"x-api-key": API_KEY}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def get_runs(project_name: str, limit: int = 100):
    """Get runs from a project."""
    # First get project ID
    projects = list_projects()
    project_id = next((p["id"] for p in projects if p["name"] == project_name), None)
    if not project_id:
        raise ValueError(f"Project not found: {project_name}")

    req = urllib.request.Request(
        f"https://api.smith.langchain.com/api/v1/runs?session_id={project_id}&limit={limit}",
        headers={"x-api-key": API_KEY}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def analyze_traces(runs: list):
    """Analyze trace structure for orphans and completeness."""
    run_ids = {r["id"] for r in runs}

    results = {
        "total_runs": len(runs),
        "by_name": {},
        "orphaned": [],
        "complete_traces": [],
        "incomplete_traces": []
    }

    # Group by name
    for run in runs:
        name = run["name"]
        results["by_name"][name] = results["by_name"].get(name, 0) + 1

        # Check for orphans
        parent_id = run.get("parent_run_id")
        if parent_id and parent_id not in run_ids:
            results["orphaned"].append({
                "id": run["id"][:8],
                "name": name,
                "parent_id": parent_id[:8]
            })

    # Analyze trace completeness
    trace_ids = set(r["trace_id"] for r in runs)
    for trace_id in trace_ids:
        trace_runs = [r for r in runs if r["trace_id"] == trace_id]
        names = [r["name"] for r in trace_runs]

        expected_children = [
            "currency_evaluation", "metadata_evaluation", "geometric_evaluation",
            "apply_phase1_corrections", "phase2_financial_validation", "upload_results"
        ]

        if "ReceiptEvaluation" in names:
            missing = [n for n in expected_children if n not in names]
            if not missing:
                results["complete_traces"].append(trace_id[:8])
            else:
                results["incomplete_traces"].append({
                    "trace_id": trace_id[:8],
                    "has": names,
                    "missing": missing
                })

    return results

if __name__ == "__main__":
    PROJECT = "label-evaluator-trace-test-001"
    runs = get_runs(PROJECT)
    analysis = analyze_traces(runs["runs"])
    print(json.dumps(analysis, indent=2))
```

### 1.4 Expected Verification Output
```json
{
  "total_runs": 21,
  "by_name": {
    "ReceiptEvaluation": 3,
    "currency_evaluation": 3,
    "metadata_evaluation": 3,
    "geometric_evaluation": 3,
    "apply_phase1_corrections": 3,
    "phase2_financial_validation": 3,
    "upload_results": 3
  },
  "orphaned": [],
  "complete_traces": ["abc12345", "def67890", "ghi11111"],
  "incomplete_traces": []
}
```

---

## Phase 2: Fix Receipt Traces (Per-Receipt Parent)

### 2.1 Goal
Each receipt gets ONE parent trace (`ReceiptEvaluation`) with ALL child steps nested underneath:

```
ReceiptEvaluation (Job: Phase 2 - Receipt Evaluation)
├── load_patterns
├── build_visual_lines
├── setup_llm
├── currency_evaluation
├── metadata_evaluation
├── geometric_evaluation
├── apply_phase1_corrections
├── phase2_financial_validation
├── phase3_llm_review (if issues found)
└── upload_results
```

### 2.2 Files to Modify
- `infra/label_evaluator_step_functions/lambdas/utils/tracing.py`
  - Already fixed: `start_child_trace` doesn't call `post()`, `end_child_trace` calls `post()`

- `infra/label_evaluator_step_functions/lambdas/unified_receipt_evaluator.py`
  - Verify all child traces use `child_trace` or `start_child_trace`/`end_child_trace`

### 2.3 Verification Criteria
- [ ] 0 orphaned traces
- [ ] Every `ReceiptEvaluation` has all expected children
- [ ] `trace_id` matches for all runs in same receipt

---

## Phase 3: Add Merchant Traces (Per-Merchant Parent)

### 3.1 Goal
Each merchant gets ONE parent trace (`PatternComputation`) with pattern learning steps nested:

```
PatternComputation (Job: Phase 1 - Pattern Learning)
├── LearnLineItemPatterns (LLM call to discover patterns)
└── BuildMerchantPatterns (Geometric computation)
```

### 3.2 Files to Modify

#### `infra/label_evaluator_step_functions/lambdas/discover_patterns.py`
```python
from tracing import create_merchant_trace, end_merchant_trace, child_trace

def handler(event, context):
    execution_arn = event["execution_arn"]
    merchant_name = event["merchant_name"]
    langchain_project = event.get("langchain_project")

    # Set project if provided
    if langchain_project:
        os.environ["LANGCHAIN_PROJECT"] = langchain_project

    # Create merchant trace (Phase 1 parent)
    merchant_trace = create_merchant_trace(
        execution_arn=execution_arn,
        merchant_name=merchant_name,
        name="PatternComputation",
        inputs={"merchant_name": merchant_name},
        metadata={"phase": "pattern_learning"},
    )

    trace_ctx = TraceContext(
        run_tree=merchant_trace.run_tree,
        headers=merchant_trace.run_tree.to_headers() if merchant_trace.run_tree else None,
        trace_id=merchant_trace.trace_id,
        root_run_id=merchant_trace.root_run_id,
    )

    try:
        with child_trace("LearnLineItemPatterns", trace_ctx, run_type="llm") as llm_ctx:
            # ... existing LLM pattern learning logic ...
            llm_ctx.set_outputs({"patterns_count": len(patterns)})

        # Return trace info for compute_patterns
        return {
            "patterns_s3_key": s3_key,
            "trace_id": merchant_trace.trace_id,
            "root_run_id": merchant_trace.root_run_id,
            "root_dotted_order": merchant_trace.root_dotted_order,
        }
    finally:
        # Don't end trace here - compute_patterns will continue it
        flush_langsmith_traces()
```

#### `infra/label_evaluator_step_functions/lambdas/compute_patterns.py`
```python
from tracing import receipt_state_trace, child_trace, end_merchant_trace, flush_langsmith_traces

def handler(event, context):
    trace_id = event.get("trace_id")  # From discover_patterns
    root_run_id = event.get("root_run_id")
    root_dotted_order = event.get("root_dotted_order")

    # Join the merchant trace started by discover_patterns
    with state_trace(
        execution_arn=event["execution_arn"],
        state_name="BuildMerchantPatterns",
        trace_id=trace_id,
        root_run_id=root_run_id,
        root_dotted_order=root_dotted_order,
    ) as trace_ctx:
        # ... existing pattern computation logic ...
        trace_ctx.set_outputs({"patterns_computed": True})

    # End the merchant trace (Phase 1 complete)
    # Note: Need to create mechanism to close trace by ID
    flush_langsmith_traces()

    return {"patterns_s3_key": s3_key, "status": "completed"}
```

### 3.3 Step Function Changes
Pass trace context through the Step Function:

```json
{
  "LearnLineItemPatterns": {
    "Type": "Task",
    "Parameters": {
      "execution_arn.$": "$$.Execution.Id",
      "merchant_name.$": "$.merchant.merchant_name",
      "langchain_project.$": "$.langchain_project"
    },
    "ResultPath": "$.line_item_patterns",
    "Next": "BuildMerchantPatterns"
  },
  "BuildMerchantPatterns": {
    "Type": "Task",
    "Parameters": {
      "execution_arn.$": "$$.Execution.Id",
      "merchant_name.$": "$.merchant.merchant_name",
      "trace_id.$": "$.line_item_patterns.trace_id",
      "root_run_id.$": "$.line_item_patterns.root_run_id",
      "root_dotted_order.$": "$.line_item_patterns.root_dotted_order"
    },
    "ResultPath": "$.patterns_result",
    "Next": "ReturnPatternResult"
  }
}
```

---

## Phase 4: Define "Jobs" for EMR Analytics

### 4.1 Job Types
| Job Type | Trace Name | Scope | Children |
|----------|------------|-------|----------|
| `PatternComputation` | Per-merchant | Phase 1 | LearnLineItemPatterns, BuildMerchantPatterns |
| `ReceiptEvaluation` | Per-receipt | Phase 2 | currency_eval, metadata_eval, etc. |

### 4.2 EMR Job Updates

#### `receipt_langsmith/receipt_langsmith/spark/processor.py`
```python
def compute_job_analytics(self, df: DataFrame) -> DataFrame:
    """Compute analytics grouped by job type."""

    # Identify job types by root trace name
    jobs = df.filter(
        (F.col("name") == "PatternComputation") |
        (F.col("name") == "ReceiptEvaluation")
    )

    # Add job_type column
    jobs = jobs.withColumn(
        "job_type",
        F.when(F.col("name") == "PatternComputation", "phase1_patterns")
         .when(F.col("name") == "ReceiptEvaluation", "phase2_evaluation")
         .otherwise("unknown")
    )

    # Aggregate by job type
    return jobs.groupBy("job_type", "metadata_merchant_name").agg(
        F.count("*").alias("job_count"),
        F.avg("duration_ms").alias("avg_duration_ms"),
        F.sum("total_tokens").alias("total_tokens"),
    )
```

---

## Phase 5: Local Development Workflow

### 5.1 Download Current Cache
```bash
# Get cache bucket name
CACHE_BUCKET=$(pulumi stack output label_evaluator_viz_cache_bucket --stack dev)

# Download existing cache
aws s3 sync s3://$CACHE_BUCKET/cache/ /tmp/viz_cache/
```

### 5.2 Trigger Bulk Export
```bash
# Get the Lambda name
TRIGGER_LAMBDA=$(pulumi stack output langsmith_trigger_lambda --stack dev)

# Trigger export for successful project
aws lambda invoke \
  --function-name "$TRIGGER_LAMBDA" \
  --payload '{"project_name": "label-evaluator-trace-test-001", "days_back": 1}' \
  /tmp/export_response.json

cat /tmp/export_response.json
```

### 5.3 Download Parquet Files
```bash
# Wait for export to complete (check LangSmith UI or poll API)
EXPORT_BUCKET="langsmith-export-dev-export-bucket-b8f3f6d"

# Download all parquet files
aws s3 sync s3://$EXPORT_BUCKET/traces/ /tmp/langsmith_traces/ --exclude "*" --include "*.parquet"
```

### 5.4 Run Spark Locally (with pandas fallback)
```python
#!/usr/bin/env python3
"""Local Spark job simulation using pandas."""
import pandas as pd
import json
from pathlib import Path

def load_all_parquet(base_path: str) -> pd.DataFrame:
    """Load all parquet files from directory tree."""
    dfs = []
    for f in Path(base_path).rglob("*.parquet"):
        dfs.append(pd.read_parquet(f))
    return pd.concat(dfs, ignore_index=True)

def compute_receipt_analytics(df: pd.DataFrame) -> pd.DataFrame:
    """Simulate LangSmithSparkProcessor.compute_receipt_analytics()"""
    # Filter to ReceiptEvaluation traces
    receipts = df[df['name'] == 'ReceiptEvaluation'].copy()

    # Parse metadata
    def get_metadata(extra):
        if pd.isna(extra):
            return {}
        try:
            return json.loads(extra).get('metadata', {})
        except:
            return {}

    receipts['merchant_name'] = receipts['extra'].apply(lambda x: get_metadata(x).get('merchant_name'))
    receipts['image_id'] = receipts['extra'].apply(lambda x: get_metadata(x).get('image_id'))
    receipts['receipt_id'] = receipts['extra'].apply(lambda x: get_metadata(x).get('receipt_id'))

    # Calculate duration
    receipts['duration_ms'] = (
        pd.to_datetime(receipts['end_time']) - pd.to_datetime(receipts['start_time'])
    ).dt.total_seconds() * 1000

    return receipts[['merchant_name', 'image_id', 'receipt_id', 'duration_ms', 'total_tokens', 'status']]

def compute_step_timing(df: pd.DataFrame) -> pd.DataFrame:
    """Simulate LangSmithSparkProcessor.compute_step_timing()"""
    step_names = [
        'ReceiptEvaluation', 'PatternComputation',
        'LearnLineItemPatterns', 'BuildMerchantPatterns',
        'currency_evaluation', 'metadata_evaluation', 'geometric_evaluation',
        'apply_phase1_corrections', 'phase2_financial_validation',
        'phase3_llm_review', 'upload_results'
    ]

    steps = df[df['name'].isin(step_names)].copy()
    steps['duration_ms'] = (
        pd.to_datetime(steps['end_time']) - pd.to_datetime(steps['start_time'])
    ).dt.total_seconds() * 1000

    return steps.groupby('name')['duration_ms'].agg([
        ('avg_duration_ms', 'mean'),
        ('p50_duration_ms', 'median'),
        ('p95_duration_ms', lambda x: x.quantile(0.95)),
        ('p99_duration_ms', lambda x: x.quantile(0.99)),
        ('total_runs', 'count')
    ]).reset_index().rename(columns={'name': 'step_name'})

if __name__ == "__main__":
    df = load_all_parquet("/tmp/langsmith_traces/")
    print(f"Loaded {len(df)} runs")

    receipt_analytics = compute_receipt_analytics(df)
    print("\n=== RECEIPT ANALYTICS ===")
    print(receipt_analytics.head(10))

    step_timing = compute_step_timing(df)
    print("\n=== STEP TIMING ===")
    print(step_timing)

    # Save as cache format
    receipt_analytics.to_parquet("/tmp/viz_cache/receipt_analytics.parquet")
    step_timing.to_parquet("/tmp/viz_cache/step_timing.parquet")
    print("\nCache written to /tmp/viz_cache/")
```

---

## Verification Checklist

### Phase 2 (Receipt Traces)
- [ ] Deploy with `pulumi up --stack dev`
- [ ] Run test execution with 3 receipts
- [ ] Query LangSmith API for traces
- [ ] Verify 0 orphaned traces
- [ ] Verify all receipts have complete children

### Phase 3 (Merchant Traces)
- [ ] Add tracing to `discover_patterns.py`
- [ ] Add tracing to `compute_patterns.py`
- [ ] Update Step Function to pass trace context
- [ ] Deploy and test
- [ ] Verify `PatternComputation` traces appear with children

### Phase 4 (EMR Updates)
- [ ] Update `processor.py` to recognize both job types
- [ ] Add `compute_job_analytics()` function
- [ ] Test locally with pandas simulation
- [ ] Deploy EMR changes

### Phase 5 (Local Workflow)
- [ ] Download current cache
- [ ] Trigger bulk export
- [ ] Download parquet files
- [ ] Run local analytics
- [ ] Compare with existing cache

---

## Timeline

| Phase | Task | Status |
|-------|------|--------|
| 1 | Verify current traces | 🔄 In Progress |
| 2 | Fix receipt traces | ✅ Code committed |
| 3 | Add merchant traces | ⏳ Pending |
| 4 | Update EMR job | ⏳ Pending |
| 5 | Local workflow | ⏳ Pending |

---

## Files Modified

```
infra/label_evaluator_step_functions/
├── infrastructure.py              # LANGCHAIN_TRACING_V2=false
├── step_function_states.py        # Pass trace context through SF
└── lambdas/
    ├── utils/tracing.py           # Fixed post/patch order
    ├── unified_receipt_evaluator.py # Already has tracing
    ├── discover_patterns.py       # TODO: Add merchant trace
    └── compute_patterns.py        # TODO: Add merchant trace

receipt_langsmith/receipt_langsmith/spark/
├── processor.py                   # TODO: Add job_type analytics
└── emr_job.py                     # TODO: Add job-type flag
```
