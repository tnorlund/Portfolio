# Split Receipt Pulumi Configuration

## Summary

✅ **Original Receipt is Preserved**: The script does NOT delete the original receipt from DynamoDB. It only creates new receipts, so the original remains available for rollback.

## Pulumi Exports Added

1. **`artifacts_bucket_name`** - Exported from `upload_images.artifacts_bucket.bucket`
   - Used for NDJSON artifact storage
   - Location: `infra/__main__.py` line ~390

## Configuration Loading

The script now automatically loads configuration from Pulumi:

1. **DynamoDB Table Name** - From `dynamodb_table_name` or `receipts_table_name`
2. **Artifacts Bucket** - From `artifacts_bucket_name` (newly exported)
3. **Embed NDJSON Queue URL** - Currently not exported (see below)

## Embed NDJSON Queue URL

**Status**: The `embed_ndjson_queue_url` is not currently exported in Pulumi.

**Options**:
1. **Use internal queue** - The `upload_images` component has an internal queue, but it's not exposed
2. **Export queue URL** - Need to add export if queue is created elsewhere
3. **Pass via environment variable** - Can be set manually: `export EMBED_NDJSON_QUEUE_URL=<url>`
4. **Use command-line argument** - Can still be passed via `--embed-ndjson-queue-url`

**Current Behavior**:
- Script loads from environment variable `EMBED_NDJSON_QUEUE_URL`
- Falls back to command-line argument `--embed-ndjson-queue-url`
- If neither is set, embedding is skipped (with warning)

## Original Receipt Preservation

✅ **Confirmed**: The original receipt is **NOT deleted** from DynamoDB.

**Evidence**:
- Script only calls `client.add_receipt()` for new receipts (line 856)
- No calls to `client.delete_receipt()` or `client.remove_receipt()`
- Original receipt is saved locally for rollback (line 525-527)
- Script output confirms: "Original receipt {original_receipt_id} kept for rollback" (line 953)

**Rollback Process**:
1. Original receipt remains in DynamoDB with original receipt_id
2. Original receipt data saved locally to `{output_dir}/{image_id}/original_receipt.json`
3. New receipts created with new receipt_ids (starting from 1)
4. To rollback: Delete new receipts, original receipt remains intact

## Usage

### With Pulumi Configuration (Recommended)
```bash
# Configuration loaded automatically from Pulumi
python scripts/split_receipt.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --original-receipt-id 1
```

### With Manual Queue URL
```bash
# If queue URL not in Pulumi, pass manually
python scripts/split_receipt.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --original-receipt-id 1 \
    --embed-ndjson-queue-url https://sqs.us-east-1.amazonaws.com/123456789012/embed-ndjson-queue
```

### Skip Embedding
```bash
# Skip embedding if queue URL not available
python scripts/split_receipt.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --original-receipt-id 1 \
    --skip-embedding
```

## Next Steps

1. **Export Embed NDJSON Queue URL** (if queue exists):
   ```python
   # In infra/__main__.py, add:
   pulumi.export("embed_ndjson_queue_url", upload_images.embed_ndjson_queue.url)
   ```

2. **Or Create Queue** (if it doesn't exist):
   - Create SQS queue in `upload_images` component
   - Export queue URL
   - Update script to use it

## Verification

To verify original receipt is preserved:
```bash
# Before split
aws dynamodb get-item \
    --table-name ReceiptsTable-dev \
    --key '{"PK":{"S":"IMAGE#13da1048-3888-429f-b2aa-b3e15341da5e"},"SK":{"S":"RECEIPT#00001"}}'

# After split - original receipt should still exist
aws dynamodb get-item \
    --table-name ReceiptsTable-dev \
    --key '{"PK":{"S":"IMAGE#13da1048-3888-429f-b2aa-b3e15341da5e"},"SK":{"S":"RECEIPT#00001"}}'

# New receipts should exist with different receipt_ids
aws dynamodb query \
    --table-name ReceiptsTable-dev \
    --key-condition-expression "PK = :pk AND begins_with(SK, :sk)" \
    --expression-attribute-values '{
        ":pk":{"S":"IMAGE#13da1048-3888-429f-b2aa-b3e15341da5e"},
        ":sk":{"S":"RECEIPT#"}
    }'
```


