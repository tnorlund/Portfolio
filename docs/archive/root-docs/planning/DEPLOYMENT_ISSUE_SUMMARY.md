# Deployment Issue Summary

## Problem
The `pulumi up` command is hanging when trying to create the `upload-images-process-ocr-image-function` Lambda.

## Root Cause
The `CodeBuildDockerImage` component is trying to create a Lambda function with the same name as the existing zip-based Lambda (`upload-images-dev-process-ocr-results`), but:

1. We deleted the old Lambda from Pulumi state (with `--target-dependents`)
2. The actual AWS Lambda resource still exists
3. The new `CodeBuildDockerImage` component is trying to CREATE (not update) the Lambda
4. AWS returns a 409 conflict initially, but then Pulumi retries and hangs

## What We've Done So Far

### 1. Fixed Missing `receipt_upload` Package ✅
- Added `receipt_upload` to `source_paths` parameter in `infra/upload_images/infra.py`
- Updated `CodeBuildDockerImage` to support optional source paths
- Commit: `9cd9c3e8` - "refactor: Make receipt_upload an optional source_path"

### 2. Fixed SQS Permissions ✅
- Added SQS `ReceiveMessage`, `DeleteMessage`, `GetQueueAttributes` permissions to `embed_role`
- Moved policy after queue creation to fix AttributeError
- Commits: `6432ea24`, `295a490c`

### 3. Removed Old Lambda from Pulumi State ✅
- Deleted `upload-images-process-ocr-results-lambda` from Pulumi state
- Used `--target-dependents` to also remove event source mapping
- Command: `pulumi state delete ... --target-dependents --yes`

### 4. Current Status: Lambda Creation Hanging ❌
- Bootstrap image exists in ECR ✅
- Lambda function exists in AWS (from before) ✅
- Pulumi is trying to CREATE the Lambda (should be updating) ❌

## Solution Options

### Option 1: Import Existing Lambda (Recommended)
Instead of creating a new Lambda, import the existing one into the new Pulumi resource:

```bash
cd /Users/tnorlund/GitHub/example/infra

# Import the existing Lambda into the new CodeBuildDockerImage component's Lambda resource
pulumi import \
  'urn:pulumi:dev::portfolio::upload_images.infra-upload-images$codebuild-docker:upload-images-process-ocr-image$aws:lambda/function:Function::upload-images-process-ocr-image-function' \
  upload-images-dev-process-ocr-results
```

Then run `pulumi up` to update it to use the container image.

### Option 2: Delete Lambda from AWS and Recreate
Delete the actual AWS Lambda and let Pulumi create it fresh:

```bash
# Delete the Lambda from AWS
aws lambda delete-function --function-name upload-images-dev-process-ocr-results

# Delete the event source mapping if it exists
aws lambda list-event-source-mappings --function-name upload-images-dev-process-ocr-results \
  --query 'EventSourceMappings[0].UUID' --output text | \
  xargs -I {} aws lambda delete-event-source-mapping --uuid {}

# Run pulumi up
cd /Users/tnorlund/GitHub/example/infra
pulumi up --yes
```

### Option 3: Modify CodeBuildDockerImage to Update Instead of Create
Update the `CodeBuildDockerImage` component to check if the Lambda exists and update it instead of creating:

```python
# In _create_lambda_function method, add logic to:
# 1. Check if Lambda exists
# 2. If exists, import it into Pulumi state
# 3. Then update it
```

This is more complex and requires component changes.

## Recommended Next Steps

1. **Try Option 1 (Import)** - Cleanest approach, preserves existing Lambda
2. If import fails, **Try Option 2 (Delete & Recreate)** - Nuclear option but guaranteed to work
3. After Lambda is in Pulumi state, **trigger CodePipeline** to build the actual container image
4. **Wait for CodePipeline** to complete (~5-10 minutes)
5. **Verify** Lambda is using Image package type
6. **Test end-to-end** workflow

## Files Modified

- `infra/codebuild_docker_image.py` - Added optional source_paths support
- `infra/upload_images/infra.py` - Added receipt_upload to source_paths, added SQS permissions
- `infra/upload_images/container_ocr/` - New container-based Lambda code

## Current Branch

`feat/efs_modular_rebase` - 17 commits ahead of `origin/main`

Latest commits:
- `295a490c` - fix: Move SQS policy after queue creation
- `6432ea24` - fix: Add SQS permissions to embed_from_ndjson Lambda role
- `9cd9c3e8` - refactor: Make receipt_upload an optional source_path
- `44f5111c` - fix: Include receipt_upload package in CodeBuild Docker context

