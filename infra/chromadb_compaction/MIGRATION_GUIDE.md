# ChromaDB Compaction Migration Guide

## Overview

This guide provides step-by-step instructions for migrating from the current direct ChromaDB writes to the new S3 compaction architecture.

## Current State

The existing system (based on your chroma_db branch) has:
- Direct ChromaDB writes from Lambda functions
- ChromaDB client utilities in `receipt_label/utils/`
- No conflict resolution for concurrent writes
- Single-writer limitation

## Target State

The new compaction architecture provides:
- Multiple concurrent producers via delta files
- Single compactor with distributed locking
- S3-based persistent storage
- Horizontal scalability

## Migration Phases

### Phase 1: Infrastructure Deployment (Week 1)

1. **Deploy new infrastructure alongside existing**
   ```bash
   cd infra/chromadb_compaction
   pulumi stack select dev
   pulumi up
   ```

2. **Verify all components are created**
   - S3 bucket: `chromadb-vectors-dev-{account}`
   - SQS queue: `chromadb-delta-queue`
   - Lambda functions: producer, compactor, query
   - EventBridge rule for scheduled compaction

3. **Test infrastructure with synthetic data**
   ```bash
   # Invoke producer with test data
   aws lambda invoke \
     --function-name chromadb-producer-dev \
     --payload '{"words": [...], "receipt_id": "test-001"}' \
     response.json
   
   # Check delta was created
   aws s3 ls s3://chromadb-vectors-dev-{account}/delta/
   
   # Wait for compaction or trigger manually
   aws lambda invoke \
     --function-name chromadb-compactor-dev \
     --payload '{"source": "manual"}' \
     response.json
   ```

### Phase 2: Code Updates (Week 2)

1. **Update polling handler to use delta pattern**
   
   The polling handler in `poll_line_embedding_batch_handler_chromadb.py` has already been updated to create deltas. Verify it's using the correct S3 bucket:
   
   ```python
   DELTA_BUCKET = os.environ.get("DELTA_BUCKET", "chromadb-deltas-bucket")
   ```

2. **Update step function to pass environment variables**
   ```python
   # In step function definition
   environment={
       "DELTA_BUCKET": chromadb_bucket.id,
       "DELTA_QUEUE_URL": delta_queue.url,
   }
   ```

3. **Create wrapper for existing ChromaDB writes**
   ```python
   # receipt_label/utils/chroma_delta_producer.py
   from receipt_label.utils.chroma_s3_helpers import produce_embedding_delta
   
   def write_to_chromadb_delta(ids, embeddings, documents, metadatas):
       """Wrapper to redirect ChromaDB writes to delta pattern."""
       return produce_embedding_delta(
           ids=ids,
           embeddings=embeddings,
           documents=documents,
           metadatas=metadatas,
           bucket=os.environ["DELTA_BUCKET"],
           queue_url=os.environ["DELTA_QUEUE_URL"]
       )
   ```

### Phase 3: Parallel Running (Week 3)

1. **Enable dual writes (both systems)**
   ```python
   # Temporary dual-write pattern
   try:
       # Write to new delta system
       delta_result = write_to_chromadb_delta(...)
       
       # Also write to existing ChromaDB (fallback)
       chroma_client.upsert_vectors(...)
   except Exception as e:
       logger.error(f"Delta write failed: {e}")
       # Continue with direct ChromaDB write
   ```

2. **Monitor both systems**
   - Check CloudWatch metrics for delta production
   - Verify compaction is running successfully
   - Compare query results between systems

3. **Gradual traffic shift**
   ```python
   # Use feature flag or percentage rollout
   if random.random() < DELTA_WRITE_PERCENTAGE:
       write_to_chromadb_delta(...)
   else:
       chroma_client.upsert_vectors(...)
   ```

### Phase 4: Query Migration (Week 4)

1. **Update query functions to use snapshot**
   ```python
   # Before: Direct ChromaDB query
   results = chroma_client.query(...)
   
   # After: Query from S3 snapshot
   results = query_chromadb_snapshot(
       bucket=CHROMADB_BUCKET,
       query_text=query_text,
       n_results=10
   )
   ```

2. **Add caching layer for performance**
   ```python
   # Cache snapshot location for warm starts
   SNAPSHOT_CACHE = {}
   
   def get_latest_snapshot():
       if "latest" in SNAPSHOT_CACHE:
           cached_time, snapshot_path = SNAPSHOT_CACHE["latest"]
           if time.time() - cached_time < 300:  # 5 min cache
               return snapshot_path
       
       # Fetch from S3
       snapshot_path = fetch_latest_snapshot_pointer()
       SNAPSHOT_CACHE["latest"] = (time.time(), snapshot_path)
       return snapshot_path
   ```

### Phase 5: Cutover (Week 5)

1. **Stop direct ChromaDB writes**
   - Remove dual-write code
   - Update all producers to use delta pattern only

2. **Final data migration**
   ```bash
   # Export existing ChromaDB data
   python scripts/export_chromadb_to_s3.py \
     --source-path /path/to/chromadb \
     --bucket chromadb-vectors-prod-{account} \
     --prefix snapshot/migration/
   
   # Run special compaction to merge migration data
   aws lambda invoke \
     --function-name chromadb-compactor-prod \
     --payload '{"source": "migration", "snapshot_path": "snapshot/migration/"}' \
     response.json
   ```

3. **Validate data integrity**
   ```python
   # Compare counts
   old_count = old_chroma_client.count()
   new_count = query_snapshot_count()
   assert abs(old_count - new_count) < 100  # Allow small difference
   
   # Sample queries
   for test_query in TEST_QUERIES:
       old_results = old_chroma_client.query(test_query)
       new_results = query_chromadb_snapshot(test_query)
       assert similarity(old_results, new_results) > 0.95
   ```

### Phase 6: Cleanup (Week 6)

1. **Remove old ChromaDB infrastructure**
   - Delete direct ChromaDB Lambda functions
   - Remove ChromaDB client code that bypasses delta pattern
   - Clean up old EFS mounts or persistent volumes

2. **Update documentation**
   - Remove references to direct ChromaDB writes
   - Update runbooks for new architecture
   - Document new operational procedures

3. **Cost optimization**
   - Review S3 lifecycle policies
   - Optimize compaction frequency based on usage
   - Enable S3 Intelligent-Tiering

## Rollback Plan

If issues arise during migration:

1. **Immediate rollback (Phase 3-4)**
   ```python
   # Disable delta writes
   DELTA_WRITE_PERCENTAGE = 0.0
   
   # Continue using direct ChromaDB
   ```

2. **Data recovery (Phase 5)**
   ```bash
   # Restore from S3 versioning
   aws s3api list-object-versions \
     --bucket chromadb-vectors-prod-{account} \
     --prefix snapshot/
   
   # Restore specific version
   aws s3api get-object \
     --bucket chromadb-vectors-prod-{account} \
     --key snapshot/latest/pointer.txt \
     --version-id {previous-version-id} \
     pointer-restored.txt
   ```

3. **Emergency procedures**
   - Force release DynamoDB lock if stuck
   - Clear SQS queue if overwhelmed
   - Revert Lambda functions to previous versions

## Monitoring During Migration

### Key Metrics to Watch

1. **Delta Production Rate**
   ```
   CloudWatch Metrics:
   - Namespace: ChromaDB/Producer
   - MetricName: DeltasCreated
   - Expected: Should match current write rate
   ```

2. **Compaction Success Rate**
   ```
   CloudWatch Metrics:
   - Namespace: ChromaDB/Compaction
   - MetricName: CompactionSuccess
   - Expected: >99% success rate
   ```

3. **Query Latency Comparison**
   ```
   # Add custom metrics in code
   start = time.time()
   results = query_chromadb_snapshot(...)
   latency = time.time() - start
   cloudwatch.put_metric("QueryLatency", latency)
   ```

### Alerts to Configure

```python
# High delta accumulation
alarm(
    name="ChromaDB-Migration-DeltaBacklog",
    metric="ApproximateNumberOfMessagesVisible",
    threshold=5000,  # Higher threshold during migration
    evaluation_periods=2
)

# Query failures
alarm(
    name="ChromaDB-Migration-QueryErrors",
    metric="Errors",
    threshold=10,
    evaluation_periods=1
)
```

## Success Criteria

Migration is complete when:

1. ✅ All producers use delta pattern
2. ✅ No direct ChromaDB writes in code
3. ✅ Query performance matches or exceeds previous system
4. ✅ Zero data loss verified through reconciliation
5. ✅ Cost reduction achieved (no more EFS/persistent volumes)
6. ✅ Horizontal scaling tested (multiple producers)

## Support Resources

- **Runbook**: See `RUNBOOK.md` for operational procedures
- **Architecture**: See `TECHNICAL_DESIGN.md` for system details
- **Troubleshooting**: See `TROUBLESHOOTING.md` for common issues

## Timeline Summary

- **Week 1**: Deploy infrastructure
- **Week 2**: Update code for delta pattern
- **Week 3**: Parallel running with monitoring
- **Week 4**: Migrate queries to snapshots
- **Week 5**: Complete cutover
- **Week 6**: Cleanup and optimization

Total migration time: 6 weeks with gradual rollout and validation at each phase.