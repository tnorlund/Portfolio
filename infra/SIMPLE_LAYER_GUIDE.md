# Simplified Lambda Layer System

## Problem with Current System

The current `lambda_layer.py` has a complex event-driven architecture with many moving parts:

- S3 bucket notifications
- SQS queues
- EventBridge rules
- Step Functions state machines
- Multiple Lambda functions (publish handler, update handler)
- SQS trigger Lambda

This complexity creates many potential failure points and makes debugging difficult.

## Simplified Solution

The new `simple_lambda_layer.py` uses a single local command that orchestrates everything:

1. **Upload Package** → S3
2. **Trigger CodeBuild** → Wait for completion
3. **Publish Layer Version** → AWS Lambda API
4. **Update Functions** → AWS Lambda API

All in one synchronous script, making it much easier to debug and understand.

## How to Switch

### 1. Replace the import in your main infrastructure file

**Before:**

```python
from lambda_layer import dynamo_layer, label_layer, upload_layer
```

**After:**

```python
from simple_lambda_layer import simple_dynamo_layer, simple_label_layer, simple_upload_layer
```

### 2. Update any references

**Before:**

```python
layers=[dynamo_layer.arn]
```

**After:**

```python
layers=[simple_dynamo_layer.arn]
```

### 3. Force rebuild to test (optional)

```bash
pulumi config set lambda-layer:force-rebuild true --stack dev
pulumi up --stack dev
pulumi config set lambda-layer:force-rebuild false --stack dev
```

## Key Benefits

### ✅ **Simpler Architecture**

- Single orchestration script instead of 8+ resources
- No event-driven complexity
- Easy to understand execution flow

### ✅ **Better Debugging**

- All steps happen synchronously
- Clear error messages and status updates
- Logs are easier to follow

### ✅ **More Reliable**

- Fewer moving parts = fewer failure points
- No eventual consistency issues
- Immediate feedback on failures

### ✅ **Same Functionality**

- Change detection (hash-based)
- Conditional rebuilds
- Force rebuild option
- Automatic function updates
- Environment-based filtering

## How It Works

### Change Detection

```bash
# Calculates hash of package contents
# Only rebuilds if:
# 1. Hash changed, OR
# 2. Force rebuild enabled, OR
# 3. No previous build exists
```

### Build Process

```bash
# 1. Upload source to S3
# 2. Start CodeBuild project
# 3. Wait for build completion (polling every 30s)
# 4. Publish new layer version
# 5. Update all Lambda functions with correct environment tag
# 6. Save new hash
```

### Function Updates

```bash
# Finds all functions with matching environment tag
# Removes old versions of same layer
# Adds new layer version
# Updates function configuration
```

## Configuration

Same configuration options as the original:

```bash
# Force rebuild all layers
pulumi config set lambda-layer:force-rebuild true

# Set timeouts (optional)
pulumi config set lambda-layer:update_lambda_timeout 300
pulumi config set lambda-layer:update_lambda_memory 512
```

## Monitoring

The simplified approach provides clear output during execution:

```
Starting simplified layer build and update process...
Hash changed. Rebuilding layer.
Uploading source package...
Starting CodeBuild...
Build ID: receipt-dynamo-simple-layer-build-dev:12345
Waiting for build to complete...
Build status: IN_PROGRESS
Build status: SUCCEEDED
Build completed successfully!
Publishing new layer version...
New layer ARN: arn:aws:lambda:us-east-1:123:layer:receipt-dynamo-dev:42
Updating Lambda functions...
Checking function: receipt_processor_lambda
Updated receipt_processor_lambda successfully
Process completed successfully!
```

## Rollback Plan

If you need to rollback to the complex system:

1. Change imports back to `lambda_layer`
2. Run `pulumi up`
3. The old system will be recreated

Both systems can coexist temporarily during migration.
