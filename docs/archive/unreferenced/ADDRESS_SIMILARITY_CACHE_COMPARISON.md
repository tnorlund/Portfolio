# Address Similarity Cache Generator vs Compaction Lambda Comparison

This document compares the address similarity cache generator Lambda with the ChromaDB compaction Lambda to ensure consistency and identify any differences.

## Overview

| Aspect | Address Similarity Cache | Compaction Lambda |
|--------|-------------------------|-------------------|
| **Purpose** | Generate cache for address similarity API | Compact ChromaDB snapshots from DynamoDB streams |
| **Trigger** | EventBridge (once per day) | EventBridge + DynamoDB Stream |
| **Architecture** | Container-based Lambda | Container-based Lambda |
| **VPC** | ❌ No VPC (S3-only, cost optimization) | ✅ VPC with EFS access |
| **Storage** | S3-only (reads snapshots, writes cache) | EFS + S3 hybrid (auto mode) |

## Infrastructure Configuration

### Memory & Timeout

| Setting | Address Similarity Cache | Compaction Lambda |
|---------|-------------------------|-------------------|
| **Memory** | 2048 MB | 2048 MB |
| **Timeout** | 300 seconds (5 min) | 300 seconds (5 min) |
| **Ephemeral Storage** | 10240 MB (10 GB) | 5120 MB (5 GB) |
| **Concurrency** | Not specified (default) | 10 reserved concurrent executions |

### Network Configuration

| Setting | Address Similarity Cache | Compaction Lambda |
|---------|-------------------------|-------------------|
| **VPC** | ❌ None | ✅ Private subnets |
| **Security Group** | ❌ None | ✅ Lambda security group |
| **EFS** | ❌ None | ✅ `/mnt/chroma` mount point |
| **NAT Gateway** | ❌ Not needed | ✅ Required for internet access |

### Storage Access

| Setting | Address Similarity Cache | Compaction Lambda |
|---------|-------------------------|-------------------|
| **ChromaDB Storage** | S3-only (reads snapshots) | EFS + S3 hybrid (`CHROMADB_STORAGE_MODE: auto`) |
| **ChromaDB Root** | `/tmp/chroma` (temporary) | `/mnt/chroma` (EFS) or `/tmp/chroma` (S3 fallback) |

## Environment Variables

### Address Similarity Cache Generator

```python
{
    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
    "CHROMADB_BUCKET": chromadb_bucket_name,
    "S3_CACHE_BUCKET": cache_bucket.id,
}
```

### Compaction Lambda

```python
{
    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
    "CHROMADB_BUCKET": chromadb_buckets.bucket_name,
    "LINES_QUEUE_URL": chromadb_queues.lines_queue_url,
    "WORDS_QUEUE_URL": chromadb_queues.words_queue_url,
    "HEARTBEAT_INTERVAL_SECONDS": "30",
    "LOCK_DURATION_MINUTES": "3",
    "MAX_HEARTBEAT_FAILURES": "3",
    "LOG_LEVEL": "INFO",
    "CHROMA_ROOT": "/mnt/chroma" if efs else "/tmp/chroma",
    "CHROMADB_STORAGE_MODE": "auto",  # EFS if available, fallback to S3
    "ENABLE_METRICS": "true",
}
```

## IAM Permissions

### Address Similarity Cache Generator

```json
{
    "DynamoDB": ["Query", "GetItem", "DescribeTable"],
    "S3 (ChromaDB)": ["GetObject", "ListBucket"],
    "S3 (Cache)": ["GetObject", "PutObject", "ListBucket"]
}
```

### Compaction Lambda

- DynamoDB: Full access (for lock management)
- S3: Full access (read/write snapshots and deltas)
- SQS: Full access (read/write queue messages)
- CloudWatch Metrics: Custom metrics write

## Dockerfile Comparison

Both use identical structure:
- ✅ Multi-stage build
- ✅ Same base image: `public.ecr.aws/lambda/python:3.12`
- ✅ Same dependency installation: `receipt_dynamo` + `receipt_label[full]`
- ✅ Same HNSWLIB_NO_NATIVE environment variable

**Difference**: Handler code location
- Cache generator: `infra/routes/address_similarity_cache_generator/handler/`
- Compaction: `infra/chromadb_compaction/lambdas/` (multiple files)

## Key Differences & Rationale

### 1. No VPC for Cache Generator

**Why?** Cost optimization. The cache generator:
- Only reads from S3 (no EFS needed)
- Only writes small JSON cache files to S3
- Doesn't need persistent storage between invocations
- Runs once per day (not time-critical)

**Trade-offs:**
- ✅ Lower cost (no NAT Gateway charges)
- ✅ Faster cold starts (no VPC ENI attachment)
- ✅ Simpler configuration
- ❌ No EFS access (uses temporary `/tmp/chroma`)

### 2. Larger Ephemeral Storage (10GB vs 5GB)

**Why?** The cache generator downloads entire ChromaDB snapshots to `/tmp/chroma` for querying. The compaction Lambda uses EFS which doesn't count against ephemeral storage limits.

### 3. No Reserved Concurrency

**Why?** Cache generator runs on a schedule (once per day) and doesn't need to handle concurrent requests. Compaction Lambda processes multiple DynamoDB stream events and needs reserved concurrency to prevent throttling.

### 4. No ECR Lifecycle Policy

**Why?** The compaction Lambda has ECR lifecycle policies to manage image retention. The cache generator could benefit from this too - **TODO: Add lifecycle policy**.

## Recommendations

### ✅ Already Consistent

1. ✅ Memory: Both use 2048 MB
2. ✅ Timeout: Both use 300 seconds
3. ✅ Dockerfile structure: Identical multi-stage builds
4. ✅ Dependencies: Both use `receipt_label[full]`
5. ✅ Architecture: Both use `CodeBuildDockerImage`

### ⚠️ Potential Improvements

1. **Add ECR Lifecycle Policy** to cache generator (similar to compaction Lambda)
2. **Add CloudWatch Metrics** to cache generator (similar to compaction Lambda's `ENABLE_METRICS`)
3. **Consider adding tags** to cache generator Lambda (compaction has comprehensive tags)
4. **Consider adding description** to cache generator Lambda

### ✅ Intentional Differences (No Changes Needed)

1. ✅ No VPC (cost optimization)
2. ✅ No EFS (S3-only for cost)
3. ✅ No reserved concurrency (scheduled, not concurrent)
4. ✅ Different environment variables (different use cases)

## Summary

The address similarity cache generator Lambda is correctly configured for its use case:
- **Cost-optimized**: No VPC/EFS to reduce costs
- **S3-only**: Reads ChromaDB snapshots from S3, writes cache to separate S3 bucket
- **Sufficient resources**: 10GB ephemeral storage for snapshot downloads, 2GB memory for ChromaDB operations
- **Consistent with patterns**: Uses same Dockerfile structure and dependency management as compaction Lambda

The main differences are intentional design choices for cost optimization and simplicity, not inconsistencies.

