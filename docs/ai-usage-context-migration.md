# AI Usage Context Manager Migration Guide

This guide helps you migrate existing code to use the new context manager pattern introduced in Phase 2 of the AI Usage Tracking system.

## Overview

The context manager pattern ensures:
- Consistent context across all AI operations
- Automatic metric flushing
- Thread-safe operation
- Proper resource cleanup

## Migration Steps

### 1. Update Environment Variables

First, ensure your deployments have the correct environment variable:

```bash
# Production
export ENVIRONMENT=production

# Staging
export ENVIRONMENT=staging

# CI/CD
export ENVIRONMENT=cicd

# Local development (default)
export ENVIRONMENT=development
```

### 2. Import the Context Manager

```python
# Old imports
from receipt_label.utils.ai_usage_tracker import AIUsageTracker

# New imports (add this)
from receipt_label.utils.ai_usage_context import ai_usage_context, batch_ai_usage_context
```

### 3. Migrate Simple Tracking

#### Before (Direct Tracker Usage)
```python
# Old approach
tracker = AIUsageTracker()
tracker.set_tracking_context(job_id="job-123")

result = await client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Analyze this receipt"}]
)

# Manual metric storage
tracker.track_openai_completion(lambda: result)()
```

#### After (Context Manager)
```python
# New approach
with ai_usage_context('receipt_analysis', job_id='job-123') as tracker:
    result = await client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": "Analyze this receipt"}]
    )
    # Metrics automatically flushed on context exit
```

### 4. Migrate Batch Processing

#### Before
```python
# Old batch processing
tracker = AIUsageTracker()
tracker.set_tracking_context(batch_id="batch-456")

for item in items:
    result = process_item(item, tracker)
    # Manual tracking for each item
```

#### After
```python
# New batch processing with automatic batch pricing
with batch_ai_usage_context('batch-456', item_count=len(items)) as tracker:
    for item in items:
        result = process_item(item, tracker)
        # Batch pricing automatically applied
```

### 5. Migrate Complex Workflows

#### Before
```python
# Old approach with manual context management
tracker = AIUsageTracker()

def process_workflow(data):
    tracker.set_tracking_context(job_id="workflow-789")

    # Step 1
    step1_result = analyze_data(data, tracker)

    # Step 2
    step2_result = generate_summary(step1_result, tracker)

    return step2_result
```

#### After
```python
# New approach with nested contexts
def process_workflow(data):
    with ai_usage_context('data_workflow', workflow_id='workflow-789') as tracker:

        # Step 1 with sub-context
        with ai_usage_context('analyze_step', tracker=tracker) as step_tracker:
            step1_result = analyze_data(data, step_tracker)

        # Step 2 with sub-context
        with ai_usage_context('summary_step', tracker=tracker) as step_tracker:
            step2_result = generate_summary(step1_result, step_tracker)

        return step2_result
```

### 6. Migrate Error Handling

#### Before
```python
# Old approach - metrics might not be saved on error
tracker = AIUsageTracker()
try:
    result = risky_ai_operation(tracker)
except Exception as e:
    # Metrics might be lost
    raise
```

#### After
```python
# New approach - metrics always flushed
with ai_usage_context('risky_operation') as tracker:
    try:
        result = risky_ai_operation(tracker)
    except Exception as e:
        # Metrics automatically flushed even on error
        raise
```

### 7. Migrate Async Operations

#### Before
```python
# Old async tracking
async def process_async(data):
    tracker = AIUsageTracker()
    tracker.set_tracking_context(job_id="async-123")

    result = await async_ai_call(data, tracker)
    return result
```

#### After
```python
# New async with context manager
async def process_async(data):
    with ai_usage_context('async_processing', job_id='async-123') as tracker:
        result = await async_ai_call(data, tracker)
        return result
```

## Best Practices

### 1. Always Use Context Managers
```python
# ❌ Don't do this
tracker = AIUsageTracker()
# ... operations ...

# ✅ Do this
with ai_usage_context('operation_name') as tracker:
    # ... operations ...
```

### 2. Use Descriptive Operation Types
```python
# ❌ Generic names
with ai_usage_context('process') as tracker:

# ✅ Descriptive names
with ai_usage_context('receipt_text_extraction') as tracker:
```

### 3. Include Relevant Context
```python
# ✅ Rich context for better tracking
with ai_usage_context(
    'invoice_processing',
    invoice_id=invoice.id,
    customer_id=customer.id,
    processing_stage='ocr_extraction'
) as tracker:
    # ... operations ...
```

### 4. Use Batch Context for Bulk Operations
```python
# ✅ Batch context for multiple items
receipts = get_receipts_to_process()
with batch_ai_usage_context(
    f'batch-{datetime.now().isoformat()}',
    item_count=len(receipts),
    batch_source='daily_processing'
) as tracker:
    for receipt in receipts:
        process_receipt(receipt, tracker)
```

### 5. Leverage Nested Contexts
```python
# ✅ Nested contexts for complex workflows
with ai_usage_context('document_pipeline', pipeline_id='pipe-123') as pipeline_tracker:

    with ai_usage_context('ocr_stage', tracker=pipeline_tracker) as ocr_tracker:
        text = extract_text(document, ocr_tracker)

    with ai_usage_context('nlp_stage', tracker=pipeline_tracker) as nlp_tracker:
        entities = extract_entities(text, nlp_tracker)

    return entities
```

## Testing Your Migration

### 1. Unit Tests
```python
def test_migrated_function():
    with ai_usage_context('test_operation', test_id='test-123') as tracker:
        # Verify context is set
        assert tracker.current_context['operation_type'] == 'test_operation'
        assert tracker.current_context['test_id'] == 'test-123'

        # Your test logic
        result = your_function(tracker)

        # Verify metrics were tracked
        assert len(tracker.pending_metrics) > 0
```

### 2. Integration Tests
```python
def test_end_to_end_tracking():
    with patch('receipt_label.utils.ai_usage_tracker.AIUsageTracker._store_metric') as mock_store:
        with ai_usage_context('integration_test') as tracker:
            # Perform operations
            result = complex_workflow(tracker)

        # Verify flush was called
        assert mock_store.call_count > 0
```

## Common Issues and Solutions

### Issue 1: Missing Context in Nested Functions
```python
# Problem: Inner function doesn't have context
def outer():
    with ai_usage_context('outer_op') as tracker:
        inner()  # ❌ No tracker passed

# Solution: Pass tracker to inner functions
def outer():
    with ai_usage_context('outer_op') as tracker:
        inner(tracker)  # ✅ Tracker passed

def inner(tracker):
    # Use the tracker
    pass
```

### Issue 2: Forgetting to Update Imports
```python
# If you see: NameError: name 'ai_usage_context' is not defined
# Add the import:
from receipt_label.utils.ai_usage_context import ai_usage_context
```

### Issue 3: Mixing Old and New Patterns
```python
# ❌ Don't mix patterns
with ai_usage_context('operation') as tracker:
    tracker.set_tracking_context(job_id='123')  # Old method

# ✅ Use context manager parameters
with ai_usage_context('operation', job_id='123') as tracker:
    # job_id already set via context
```

## Rollback Plan

If you need to temporarily rollback:

1. The old `set_tracking_context` method still works
2. Direct tracker instantiation is still supported
3. Both patterns can coexist during migration

```python
# Both work during transition
tracker = AIUsageTracker()  # Old way still works
with ai_usage_context('op') as tracker:  # New way
```

## Need Help?

- Check the demo script: `demo_environment_config.py`
- Review the tests: `test_ai_usage_context.py`
- See the implementation spec: `spec/ai-usage-tracking/implementation.md`
