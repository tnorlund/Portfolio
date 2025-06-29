# AI Usage Context Manager Patterns

This document describes the context manager patterns implemented in Phase 3 of the AI Usage Tracking system.

## Overview

The context manager patterns provide automatic tracking of AI service usage with minimal code changes. They ensure consistent metadata propagation, automatic metric flushing, and proper error handling.

## Key Features

- **Decorator-based tracking** for functions and methods
- **Context propagation** across function calls
- **Nested context support** with parent references
- **Thread-safe** concurrent operations
- **Error recovery** and partial failure handling
- **Performance** < 5ms overhead per operation

## Usage Patterns

### 1. Basic Context Manager

```python
from receipt_label.utils import ai_usage_context

with ai_usage_context("receipt_processing", job_id="job-123") as tracker:
    # All AI operations within this context are tracked
    result = process_receipt_with_ai(receipt_data)
    # Metrics are automatically flushed when exiting
```

### 2. Decorator Pattern

The decorator automatically wraps functions with tracking:

```python
from receipt_label.utils import ai_usage_tracked

@ai_usage_tracked(operation_type="receipt_analysis")
def analyze_receipt(receipt_id: str, image_url: str):
    # Function is automatically tracked
    result = openai_client.chat.completions.create(...)
    return result

# Can also be used without parentheses
@ai_usage_tracked
def simple_function():
    pass
```

### 3. Runtime Context Extraction

The decorator extracts context from function arguments:

```python
@ai_usage_tracked
def process_batch(items: list, job_id: str = None, batch_id: str = None):
    # job_id and batch_id are automatically extracted and added to context
    for item in items:
        process_item(item)
```

### 4. Async Support

Both context managers and decorators support async functions:

```python
@ai_usage_tracked(operation_type="async_processing")
async def process_async(data: str):
    result = await async_ai_operation(data)
    return result

# Context manager with async
async with ai_usage_context("async_op") as tracker:
    await process_data()
```

### 5. Nested Contexts

Contexts can be nested, with automatic parent tracking:

```python
with ai_usage_context("parent_operation", job_id="job-1") as tracker:
    # Parent context
    process_step_1()

    with ai_usage_context("child_operation") as child_tracker:
        # Child inherits job_id and tracks parent
        process_step_2()
```

### 6. Batch Operations

Special context for batch operations with automatic batch pricing:

```python
from receipt_label.utils import batch_ai_usage_context

with batch_ai_usage_context("batch-123", item_count=100) as tracker:
    # All operations get batch pricing (50% discount)
    for item in items:
        process_with_ai(item)
```

### 7. Error Recovery

Context managers ensure metrics are flushed even on errors:

```python
with ai_usage_context("risky_operation") as tracker:
    try:
        risky_ai_operation()
    except Exception as e:
        # Metrics are still flushed with error info
        raise
```

### 8. Partial Failure Handling

Track operations that can partially fail:

```python
from receipt_label.utils import partial_failure_context

with partial_failure_context("batch_processing", continue_on_error=True) as ctx:
    for item in items:
        try:
            process_item(item)
            ctx['success_count'] += 1
        except Exception as e:
            ctx['errors'].append({'item': item, 'error': str(e)})
            ctx['failure_count'] += 1
            # Continue processing other items
```

## Integration with AI Clients

The context managers automatically integrate with wrapped AI clients:

```python
from receipt_label.utils import ClientManager, ai_usage_context

manager = ClientManager.from_env()

with ai_usage_context("document_analysis", job_id="job-456"):
    # Context is automatically passed to OpenAI calls
    response = manager.openai.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": "Analyze this document"}]
    )
```

## Performance Characteristics

Based on our benchmarks:

- **Context manager overhead**: ~0.02ms
- **Decorator overhead**: ~0.05ms
- **Nested contexts (3 levels)**: ~0.09ms
- **Thread-local operations**: ~0.0001ms
- **Large contexts (1000 keys)**: ~0.15ms

All operations are well under the 5ms requirement.

## Best Practices

1. **Use decorators for simple functions** that don't need custom tracking
2. **Use context managers for complex operations** with multiple AI calls
3. **Leverage runtime extraction** for job_id and batch_id
4. **Handle partial failures** in batch operations
5. **Keep context data reasonable** - avoid huge contexts

## Thread Safety

All context operations are thread-safe using thread-local storage:

```python
import threading

def worker(worker_id):
    with ai_usage_context(f"worker_{worker_id}"):
        # Each thread has isolated context
        process_data()

threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
for t in threads:
    t.start()
```

## Error Handling

Errors are tracked automatically:

```python
@ai_usage_tracked
def may_fail():
    if random.random() > 0.5:
        raise ValueError("Random failure")
    return "success"

# Error info is captured in metrics:
# - error_occurred: True
# - error_message: "Random failure"
# - operation_duration_ms: <time until error>
```

## Migration Guide

To migrate existing code:

1. **Identify AI operations** - Find all OpenAI/Anthropic API calls
2. **Add decorators** to functions making AI calls
3. **Wrap batch operations** with batch_ai_usage_context
4. **Handle errors** with partial_failure_context where appropriate
5. **Test thoroughly** - Ensure metrics are captured correctly

## Examples

See `examples/context_manager_demo.py` for comprehensive examples of all patterns.
