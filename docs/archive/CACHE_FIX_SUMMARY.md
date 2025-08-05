# Label Count Cache Fix Summary

## Problem Identified
The label validation count API was taking ~1 second instead of being fast because the cache wasn't working. Investigation revealed:

1. **TTL Field Mismatch**: 
   - DynamoDB table configured TTL on field: `TimeToLive` (CamelCase)
   - Cache entity was writing to field: `time_to_live` (snake_case)
   - Result: DynamoDB never expired cache entries, they accumulated for 17+ days

2. **Impact**:
   - 18 stale cache entries from June 25, 2025 (17 days old)
   - All had expired TTL values but weren't cleaned up
   - API was querying live data every time (1-3 second response times)

## Changes Made

### 1. Fixed TTL Field Name
Updated `receipt_dynamo/receipt_dynamo/entities/label_count_cache.py`:
- Changed `time_to_live` to `TimeToLive` in the `to_item()` method
- Added backward compatibility in `item_to_label_count_cache()` to read both field names

### 2. Enhanced API Logging
Updated `infra/routes/label_validation_count/handler/index.py`:
- Added detailed logging for cache hits/misses
- Log cache entry age and TTL status
- Log total cache entries retrieved

### 3. Created Debug Scripts
- `scripts/debug_label_cache_simple.py` - Check cache entries and TTL status
- `scripts/cleanup_stale_cache.py` - Remove stale cache entries
- `scripts/test_cache_api.py` - Test API performance

### 4. Cleaned Up Stale Entries
- Deleted all 18 stale cache entries
- Cache updater Lambda should repopulate with correct TTL field

## Next Steps

1. **Wait for Cache Updater Lambda** - It runs every 5 minutes and should repopulate the cache
2. **Monitor CloudWatch Logs** - Check if the cache updater Lambda is running successfully
3. **Verify Fix**:
   - Run `DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 python scripts/debug_label_cache_simple.py`
   - Should see new entries with `TimeToLive` field set
   - API response time should drop to <100ms

## Deployment Notes

The fix requires:
1. Deploy the updated `receipt_dynamo` package (with TTL field fix)
2. Deploy the updated API handler (with enhanced logging)
3. The cache updater Lambda will automatically use the new code via the Lambda layer

## Testing Commands

```bash
# Check cache status
DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 python scripts/debug_label_cache_simple.py

# Test API performance
python scripts/test_cache_api.py

# Clean up stale entries (if needed)
python scripts/cleanup_stale_cache.py --force

# Manually invoke cache updater (if needed)
aws lambda invoke --function-name label_count_cache_updater_lambda-2d92d24 output.json
```

## Current Status

✅ **Cache Fixed**: The TTL field mismatch has been corrected
✅ **Cache Updater Fixed**: Now using correct method name `get_receipt_word_labels_by_label`
✅ **Cache Populated**: 18 entries with correct `TimeToLive` field  
✅ **TTL Working**: Entries expire after 6 minutes as designed
❌ **API Broken**: Getting 500 errors due to Lambda layer version mismatch

## Remaining Issue

The Lambda layers have a version mismatch issue. After deployment, the API is getting import errors:
```
Runtime.ImportModuleError: Unable to import module 'index': 
cannot import name 'DocumentEntity' from 'receipt_dynamo.entities.util'
```

This suggests the Lambda layers need to be properly rebuilt to match the current codebase.

## Manual Workaround

Until the Lambda layers are properly updated, you can manually invoke the cache updater every 5 minutes:
```bash
aws lambda invoke --function-name label_count_cache_updater_lambda-2d92d24 output.json
```