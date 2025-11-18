# Container Lambda Deployment Status

## You Are Correct! ✅

The `embedding-line-poll-lambda-dev` is a **container-based Lambda** (PackageType: Image), not a zip-based Lambda that uses layers.

## How It Works

### Docker Image Build Process

1. **Dockerfile copies `receipt_label/`**:
   ```dockerfile
   COPY receipt_label/ /tmp/receipt_label/
   RUN pip install --no-cache-dir "/tmp/receipt_label[full]"
   ```

2. **CodeBuild builds the Docker image** when Pulumi detects changes

3. **Lambda uses the Docker image** from ECR

### Change Detection

The `CodeBuildDockerImage` component calculates a content hash based on:
- Dockerfile
- `receipt_dynamo/` directory
- `receipt_label/` directory (includes our fix!)
- Handler code in `unified_embedding/`

When the hash changes, it triggers:
1. Upload source to S3
2. Trigger CodeBuild
3. Build Docker image
4. Push to ECR
5. Update Lambda function

## Current Status

**Code committed**: 2025-11-16 21:15:51 UTC
**Lambda image**: `sha256:72402f310e4986bfdd360831cd2b766bbe7455ff7563f81eb06c03d0137decfb`

### Check if Docker Image Was Rebuilt

The Docker image needs to be rebuilt to include the fix. Check:

1. **Recent ECR images**:
   ```bash
   aws ecr describe-images \
     --repository-name embedding-line-poll-docker-repo-4146688 \
     --region us-east-1 \
     --max-items 5
   ```
   Look for images pushed AFTER 21:15:51 UTC.

2. **CodeBuild builds**:
   ```bash
   aws codebuild list-builds \
     --region us-east-1 \
     --sort-order DESCENDING \
     --max-items 20
   ```
   Look for builds with project name containing `embedding-line-poll`.

3. **Lambda function image**:
   ```bash
   aws lambda get-function \
     --function-name embedding-line-poll-lambda-dev \
     --region us-east-1
   ```
   Check if `Code.ImageUri` points to a new image digest.

## What Happens When `pulumi up` Runs

1. **Calculates content hash** for `receipt_label/` (includes committed fix)
2. **Compares with stored hash** in S3
3. **If different**:
   - Uploads source to S3
   - Triggers CodeBuild
   - Builds Docker image
   - Pushes to ECR
   - Updates Lambda function

## Verification After Deployment

After the Docker image is rebuilt:

1. **Check Lambda is using new image**:
   ```bash
   aws lambda get-function \
     --function-name embedding-line-poll-lambda-dev \
     --region us-east-1 \
     --query 'Code.ImageUri'
   ```
   Should show a new digest.

2. **Check logs for validation**:
   ```bash
   aws logs filter-log-events \
     --log-group-name "/aws/lambda/embedding-line-poll-lambda-dev" \
     --filter-pattern "validation" \
     --start-time $(date -u -v-1H +%s)000
   ```
   Should see "Validating uploaded delta..." messages.

## Key Difference from Layers

| Aspect | Container Lambda | Zip Lambda with Layers |
|--------|------------------|------------------------|
| **Code Location** | Baked into Docker image | In Lambda layer |
| **Update Trigger** | Docker image rebuild | Layer version update |
| **Build Process** | CodeBuild → Docker → ECR | CodeBuild → Layer → Lambda |
| **Change Detection** | Content hash of source files | Content hash of package |

## Summary

✅ **You're correct** - the fix is in the Docker image, not a layer
✅ **The Docker image needs to be rebuilt** to include the fix
✅ **`pulumi up` should trigger the rebuild** when it detects the hash change
⏳ **Check CodeBuild/ECR** to verify the image was rebuilt after the commit

