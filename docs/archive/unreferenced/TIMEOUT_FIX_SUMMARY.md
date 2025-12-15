# Lambda Timeout Fix Summary

## Problem

The step function execution `a7bfaf55-8500-4e63-a4d8-bdea190d0c4a` failed with timeout errors after only 1.2 seconds, even though:
- The Lambda timeout is configured to 900 seconds (15 minutes)
- The handler has a timeout protection decorator set to 840 seconds (14 minutes)
- The successful execution `885ea78b-e884-4e63-94ae-7e29736cc4fe` completed successfully using the same ECR image

## Root Cause

The timeout protection system had a critical bug:

1. **Module Import Time vs Lambda Execution Time Mismatch**:
   - The `TimeoutProtection` class initializes `start_time = time.time()` when the module is imported (at container initialization)
   - When the Lambda function actually executes, `start_lambda_monitoring(context)` is called
   - However, `start_time` was never reset, so it still referenced the module import time
   - If the module was imported minutes or hours before Lambda execution (common with container reuse), `get_remaining_time()` would calculate elapsed time from import time, not execution time

2. **Immediate Timeout Detection**:
   - `should_abort()` checks if remaining time is <= 5% of timeout (45 seconds for 900s timeout)
   - If the module was imported 20+ minutes ago, `get_remaining_time()` would return a negative or very small value
   - This caused `should_abort()` to return `True` immediately, triggering timeout errors

3. **Why It Was Intermittent**:
   - Cold starts: New containers = fresh import time = works correctly
   - Warm starts: Reused containers = old import time = immediate timeout
   - This explains why the successful execution worked (likely a cold start) but the failed one didn't (warm start with old import time)

## Fix

### Changes Made

1. **Reset `start_time` in `set_lambda_context()`**:
   ```python
   def set_lambda_context(self, context):
       # ... existing code ...
       # CRITICAL: Reset start_time when context is set to ensure accurate timing
       # This prevents timeouts caused by module import time vs Lambda execution time mismatch
       self.start_time = time.time()
   ```

2. **Reset `start_time` in `start_lambda_monitoring()`**:
   ```python
   def start_lambda_monitoring(context=None):
       # CRITICAL: Reset start_time before setting context to ensure accurate timing
       # This prevents timeouts caused by module import time vs Lambda execution time mismatch
       timeout_protection.start_time = time.time()

       if context:
           timeout_protection.set_lambda_context(context)
   ```

### Why Both Fixes?

- `set_lambda_context()`: Ensures `start_time` is reset whenever context is set
- `start_lambda_monitoring()`: Provides an additional safety net, resetting `start_time` even if context is not provided (defensive programming)

## Testing

To verify the fix:

1. **Deploy the updated code**:
   ```bash
   pulumi up
   ```

2. **Run a step function execution**:
   ```bash
   aws stepfunctions start-execution \
     --state-machine-arn "arn:aws:states:us-east-1:681647709217:stateMachine:line-ingest-sf-dev-1554303" \
     --name "test-timeout-fix-$(date +%s)"
   ```

3. **Monitor execution**:
   - Check that executions don't fail immediately with timeout errors
   - Verify that timeout protection still works correctly (warns at 90%, aborts at 95%)
   - Confirm that both cold starts and warm starts work correctly

## Expected Behavior After Fix

1. **Cold Starts**:
   - Module imported at container initialization
   - `start_time` reset when Lambda executes
   - Timeout protection works correctly

2. **Warm Starts**:
   - Module imported during previous execution
   - `start_time` reset when Lambda executes
   - Timeout protection works correctly

3. **Timeout Protection**:
   - Warns at 90% of timeout (810 seconds for 900s timeout)
   - Aborts at 95% of timeout (855 seconds for 900s timeout)
   - Handler decorator limits to 840 seconds (14 minutes)

## Files Changed

- `infra/embedding_step_functions/unified_embedding/utils/timeout_handler.py`
  - Added `start_time` reset in `set_lambda_context()`
  - Added `start_time` reset in `start_lambda_monitoring()`

## Related Issues

- Execution `a7bfaf55-8500-4e63-a4d8-bdea190d0c4a` failed due to this bug
- Execution `885ea78b-e884-4e63-94ae-7e29736cc4fe` succeeded (likely cold start)
- Both executions used the same ECR image, confirming the issue was not image-related

## Prevention

To prevent similar issues in the future:

1. **Always reset timing state when Lambda execution starts**
2. **Test with both cold and warm starts**
3. **Monitor timeout metrics to detect early timeouts**
4. **Use Lambda context for accurate timeout tracking**

## Next Steps

1. ✅ Fix applied to `timeout_handler.py`
2. ⏳ Deploy fix to AWS
3. ⏳ Test with step function execution
4. ⏳ Monitor for timeout errors
5. ⏳ Verify both cold and warm starts work correctly

