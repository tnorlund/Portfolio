# LayoutLM Inference Cache Issue Analysis

## Problem
The `/layoutlm_inference` API endpoint is failing on the prod stack with a "cache is broken" error.

## Architecture Overview

### Components
1. **Cache Generator Lambda** (`layoutlm-inference-cache-generator-prod`)
   - Container-based Lambda that runs inference
   - Scheduled to run every 2 minutes via EventBridge
   - Downloads model from training bucket
   - Generates cache and uploads to S3

2. **API Lambda** (`api_layoutlm_inference_GET_lambda`)
   - Simple Python Lambda that reads cache from S3
   - Serves cached results via API Gateway

3. **Cache Bucket** (`layoutlm-inference-cache-generator-prod-cache-bucket`)
   - Dedicated S3 bucket for API cache
   - Cache stored at: `layoutlm-inference-cache/latest.json`
   - **No lifecycle policies configured** (good - cache won't be auto-deleted)

4. **Training Bucket** (LayoutLM training bucket)
   - Stores model artifacts
   - Models stored under: `runs/{job_id}/best/` or `runs/{job_id}/checkpoint-*/`
   - Model auto-discovery looks for latest run with `best/` directory
   - **No lifecycle policies configured** (models won't be auto-deleted)

## Potential Root Causes

### 1. Cache File Missing (Most Likely)
**Symptom**: API returns 404 "Cache not found" error

**Possible reasons**:
- Cache generator Lambda hasn't run successfully
- Cache generator Lambda is failing
- Cache was manually deleted
- IAM permissions issue preventing cache upload

**Check**:
```bash
# Get cache bucket name
pulumi stack output --stack prod layoutlm_inference_cache_bucket

# Check if cache exists
aws s3 ls s3://<bucket-name>/layoutlm-inference-cache/latest.json

# Check cache generator Lambda logs
aws logs tail /aws/lambda/layoutlm-inference-cache-generator-prod-lambda-prod --follow
```

### 2. Model Missing in Training Bucket
**Symptom**: Cache generator Lambda fails with "model not found" error

**Possible reasons**:
- No training runs completed in prod
- Model files deleted manually
- Wrong bucket configured

**Check**:
```bash
# Get training bucket name
pulumi config get --stack prod ml-training:training-bucket-name

# List runs in training bucket
aws s3 ls s3://<training-bucket>/runs/

# Check for best model
aws s3 ls s3://<training-bucket>/runs/*/best/
```

### 3. IAM Permissions Issue
**Symptom**: Lambda fails with AccessDenied errors

**Check**:
- Cache generator Lambda role has:
  - Read access to training bucket (`s3:GetObject`, `s3:ListBucket`)
  - Read/write access to cache bucket (`s3:GetObject`, `s3:PutObject`, `s3:ListBucket`)
  - DynamoDB read access (`dynamodb:Query`, `dynamodb:GetItem`)

### 4. Cache Generator Lambda Not Running
**Symptom**: No recent logs in CloudWatch

**Check**:
```bash
# Check EventBridge schedule
aws events list-rules --name-prefix layoutlm-inference-cache-generator-prod

# Check Lambda function exists
aws lambda get-function --function-name layoutlm-inference-cache-generator-prod-lambda-prod

# Check recent invocations
aws logs filter-log-events \
  --log-group-name "/aws/lambda/layoutlm-inference-cache-generator-prod-lambda-prod" \
  --start-time $(date -u -d '1 hour ago' +%s)000
```

## Storage Locations

### Cache Storage
- **Bucket**: `layoutlm-inference-cache-generator-prod-cache-bucket` (or similar)
- **Key**: `layoutlm-inference-cache/latest.json`
- **Lifecycle**: No expiration configured (persistent)

### Model Storage
- **Bucket**: LayoutLM training bucket (from config or training infra)
- **Path**: `runs/{job_id}/best/` (preferred) or `runs/{job_id}/checkpoint-*/`
- **Lifecycle**: No expiration configured (persistent)
- **Auto-discovery**: Code looks for latest run with `best/` directory containing `model.safetensors`

## Lifecycle Policies

### Current State
- **Cache bucket**: No lifecycle policies configured ✅
- **Training bucket**: No lifecycle policies configured ✅

### Recommendation
**No changes needed** - The lack of lifecycle policies is correct. Both buckets should retain their data:
- Cache bucket: Keep cache file for API serving
- Training bucket: Keep model artifacts for inference

If lifecycle policies were added, they could accidentally delete:
- The cache file (breaking the API)
- Model files (breaking inference)

## Troubleshooting Steps

### Step 1: Verify Cache Exists
```bash
STACK=prod
BUCKET=$(pulumi stack output --stack "$STACK" layoutlm_inference_cache_bucket)
aws s3 ls "s3://$BUCKET/layoutlm-inference-cache/latest.json"
```

### Step 2: Check Cache Generator Lambda
```bash
LAMBDA_NAME=$(aws lambda list-functions --query "Functions[?contains(FunctionName, 'layoutlm-inference-cache-generator-prod')].FunctionName" --output text | head -1)
aws logs tail "/aws/lambda/$LAMBDA_NAME" --follow
```

### Step 3: Manually Trigger Cache Generation
```bash
# Use the trigger script
./scripts/trigger_layoutlm_cache.sh prod

# Or invoke directly
aws lambda invoke \
  --function-name layoutlm-inference-cache-generator-prod-lambda-prod \
  --payload '{}' \
  response.json
```

### Step 4: Verify Model Exists
```bash
TRAINING_BUCKET=$(pulumi config get --stack prod ml-training:training-bucket-name)
aws s3 ls "s3://$TRAINING_BUCKET/runs/" --recursive | grep "best/model.safetensors"
```

### Step 5: Check API Lambda Configuration
```bash
API_LAMBDA=$(aws lambda list-functions --query "Functions[?contains(FunctionName, 'api_layoutlm_inference')].FunctionName" --output text | head -1)
aws lambda get-function-configuration --function-name "$API_LAMBDA" | jq '.Environment.Variables'
```

## Fixes

### If Cache is Missing
1. Check cache generator Lambda logs for errors
2. Verify model exists in training bucket
3. Manually trigger cache generation
4. Check IAM permissions

### If Model is Missing
1. Copy model from dev to prod:
   ```bash
   python scripts/copy_layoutlm_model_dev_to_prod.py
   ```
2. Or train a new model in prod
3. Verify model path: `runs/{job_id}/best/` should contain `model.safetensors`

### If IAM Permissions Issue
1. Check cache generator Lambda role policies
2. Verify bucket ARNs match
3. Ensure policies include required S3 and DynamoDB permissions

## Verification Script

Use the provided verification script:
```bash
./scripts/verify_layoutlm_inference.sh prod
```

This script checks:
- Cache generator Lambda exists
- Cache bucket exists
- Cache file exists
- API Lambda exists
- API Gateway endpoint
- Recent Lambda invocations

## Root Cause Found! 🎯

**Issue**: The API Lambda has the wrong bucket name configured!

### Current State (Verified via AWS CLI)
- ✅ **Cache file exists**: `s3://layoutlm-inference-cache-generator-prod-cache-bucket-b6f5ed6/layoutlm-inference-cache/latest.json`
  - Last updated: 2025-11-24 09:30:39
  - Size: 290,878 bytes
- ✅ **Cache generator Lambda working**: Successfully generating cache every 2 minutes
- ✅ **Model exists**: Cache generator successfully loads model from training bucket
- ❌ **API Lambda misconfigured**: Has `S3_CACHE_BUCKET: "placeholder-bucket-name"` instead of actual bucket name

### The Problem
The API Lambda (`api_layoutlm_inference_GET_lambda-3e4e71f` and `api_layoutlm_inference_GET_lambda-a102654`) both have:
```json
{
    "S3_CACHE_BUCKET": "placeholder-bucket-name"
}
```

But the actual cache bucket is:
```
layoutlm-inference-cache-generator-prod-cache-bucket-b6f5ed6
```

### Why This Happened
The infrastructure code creates the API Lambda at module import time with a placeholder bucket name, then updates it in `__main__.py` when the cache generator is created. The Lambda should be replaced when `pulumi up` runs due to `replace_on_changes=["environment"]`, but this hasn't happened yet.

### The Fix
Run `pulumi up` in the prod stack to update the Lambda with the correct bucket name:

```bash
cd infra
pulumi stack select prod
pulumi up
```

This will:
1. Detect that the environment variable changed from placeholder to real bucket
2. Replace the Lambda function with the correct bucket name
3. Update the API Gateway integration to use the new Lambda

### Verification After Fix
```bash
# Check Lambda has correct bucket
aws lambda get-function-configuration \
  --function-name <new-lambda-name> \
  --query 'Environment.Variables.S3_CACHE_BUCKET'

# Test the API
curl https://api.tylernorlund.com/layoutlm_inference
```

## Summary

**Cache Location**: ✅ In S3 (dedicated cache bucket) - **EXISTS**
**Model Location**: ✅ In S3 (training bucket) - **EXISTS**
**Lifecycle Policies**: ✅ None configured (correct - prevents accidental deletion)
**Cache Generator**: ✅ Working correctly
**API Lambda**: ❌ **MISCONFIGURED** - has placeholder bucket name

**Root Cause**: API Lambda environment variable not updated after cache bucket creation

**Fix**: Run `pulumi up` to update Lambda with correct bucket name

