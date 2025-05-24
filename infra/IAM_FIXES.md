# Lambda Layer IAM Permission Fixes

## Issues Encountered

During the implementation of the lambda layer components, we encountered **three critical IAM permission issues** that prevented CodeBuild from successfully building and publishing lambda layers.

## 1. CloudWatch Logs Access Denied

### Error

```
ACCESS_DENIED: Service role arn:aws:iam::681647709217:role/receipt-dynamo-fast-codebuild-role-bf856b5 does not allow AWS CodeBuild to create Amazon CloudWatch Logs log streams for build arn:aws:codebuild:us-east-1:681647709217:build/receipt-dynamo-fast-layer-build-dev:817f3b8c-54de-45c7-b547-05ace80ed7fd. Error message: User: arn:aws:sts::681647709217:assumed-role/receipt-dynamo-fast-codebuild-role-bf856b5/AWSCodeBuild-817f3b8c-54de-45c7-b547-05ace80ed7fd is not authorized to perform: logs:CreateLogStream on resource: arn:aws:logs:us-east-1:681647709217:log-group:/aws/codebuild/receipt-dynamo-fast-layer-build-dev:log-stream:817f3b8c-54de-45c7-b547-05ace80ed7fd because no identity-based policy allows the logs:CreateLogStream action
```

### Root Cause

The IAM policy for CodeBuild only included permissions for log groups but not log streams. CodeBuild needs to create both log groups and log streams.

### Fix

**Before:**

```python
"Resource": f"arn:aws:logs:{aws.config.region}:{aws.get_caller_identity().account_id}:log-group:/aws/codebuild/*"
```

**After:**

```python
"Resource": [
    f"arn:aws:logs:{aws.config.region}:{aws.get_caller_identity().account_id}:log-group:/aws/codebuild/*",
    f"arn:aws:logs:{aws.config.region}:{aws.get_caller_identity().account_id}:log-group:/aws/codebuild/*:*"
]
```

The second ARN pattern (`/*:*`) allows access to log streams within the log groups.

## 2. Lambda Layer Publishing Access Denied

### Error

```
An error occurred (AccessDeniedException) when calling the PublishLayerVersion operation: User: arn:aws:sts::681647709217:assumed-role/receipt-upload-fast-codebuild-role-97b2436/AWSCodeBuild-0b9b626f-4066-49df-97b6-1f0491acd3dc is not authorized to perform: lambda:PublishLayerVersion on resource: arn:aws:lambda:us-east-1:681647709217:layer:receipt-upload-dev because no identity-based policy allows the lambda:PublishLayerVersion action
```

### Root Cause

The IAM policy only allowed access to versioned layer ARNs (`layer:name:version`) but the `PublishLayerVersion` operation needs access to the base layer ARN (`layer:name`) as well.

### Fix

**Before:**

```python
"Resource": f"arn:aws:lambda:*:*:layer:{layer_name}:*"
```

**After:**

```python
"Resource": [
    f"arn:aws:lambda:*:*:layer:{layer_name}",      # Base layer ARN
    f"arn:aws:lambda:*:*:layer:{layer_name}:*"     # Versioned layer ARN
]
```

## 3. S3 Bucket Access Denied for Layer Publishing

### Error

```
An error occurred (AccessDeniedException) when calling the PublishLayerVersion operation: Your access has been denied by S3, please make sure your request credentials have permission to GetObject for fast-lambda-layer-receipt-dynamo-artifacts-dev/receipt-dynamo-dev/layer.zip. S3 Error Code: AccessDenied. S3 Error Message: User: arn:aws:sts::681647709217:assumed-role/receipt-dynamo-fast-codebuild-role-bf856b5/AWSCodeBuild-913409e2-2793-4865-a1f3-66e02d53aa00 is not authorized to perform: s3:ListBucket on resource: "arn:aws:s3:::fast-lambda-layer-receipt-dynamo-artifacts-dev" because no identity-based policy allows the s3:ListBucket action
```

### Root Cause

When Lambda tries to read the layer zip file from S3 during `PublishLayerVersion`, it needs `s3:ListBucket` permission on the bucket itself, not just object-level permissions.

### Fix

**Before:**

```python
"Action": [
    "s3:GetBucketAcl",
    "s3:GetBucketLocation",
],
```

**After:**

```python
"Action": [
    "s3:GetBucketAcl",
    "s3:GetBucketLocation",
    "s3:ListBucket",           # Added this
],
```

## Files Updated

These fixes were applied to all three lambda layer implementations:

1. **`fast_lambda_layer.py`** - The recommended hybrid approach
2. **`simple_lambda_layer.py`** - The simplified synchronous approach
3. **`lambda_layer.py`** - The original complex event-driven approach

## AWS Lambda Layer ARN Patterns

For reference, here are the different Lambda layer ARN patterns:

- **Base Layer ARN**: `arn:aws:lambda:region:account:layer:layer-name`
- **Versioned Layer ARN**: `arn:aws:lambda:region:account:layer:layer-name:version`
- **Latest Version ARN**: `arn:aws:lambda:region:account:layer:layer-name:$LATEST`

The `PublishLayerVersion` operation creates a new version, so it needs permission on the base layer ARN.

## S3 Permission Requirements

For Lambda layer publishing from S3, the following S3 permissions are required:

**Object-level permissions** (on `bucket/path/*`):

- `s3:GetObject` - Read the layer zip file
- `s3:PutObject` - Upload build artifacts
- `s3:GetObjectVersion` - Access versioned objects

**Bucket-level permissions** (on `bucket`):

- `s3:ListBucket` - Required by Lambda when reading objects
- `s3:GetBucketAcl` - Get bucket access control list
- `s3:GetBucketLocation` - Get bucket region

## Testing

After applying these fixes, the lambda layer build process should work correctly:

1. CodeBuild can create CloudWatch log groups and log streams
2. CodeBuild can publish new lambda layer versions
3. CodeBuild can update Lambda functions with the new layer versions
4. Lambda can read layer zip files from S3 buckets

## Prevention

To prevent these issues in future IAM policies:

1. **Always include log stream permissions** when granting CloudWatch Logs access to AWS services
2. **Include both base and versioned ARN patterns** when granting Lambda layer permissions
3. **Include s3:ListBucket permission** when granting S3 bucket access for Lambda operations
4. **Test with minimal permissions first** and expand as needed based on actual AWS service requirements
