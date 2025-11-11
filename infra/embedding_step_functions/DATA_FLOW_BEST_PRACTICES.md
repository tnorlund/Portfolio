# Data Flow Best Practices for Step Functions Workflows

## Problem Statement

Step Functions passes `null` values in JSON as `None` in Python. When using `event.get("key", default)`, if the key exists with a `null` value, Python receives `None`, not the default value. This can cause type errors when code assumes a specific type (e.g., trying to slice `None` as if it were a list).

## Root Cause

In the `merge_chunk_group_handler`, we were:
1. Getting `chunk_group` from the event (could be `None` if Step Functions passed `null`)
2. Trying to slice it: `chunk_group[:10]` - **This fails if `chunk_group` is `None`**
3. Iterating over it: `for chunk in chunk_group` - **This fails if `chunk_group` is `None`**

## Solution: Type Safety and Validation

### 1. Explicit None Handling

```python
# BAD: event.get() returns None if key exists with null value
chunk_group = event.get("chunk_group", [])

# GOOD: Explicitly handle None
chunk_group = event.get("chunk_group")
if chunk_group is None:
    chunk_group = []
```

### 2. Type Validation Before Operations

```python
# Validate type before using list operations
if not isinstance(chunk_group, list):
    return {
        "statusCode": 400,
        "error": f"chunk_group must be a list, got {type(chunk_group).__name__}",
    }

# Now safe to use list operations
chunk_group = chunk_group[:10]
for chunk in chunk_group:
    # Process chunk
```

### 3. S3 Download Validation

When downloading data from S3, validate the structure:

```python
all_groups = json.load(f)

# Validate structure
if not isinstance(all_groups, list):
    return {
        "statusCode": 500,
        "error": f"Invalid groups format in S3: expected list, got {type(all_groups).__name__}",
    }

chunk_group = all_groups[group_index]

# Validate downloaded group
if not isinstance(chunk_group, list):
    return {
        "statusCode": 500,
        "error": f"Invalid group format in S3: expected list, got {type(chunk_group).__name__}",
    }
```

## Data Flow in Ingestion Workflows

### Line/Word Ingestion Workflow

```
ListPendingBatches → PollBatches (Map) → SplitIntoChunks → ProcessChunksInParallel (Map)
    → GroupChunksForMerge → CreateChunkGroups → LoadChunkGroupsFromS3 → MergeChunkGroupsInParallel (Map)
    → PrepareHierarchicalFinalMerge → FinalMerge → MarkBatchesComplete
```

### Key Data Structures

#### 1. PollBatches Output
```json
{
  "poll_results": [
    {
      "batch_id": "...",
      "delta_key": "...",
      "intermediate_key": "..."
    }
  ]
}
```

#### 2. SplitIntoChunks Output
```json
{
  "chunked_data": {
    "batch_id": "...",
    "chunks": [...],
    "use_s3": true,
    "chunks_s3_key": "...",
    "chunks_s3_bucket": "..."
  }
}
```

#### 3. CreateChunkGroups Output
```json
{
  "chunk_groups": {
    "batch_id": "...",
    "groups_s3_key": "...",
    "groups_s3_bucket": "...",
    "total_groups": 3,
    "use_s3": true,
    "poll_results_s3_key": "...",
    "poll_results_s3_bucket": "...",
    "poll_results": null
  }
}
```

#### 4. LoadChunkGroupsFromS3 Output
```json
{
  "chunk_groups": {
    "batch_id": "...",
    "groups": [
      {
        "group_index": 0,
        "chunk_group": null,  // Will be downloaded by Lambda
        "groups_s3_key": "...",
        "groups_s3_bucket": "..."
      }
    ],
    "total_groups": 3,
    "use_s3": true
  }
}
```

#### 5. MergeChunkGroupsInParallel Input (per iteration)
```json
{
  "chunk_group": null,  // Lambda downloads from S3
  "batch_id": "...",
  "group_index": 0,
  "groups_s3_key": "...",
  "groups_s3_bucket": "..."
}
```

## Best Practices

### 1. Always Validate Types

Before using list/dict operations, validate the type:

```python
if not isinstance(data, list):
    return error_response("Expected list")
```

### 2. Handle None Explicitly

Don't rely on `event.get(key, default)` when the key might exist with `null`:

```python
# BAD
value = event.get("key", [])

# GOOD
value = event.get("key")
if value is None:
    value = []
```

### 3. Validate S3 Data

When loading data from S3, validate the structure matches expectations:

```python
data = json.load(f)
if not isinstance(data, list):
    raise ValueError(f"Expected list, got {type(data).__name__}")
```

### 4. Use Consistent Data Structures

Ensure Step Function states pass data in consistent formats. Use `ResultPath` and `OutputPath` carefully to maintain structure.

### 5. Log Type Information

When errors occur, log the actual type received:

```python
logger.error(
    "Invalid type",
    expected="list",
    actual=type(data).__name__,
    value=str(data)[:200]  # Truncate for logging
)
```

## Common Pitfalls

1. **Assuming `event.get(key, default)` always returns default**: If key exists with `null`, returns `None`
2. **Slicing `None`**: `None[:10]` raises `TypeError`
3. **Iterating over `None`**: `for item in None` raises `TypeError`
4. **Not validating S3 data**: S3 might contain unexpected formats
5. **Inconsistent data structures**: Step Functions might pass data in different formats

## Testing Recommendations

1. Test with `null` values from Step Functions
2. Test with empty lists/arrays
3. Test with invalid S3 data formats
4. Test type validation error paths
5. Test S3 download failure scenarios

