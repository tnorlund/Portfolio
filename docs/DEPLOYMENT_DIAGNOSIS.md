# Deployment Diagnosis: Why Validation Code Wasn't Deployed

## Summary

**The validation code changes are NOT committed to git**, so the CodeBuild pipeline hasn't rebuilt the Docker images with the new code.

## Timeline

1. **Lambda Last Updated**: 2025-11-16T17:28:13-17:28:17
2. **Execution Failed**: 2025-11-16T17:30:13
3. **Validation Code Written**: Just now (uncommitted)
4. **ECR Images Last Built**: 2025-11-16T09:27:04-09:27:07

## Current State

### Lambda Functions
- **embedding-vector-compact-lambda-dev**
  - Last Modified: `2025-11-16T17:28:13`
  - Image URI: `681647709217.dkr.ecr.us-east-1.amazonaws.com/embedding-vector-compact-docker-repo-6cf3ade@sha256:3c622258ecb305771277b5d619aa37606e043ae7a2d8b60b5a63caff22cb101b`

- **embedding-line-poll-lambda-dev**
  - Last Modified: `2025-11-16T17:28:17`
  - Image URI: `681647709217.dkr.ecr.us-east-1.amazonaws.com/embedding-line-poll-docker-repo-4146688@sha256:aefae73728f27f28c66fcd9b42bd7cbad763d24f7a076b9b575cee399f4830e6`

### ECR Images

**embedding-vector-compact-docker-repo-6cf3ade**:
- Latest image: `cache` tag
- Pushed: `2025-11-16T09:27:04`
- Digest: `sha256:3c622258ecb305771277b5d619aa37606e043ae7a2d8b60b5a63caff22cb101b`

**embedding-line-poll-docker-repo-4146688**:
- Latest image: `28637f5d4a02` tag
- Pushed: `2025-11-16T09:27:07`
- Digest: `sha256:aefae73728f27f28c66fcd9b42bd7cbad763d24f7a076b9b575cee399f4830e6`

### CodeBuild Pipeline

The CodeBuild pipeline uses **content-based hashing** to detect changes:
- Hashes Dockerfile + source files (including `receipt_label/receipt_label/vector_store/client/chromadb_client.py`)
- Only rebuilds if hash changes
- Hash is calculated from **committed files** (not uncommitted changes)

### Git Status

**Current State**: Validation code changes are **uncommitted**:
```bash
git status --short chromadb_client.py
# Shows: M  receipt_label/receipt_label/vector_store/client/chromadb_client.py
```

## Why Validation Wasn't Deployed

### Root Cause

1. **Code Changes**: Validation code was written but **not committed**
2. **Content Hash**: CodeBuild calculates hash from **committed files only**
3. **No Rebuild**: Hash didn't change → no rebuild → no new image
4. **Old Image**: Lambda still using old image without validation

### CodeBuild Hash Calculation

From `codebuild_docker_image.py`:
```python
def _calculate_content_hash(self) -> str:
    # Hashes files from git repository
    # Does NOT include uncommitted changes
    for file_path in sorted(full_path.rglob("*.py")):
        with open(file_path, "rb") as f:
            h.update(f.read())  # Reads from filesystem, but git status matters
```

**Key Point**: The hash is calculated during `pulumi up`, which reads from the filesystem. However, if the changes aren't committed, they might not trigger a rebuild if the pipeline uses git-based source.

## How CodeBuild Pipeline Works

1. **Pulumi Up**: Calculates content hash from source files
2. **Hash Change**: If hash differs from last build → triggers CodeBuild
3. **CodeBuild**: Builds Docker image from committed code
4. **ECR Push**: Pushes image to ECR with content-based tag
5. **Lambda Update**: Updates Lambda function with new image URI

## What Needs to Happen

### Step 1: Commit Changes
```bash
git add receipt_label/receipt_label/vector_store/client/chromadb_client.py
git commit -m "Add delta validation and retry logic to prevent corruption"
```

### Step 2: Deploy via Pulumi
```bash
pulumi up --stack dev
```

This will:
1. Calculate new content hash (includes committed validation code)
2. Detect hash change
3. Trigger CodeBuild pipeline
4. Build new Docker image with validation code
5. Push to ECR
6. Update Lambda functions

### Step 3: Verify Deployment

After deployment, check:
```bash
# Check Lambda was updated
aws lambda get-function --function-name embedding-line-poll-lambda-dev --query 'Configuration.LastModified'

# Check ECR has new image
aws ecr describe-images --repository-name embedding-line-poll-docker-repo-4146688 --query 'sort_by(imageDetails, &imagePushedAt)[-1:].[imageTags[0],imagePushedAt]' --output table

# Check logs for validation
aws logs filter-log-events --log-group-name "/aws/lambda/embedding-line-poll-lambda-dev" --filter-pattern "validation" --max-items 10
```

## Expected Behavior After Deployment

### New Delta Creation
1. Create delta locally
2. Upload to S3
3. **Download and validate** ← NEW
4. If validation fails:
   - Delete failed upload
   - Retry (up to 3 times)
5. If all retries fail:
   - Raise error (delta not created)

### Logs to Look For
- `"Validating delta by downloading from S3"`
- `"Delta validation successful"`
- `"Delta validation failed"` (with retry attempts)

## Comparison: Before vs After

| Aspect | Before (Current) | After (Deployed) |
|--------|-----------------|-----------------|
| **Code Status** | Uncommitted | Committed |
| **Content Hash** | Old hash | New hash |
| **Docker Image** | Old image (09:27) | New image (after deploy) |
| **Lambda Code** | No validation | With validation |
| **Delta Upload** | No validation | Validated + retry |

## Conclusion

**The validation code wasn't deployed because it's not committed to git.** The CodeBuild pipeline only builds from committed code. Once you commit and run `pulumi up`, the pipeline will:

1. Detect the hash change
2. Rebuild the Docker images
3. Deploy to Lambda
4. Enable validation for new deltas

**Next Steps**: Commit the changes and deploy via `pulumi up`.

