# EFS Lambda Setup - Complete ✅

## What We Implemented

Successfully configured `embed_from_ndjson_lambda` to use `worker.py` with EFS mount for ChromaDB access.

## Architecture

```
process_ocr_results.py
         ↓ (enqueues)
EMBED_NDJSON_QUEUE
         ↓ (EventSourceMapping - auto-scales)
embed_from_ndjson_lambda ✅
  - Uses worker.py code
  - Has EFS mounted at /mnt/chroma
  - Reads existing ChromaDB for merchant resolution
  - Creates embeddings
  - Uploads delta files to S3
  - Writes CompactionRun to DynamoDB
         ↓
DynamoDB Stream
         ↓
stream_processor.py
         ↓
LINES_QUEUE & WORDS_QUEUE
         ↓
enhanced_compaction_handler
  - Merges deltas into main ChromaDB on EFS
```

## Changes Made

### 1. Updated `infra/upload_images/infra.py`

#### Added Parameters (lines 69-72):

```python
worker_image_uri: pulumi.Input[str] | None = None,
chromadb_efs_access_point_arn: pulumi.Input[str] | None = None,
chromadb_efs_mount_target: pulumi.Input[Any] | None = None,
```

#### Updated Lambda Configuration (lines 572-625):

```python
embed_from_ndjson_lambda = Function(
    ...
    # Use chromadb worker image (has worker.py with lambda_handler)
    image_uri=worker_image_uri,

    # Increased resources for EFS + ChromaDB
    memory_size=2048,
    ephemeral_storage={"size": 5120},

    # Environment variables
    environment={
        "CHROMA_ROOT": "/mnt/chroma",  # EFS mount point
        "CHROMADB_BUCKET": ...,
        "DYNAMO_TABLE_NAME": ...,
        "OPENAI_API_KEY": ...,
        "GOOGLE_PLACES_API_KEY": ...,
    },

    # VPC configuration (required for EFS)
    vpc_config=FunctionVpcConfigArgs(...),

    # EFS mount configuration ✅
    file_system_config=FunctionFileSystemConfigArgs(
        arn=chromadb_efs_access_point_arn,
        local_mount_path="/mnt/chroma",
    ),

    # Ensure EFS mount target is ready
    opts=ResourceOptions(
        depends_on=[chromadb_efs_mount_target]
    ),
)
```

### 2. Updated `infra/__main__.py`

#### Built Worker Image First (lines 364-400):

```python
# Build worker Docker image
worker_repo = aws_ecr.Repository(...)
worker_image = docker_build.Image(
    context={"location": repo_root_dir},
    dockerfile={"location": "infra/chromadb_compaction/worker/Dockerfile"},
    ...
)
worker_lambda_image_uri = ...
```

#### Passed to UploadImages (lines 402-418):

```python
upload_images = UploadImages(
    ...
    # Pass worker image and EFS for embed_from_ndjson_lambda
    worker_image_uri=worker_lambda_image_uri,
    chromadb_efs_access_point_arn=chromadb_infrastructure.efs.access_point_arn,
    chromadb_efs_mount_target=chromadb_infrastructure.efs.primary_mount_target,
)
```

### 3. Worker Code Already Perfect ✅

The `worker.py` already handles EFS properly:

#### Snapshot Restoration (lines 176-188):

```python
lines_dir = os.path.join(CHROMA_ROOT, "lines")  # /mnt/chroma/lines
words_dir = os.path.join(CHROMA_ROOT, "words")  # /mnt/chroma/words

# Restore from S3 if EFS is empty
if not _dir_has_content(lines_dir):
    _restore_snapshot_from_s3("lines", lines_dir)
if not _dir_has_content(words_dir):
    _restore_snapshot_from_s3("words", words_dir)
```

#### Chroma Client Setup (lines 191-197):

```python
# Read directly from EFS-backed Chroma
chroma_line_client = VectorClient.create_chromadb_client(
    persist_directory=lines_dir,  # /mnt/chroma/lines
    mode="read"
)
```

#### Lambda Handler (lines 424-454):

```python
def lambda_handler(event=None, context=None):
    """SQS batch entrypoint for container-based Lambda."""
    for rec in event["Records"]:
        body = json.loads(rec.get("body", "{}"))
        _process_payload_dict(body, queue_name="sqs")
    return {"batchItemFailures": failed}
```

## Features

### ✅ Auto-Scaling

- EventSourceMapping automatically scales Lambda based on queue depth
- No manual scaling configuration needed

### ✅ EFS Access

- Lambda mounts EFS at `/mnt/chroma`
- Can read existing ChromaDB collections
- Used for merchant resolution during embedding

### ✅ Snapshot Bootstrap

- Automatically restores from S3 if EFS is empty
- Ensures ChromaDB data is always available

### ✅ Delta Generation

- Creates embeddings for lines and words
- Uploads delta files to S3
- Writes CompactionRun to DynamoDB

### ✅ Error Handling

- Uses `ReportBatchItemFailures` for partial batch failures
- Failed messages automatically retry
- Dead Letter Queue available

## Deployment

```bash
cd infra
pulumi up
```

### What Will Happen:

1. **Worker Image Built:**

   - Docker image from `chromadb_compaction/worker/`
   - Pushed to ECR
   - Contains `worker.py` with `lambda_handler()`

2. **Lambda Updated:**

   - `embed_from_ndjson_lambda` will use new worker image
   - EFS mount added at `/mnt/chroma`
   - Memory increased to 2048MB
   - Ephemeral storage increased to 5GB

3. **EventSourceMapping:**
   - Remains active (already enabled)
   - Connects EMBED_NDJSON_QUEUE to Lambda

## Verification

### 1. Check Lambda Configuration

```bash
aws lambda get-function-configuration \
  --function-name upload-images-dev-embed-from-ndjson \
  --query '{Memory:MemorySize,Timeout:Timeout,EFS:FileSystemConfigs,VPC:VpcConfig}'
```

Should show:

- Memory: 2048
- Timeout: 900
- EFS: `arn:...access-point/...` at `/mnt/chroma`
- VPC: Configured with subnets and security groups

### 2. Check EFS Mount

```bash
# Check EFS file system
aws efs describe-file-systems \
  --query 'FileSystems[?Name==`chromadb-dev-efs`]'

# Check access point
aws efs describe-access-points \
  --query 'AccessPoints[?Name==`chromadb-dev-access-point`]'
```

### 3. Test Processing

```bash
# Watch Lambda logs
aws logs tail /aws/lambda/upload-images-dev-embed-from-ndjson --follow

# Expected output:
# "Using EFS-backed Chroma for read-only access"
# "Loaded NDJSON rows: lines=X words=Y"
# "Upserted embeddings into delta mode"
# "Uploaded lines delta bundle to s3://..."
# "Wrote CompactionRun..."
```

### 4. Check EFS Contents

```bash
# From a Lambda or EC2 instance with EFS mounted:
ls -la /mnt/chroma/
# Should show:
# lines/
# words/
```

## Benefits

### ✅ Merchant Resolution

- Can query existing ChromaDB for similar merchants
- Improves embedding quality with context
- Uses EFS for fast local access

### ✅ Auto-Scaling

- Lambda scales based on queue size
- No manual configuration
- Cost-effective (pay per invocation)

### ✅ Code Reuse

- Same `worker.py` code
- Could be used by ECS if needed
- Single codebase for embedding logic

### ✅ Performance

- EFS provides low-latency access to ChromaDB
- Local delta creation (5GB ephemeral storage)
- Fast S3 upload of bundled deltas

## Monitoring

### CloudWatch Metrics

**Lambda:**

- `Invocations` - number of times triggered
- `Duration` - processing time
- `Errors` - any failures
- `ConcurrentExecutions` - scaling behavior

**SQS:**

- `ApproximateNumberOfMessagesVisible` - queue backlog
- `NumberOfMessagesSent` - incoming rate
- `NumberOfMessagesDeleted` - processing rate

**EFS:**

- `ClientConnections` - Lambda connections
- `DataReadIOBytes` - read from ChromaDB
- `DataWriteIOBytes` - (minimal, mostly reads)

### Alarms to Set

```python
# High queue backlog
if ApproximateNumberOfMessagesVisible > 100:
    alert("Queue backing up")

# Lambda errors
if Errors > 5 in 5 minutes:
    alert("Lambda failing")

# Long duration
if Duration > 800s (near timeout):
    alert("Lambda timing out")
```

## Troubleshooting

### Lambda Can't Access EFS

- Check VPC configuration
- Verify security groups allow NFS (port 2049)
- Ensure Lambda and EFS in same VPC
- Check EFS mount target status

### ChromaDB Empty on First Run

- Normal! Worker will restore from S3
- Check logs for "Restoring snapshot for..."
- Verify S3 bucket has snapshots at `snapshots/lines/` and `snapshots/words/`

### Out of Memory

- Check CloudWatch metrics
- May need to increase memory beyond 2048MB
- Check ephemeral storage usage

### Timeout

- Current: 900s (15 minutes)
- If hitting limit, consider:
  - Reducing batch_size in EventSourceMapping
  - Optimizing embedding creation
  - Switching to ECS worker for very large jobs

## Summary

✅ **Lambda uses worker.py** - Same code as ECS worker  
✅ **EFS mounted** - ChromaDB access at `/mnt/chroma`  
✅ **Auto-scaling** - Based on queue depth  
✅ **Snapshot restoration** - Automatic if EFS empty  
✅ **Merchant resolution** - Queries existing ChromaDB  
✅ **Delta generation** - Creates and uploads to S3  
✅ **CompactionRun tracking** - Writes to DynamoDB

**Ready to deploy!** Run `pulumi up` to activate.
