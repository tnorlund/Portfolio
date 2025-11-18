# Layer Update Timing Issue

## Problem

The validation fix code was updated in `legacy_helpers.py`, but the Lambda layer wasn't rebuilt until AFTER executions had already started using the old code.

## Timeline

1. **18:10:14 UTC** - Validation code deployed to Lambda functions (Docker images)
2. **18:14:34 UTC** - Delta created (using OLD layer code - no validation)
3. **18:33:25 UTC** - Execution started (using OLD layer code)
4. **18:33:53 UTC** - Layer updated with new code (too late!)

## Root Cause

The `receipt-label` Lambda layer contains the `receipt_label` package code, including `legacy_helpers.py`. When we updated `produce_embedding_delta()` to use `persist_and_upload_delta()`, we need to:

1. ✅ Commit the code change
2. ✅ Run `pulumi up` to rebuild the layer
3. ⚠️ **Wait for the layer build to complete** before running executions

## Why This Happened

The layer build is **async by default** (for faster `pulumi up`). This means:
- `pulumi up` triggers the layer build
- But doesn't wait for it to complete
- Executions can start before the new layer is ready

## Solution

### Option 1: Force Sync Mode (Recommended for Critical Fixes)

```bash
pulumi config set lambda-layer:sync-mode true
pulumi up
```

This makes `pulumi up` wait for the layer build to complete before finishing.

### Option 2: Check Layer Status Before Running Executions

```bash
# Check if layer was updated
aws lambda list-layer-versions --layer-name receipt-label-dev --region us-east-1

# Wait for latest version to be created after pulumi up
```

### Option 3: Verify Code is Deployed

Check Lambda logs for validation messages:
```bash
aws logs filter-log-events \
  --log-group-name "/aws/lambda/embedding-line-poll-lambda-dev" \
  --filter-pattern "validation" \
  --start-time $(date -u -v-1H +%s)000
```

If no validation messages appear, the new code isn't deployed yet.

## Current Status

- ✅ Code change committed
- ✅ Layer updated (version 371 at 18:33:53 UTC)
- ⚠️ But executions started before layer was ready
- ❌ Deltas created with old code are still corrupted

## Next Steps

1. **Wait for layer build to complete** before running new executions
2. **Verify new code is active** by checking logs for validation messages
3. **Run new execution** - should now use validation code
4. **Monitor** - should see validation success/failure messages in logs

