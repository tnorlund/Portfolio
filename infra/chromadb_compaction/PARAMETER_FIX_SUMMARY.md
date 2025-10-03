# ECS Compaction Worker - Parameter and Type Safety Fixes

## Problem

The infrastructure was using unsafe patterns that could silently fail:

1. ❌ Passing unused `LINES_QUEUE` and `WORDS_QUEUE` parameters
2. ❌ Using `Output.from_input("")` which fails silently
3. ❌ Making `embed_ndjson_queue_url/arn` optional when they're actually REQUIRED
4. ❌ Confusing parameter passing between call site and component

## Solution

### 1. Updated Component Signature (`ecs_compaction_worker.py`)

**BEFORE:**

```python
def __init__(
    self,
    name: str,
    *,
    vpc_id: pulumi.Input[str],  # ❌ Unused
    lines_queue_url: pulumi.Input[str],  # ❌ Not needed
    words_queue_url: pulumi.Input[str],  # ❌ Not needed
    lines_queue_arn: pulumi.Input[str],  # ❌ Not needed
    words_queue_arn: pulumi.Input[str],  # ❌ Not needed
    embed_ndjson_queue_url: Optional[pulumi.Input[str]] = None,  # ❌ Should be required!
    embed_ndjson_queue_arn: Optional[pulumi.Input[str]] = None,  # ❌ Should be required!
    ...
) -> None:
```

**AFTER:**

```python
def __init__(
    self,
    name: str,
    *,
    # Removed: vpc_id, lines_queue_url, words_queue_url, lines_queue_arn, words_queue_arn
    private_subnet_ids: pulumi.Input[list[str]],
    embed_ndjson_queue_url: pulumi.Input[str],  # ✅ Required
    embed_ndjson_queue_arn: pulumi.Input[str],  # ✅ Required
    artifacts_bucket_arn: Optional[pulumi.Input[str]] = None,  # ✅ Truly optional
    ...
) -> None:
```

### 2. Fixed IAM Policy - No More Silent Failures

**BEFORE:**

```python
policy=Output.all(
    lines_queue_arn,
    words_queue_arn,
    dynamodb_table_name,
    chromadb_bucket_arn,
    (
        artifacts_bucket_arn
        if artifacts_bucket_arn
        else Output.from_input("")  # ❌ Silently fails!
    ),
    (
        embed_ndjson_queue_arn
        if embed_ndjson_queue_arn
        else Output.from_input("")  # ❌ Silently fails!
    ),
).apply(lambda args: ...)
```

**AFTER:**

```python
# Build policy inputs explicitly
policy_inputs = [
    dynamodb_table_name,
    chromadb_bucket_arn,
    embed_ndjson_queue_arn,  # ✅ Required - no conditional
]

# Add optional parameters properly
if artifacts_bucket_arn is not None:
    policy_inputs.append(artifacts_bucket_arn)

policy=Output.all(*policy_inputs).apply(
    lambda args: json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            # Only EMBED_NDJSON_QUEUE access
            {
                "Effect": "Allow",
                "Action": ["sqs:ReceiveMessage", ...],
                "Resource": [args[2]],  # embed_ndjson_queue_arn
            },
            # Artifacts bucket - conditional on presence
            *(
                [{
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", ...],
                    "Resource": [f"{args[3]}/*"],
                }]
                if len(args) > 3 and artifacts_bucket_arn is not None
                else []
            ),
        ]
    })
)
```

### 3. Fixed Container Environment

**BEFORE:**

```python
container_def = Output.all(
    ...,
    lines_queue_url,
    words_queue_url,
    ...,
    (
        embed_ndjson_queue_url
        if embed_ndjson_queue_url
        else Output.from_input("")  # ❌ Silently fails!
    ),
).apply(lambda args: [
    {
        "environment": [
            {"name": "LINES_QUEUE_URL", "value": args[2]},  # ❌ Not needed
            {"name": "WORDS_QUEUE_URL", "value": args[3]},  # ❌ Not needed
            {"name": "EMBED_NDJSON_QUEUE_URL", "value": args[6]},
            ...
        ]
    }
])
```

**AFTER:**

```python
container_def = Output.all(
    self.repo.repository_url,
    self.image.tags[0],
    dynamodb_table_name,
    chromadb_bucket_name,
    embed_ndjson_queue_url,  # ✅ Required - no conditional
).apply(lambda args: [
    {
        "environment": [
            # Removed: LINES_QUEUE_URL, WORDS_QUEUE_URL
            {"name": "EMBED_NDJSON_QUEUE_URL", "value": args[4]},  # ✅ Always present
            {"name": "DYNAMODB_TABLE_NAME", "value": args[2]},
            {"name": "CHROMADB_BUCKET", "value": args[3]},
            ...
        ]
    }
])
```

### 4. Updated Call Site (`__main__.py`)

**BEFORE:**

```python
def _create_compaction_worker():
    return ChromaCompactionWorker(
        name=f"chroma-compaction-worker-{pulumi.get_stack()}-v2",
        vpc_id=public_vpc.vpc_id,  # ❌ Not needed
        lines_queue_url=chromadb_infrastructure.lines_queue_url,  # ❌ Not needed
        words_queue_url=chromadb_infrastructure.words_queue_url,  # ❌ Not needed
        lines_queue_arn=chromadb_infrastructure.chromadb_queues.lines_queue_arn,  # ❌ Not needed
        words_queue_arn=chromadb_infrastructure.chromadb_queues.words_queue_arn,  # ❌ Not needed
        embed_ndjson_queue_url=upload_images.embed_ndjson_queue.url,
        embed_ndjson_queue_arn=upload_images.embed_ndjson_queue.arn,
        ...
    )
```

**AFTER:**

```python
def _create_compaction_worker():
    """
    Create ECS worker that consumes EMBED_NDJSON_QUEUE to create embeddings.

    This worker does NOT consume LINES_QUEUE or WORDS_QUEUE - those are
    handled by enhanced_compaction_handler Lambda.
    """
    return ChromaCompactionWorker(
        name=f"chroma-compaction-worker-{pulumi.get_stack()}-v2",
        # Removed: vpc_id, lines_queue_url, words_queue_url, lines_queue_arn, words_queue_arn
        private_subnet_ids=nat.private_subnet_ids.apply(lambda ids: [ids[0]]),
        # Worker ONLY needs EMBED_NDJSON_QUEUE (required parameters)
        embed_ndjson_queue_url=upload_images.embed_ndjson_queue.url,
        embed_ndjson_queue_arn=upload_images.embed_ndjson_queue.arn,
        artifacts_bucket_arn=upload_images.artifacts_bucket.arn,
        ...
        desired_count=0,  # TODO: Set to 1 to enable worker
    )
```

## Benefits

### ✅ Type Safety

- `embed_ndjson_queue_url/arn` are now **required** parameters
- No more `Optional[...]` for things that must exist
- Pulumi will fail early if queue isn't provided

### ✅ No Silent Failures

- Removed all `Output.from_input("")` patterns
- Empty strings would have caused runtime failures
- Now fails at deploy time if parameters are missing

### ✅ Clear Intent

- Only passes parameters that are actually used
- No confusion about which queues the worker consumes
- Docstrings explain the architecture

### ✅ Simpler Code

- Fewer parameters to track
- Simpler IAM policy construction
- Clearer environment variable mapping

## Testing

After deploying:

```bash
# 1. Check that Pulumi validates the parameters
pulumi preview

# 2. Deploy
pulumi up

# 3. Verify the task definition
aws ecs describe-task-definition \
  --task-definition chroma-compaction-worker-dev-v2-task \
  --query 'taskDefinition.containerDefinitions[0].environment' \
  --output json

# Should show ONLY:
# - EMBED_NDJSON_QUEUE_URL (present)
# - DYNAMODB_TABLE_NAME (present)
# - CHROMADB_BUCKET (present)
# - CHROMA_ROOT (present)
# - LOG_LEVEL (present)

# Should NOT show:
# - LINES_QUEUE_URL
# - WORDS_QUEUE_URL
```

## Migration Notes

If you have an existing deployment:

1. The `pulumi up` will show parameter changes
2. It will recreate the IAM policy (only grants EMBED_NDJSON_QUEUE access now)
3. It will update the task definition (removes LINES/WORDS queue env vars)
4. The worker will restart with the new configuration

No data loss - this only changes configuration, not data.

## Related Files

- `/infra/chromadb_compaction/components/ecs_compaction_worker.py` - Component definition
- `/infra/__main__.py` - Call site
- `/infra/chromadb_compaction/worker/worker.py` - Worker code (only polls EMBED_NDJSON_QUEUE)
- `QUEUE_CONFIGURATION.md` - Architecture documentation
- `CHANGES_SUMMARY.md` - Complete deployment guide
