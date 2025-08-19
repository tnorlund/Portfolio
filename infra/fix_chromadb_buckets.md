# Fix for ChromaDB Bucket Duplication

## Problem
Currently, two separate ChromaDB bucket instances are being created:
1. In `__main__.py`: `chromadb_storage = ChromaDBBuckets("chromadb-test")`
2. In `EmbeddingInfrastructure`: `self.chromadb_buckets = ChromaDBBuckets(...)`

This causes:
- Data fragmentation across buckets
- Confusion about source of truth
- Unnecessary costs
- Potential data inconsistency

## Solution

### Option 1: Pass Shared Resources (Recommended)
Modify `EmbeddingInfrastructure` to accept external ChromaDB resources:

```python
# infra/__main__.py
# Create shared ChromaDB resources ONCE
chromadb_storage = ChromaDBBuckets("chromadb")
chromadb_queues = ChromaDBQueues("chromadb")

# Pass to EmbeddingInfrastructure
embedding_infrastructure = EmbeddingInfrastructure(
    "embedding-infra",
    base_images=base_images,
    chromadb_buckets=chromadb_storage,  # Pass existing instance
    chromadb_queues=chromadb_queues,    # Pass existing instance
)
```

```python
# infra/embedding_step_functions/infrastructure.py
class EmbeddingInfrastructure(ComponentResource):
    def __init__(
        self,
        name: str,
        base_images=None,
        chromadb_buckets: Optional[ChromaDBBuckets] = None,
        chromadb_queues: Optional[ChromaDBQueues] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(...)
        
        # Use provided resources or create new ones
        if chromadb_buckets:
            self.chromadb_buckets = chromadb_buckets
        else:
            self.chromadb_buckets = ChromaDBBuckets(
                f"{name}-chromadb-buckets",
                opts=ResourceOptions(parent=self),
            )
        
        if chromadb_queues:
            self.chromadb_queues = chromadb_queues
        else:
            self.chromadb_queues = ChromaDBQueues(
                f"{name}-chromadb-queues",
                opts=ResourceOptions(parent=self),
            )
```

### Option 2: Remove from EmbeddingInfrastructure
Remove ChromaDB resource creation from `EmbeddingInfrastructure` entirely and always pass them in:

```python
# infra/embedding_step_functions/infrastructure.py
class EmbeddingInfrastructure(ComponentResource):
    def __init__(
        self,
        name: str,
        chromadb_buckets: ChromaDBBuckets,  # Required
        chromadb_queues: ChromaDBQueues,    # Required
        base_images=None,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(...)
        
        # Use provided resources only
        self.chromadb_buckets = chromadb_buckets
        self.chromadb_queues = chromadb_queues
```

## Implementation Steps

1. **Update `EmbeddingInfrastructure.__init__`** to accept ChromaDB resources
2. **Update `__main__.py`** to pass the shared resources
3. **Remove duplicate `ChromaDBBuckets` creation** from `__main__.py` 
4. **Update Lambda environment variables** to use the single bucket
5. **Test with `pulumi preview`** to verify only one bucket
6. **Migrate existing data** if needed (from old bucket to new)

## Environment Variable Consistency

Ensure all Lambda functions use the same bucket:
```python
environment_vars = {
    "CHROMADB_BUCKET": chromadb_storage.bucket_name,
    "COMPACTION_QUEUE_URL": chromadb_queues.delta_queue_url,
    # ... other vars
}
```

## Benefits

- **Single source of truth**: All embeddings in one bucket
- **Cost savings**: One bucket instead of two
- **Simpler operations**: Clear where data lives
- **Easier debugging**: No confusion about which bucket to check
- **Consistent configuration**: All services use same resources

## Migration Considerations

If data already exists in both buckets:
1. Identify which bucket has the authoritative data
2. Copy data from old bucket to new (if needed)
3. Update all Lambda functions to use new bucket
4. Delete old bucket after verification

## Testing

After changes:
```bash
# Preview changes
pulumi preview

# Verify only one ChromaDB bucket in outputs
pulumi stack output | grep chromadb_bucket

# Should see only one bucket, not two
```