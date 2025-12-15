# Pulumi Hash Detection Issue

## Problem

**Pulumi is NOT detecting the hash change** even though the filesystem has the validation code.

### Evidence

1. **Filesystem hash**: `846fe866d8b7` (with validation code)
2. **Pulumi showed**: `28637f5d4a02` (old hash, without validation)
3. **Hash mismatch**: Pulumi should have detected this and triggered a rebuild

### Root Cause

Pulumi calculated the hash as `28637f5d4a02` but the actual current hash is `846fe866d8b7`. This suggests:

1. **Cached state**: Pulumi might be using cached state instead of recalculating
2. **Timing issue**: Hash calculated before files were committed/updated
3. **State file**: Pulumi state might have the old hash stored

### Solution

**Force Pulumi to recalculate** by either:

1. **Delete the hash file in S3** (forces upload):
   ```bash
   aws s3 rm s3://embedding-line-poll-docker-artifacts-3a069d5/embedding-line-poll-/hash.txt
   ```

2. **Force rebuild via config**:
   ```bash
   pulumi config set docker-build:force-rebuild true
   pulumi up --stack tnorlund/portfolio/dev
   ```

3. **Manually trigger CodeBuild**:
   ```bash
   aws codebuild start-build --project-name embedding-line-poll-docker-builder-1ec00a3
   ```

4. **Refresh Pulumi state**:
   ```bash
   pulumi refresh --stack tnorlund/portfolio/dev
   pulumi up --stack tnorlund/portfolio/dev
   ```

### Expected Behavior

When code changes:
1. ✅ Pulumi calculates hash from filesystem
2. ✅ Compares with stored hash in S3
3. ✅ If different → uploads context → triggers CodeBuild
4. ✅ CodeBuild builds image → pushes to ECR
5. ✅ Lambda updates with new image

### Current Status

- ❌ Step 1 failed: Pulumi used old hash instead of recalculating
- ❌ No rebuild triggered
- ❌ Lambda still using old image

### Verification

After forcing rebuild, verify:
```bash
# Check new hash was calculated
pulumi preview | grep "Hash:"

# Check CodeBuild was triggered
aws codebuild list-builds-for-project \
  --project-name embedding-line-poll-docker-builder-1ec00a3 \
  --max-items 1

# Check Lambda was updated
aws lambda get-function \
  --function-name embedding-line-poll-lambda-dev \
  --query 'Configuration.LastModified'
```

