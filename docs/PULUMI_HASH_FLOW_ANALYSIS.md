# Pulumi Hash Flow Analysis

## How Pulumi Determines Updates

### Flow Overview

1. **Hash Calculation** (`_calculate_content_hash()`)
   - Calculates SHA256 hash of:
     - Dockerfile
     - `receipt_label/receipt_label/` (all Python files)
     - Handler directory (`unified_embedding/`)
   - Hash is calculated from **filesystem** (not git)

2. **Upload Command** (`upload_cmd`)
   - Generated script checks S3 for stored hash
   - If hash matches → skips upload (exits early)
   - If hash differs → uploads context.zip to S3
   - Stores new hash in S3 (`hash.txt`)
   - **Trigger**: `triggers=[content_hash]` - runs when hash changes

3. **Pipeline Trigger** (`pipeline_trigger_cmd`)
   - Triggers CodePipeline execution
   - **Trigger**: `triggers=[content_hash]` - runs when hash changes
   - **Depends on**: `upload_cmd` (must complete first)

4. **CodeBuild**
   - Downloads context.zip from S3
   - Builds Docker image
   - Pushes to ECR
   - Updates Lambda function

## Why Hash Didn't Change

### The Problem

**Pulumi calculated hash `28637f5d4a02`** - and this is **CORRECT**!

The hash matches because:
- ✅ Validation code IS in the committed file
- ✅ Filesystem file matches committed file
- ✅ Hash calculation includes `chromadb_client.py`
- ✅ Hash is deterministic and correct

### Why No Rebuild Was Triggered

The hash `28637f5d4a02` was **already stored in S3** from a previous build. When Pulumi ran:

1. Calculated hash: `28637f5d4a02`
2. Checked S3: Found `28637f5d4a02`
3. **Hash matches** → Skipped upload
4. **No upload** → No pipeline trigger
5. **No rebuild** → Lambda not updated

### The Real Issue

**The hash was calculated BEFORE the validation code was committed!**

Timeline:
1. **Before commit**: Hash was `28637f5d4a02` (without validation)
2. **Code committed**: Validation code added
3. **After commit**: Hash should be different, but...
4. **Pulumi ran**: Calculated hash `28637f5d4a02` (same as before!)

This suggests:
- Hash was calculated from **committed files** (not filesystem)
- OR hash calculation happened before commit was written
- OR there's a caching issue in Pulumi

## How to Fix

### Option 1: Force Rebuild
```bash
# Delete hash file to force upload
aws s3 rm s3://embedding-line-poll-docker-artifacts-3a069d5/embedding-line-poll-/hash.txt

# Run pulumi up again
pulumi up --stack tnorlund/portfolio/dev
```

### Option 2: Use Force Rebuild Config
```bash
pulumi config set docker-build:force-rebuild true
pulumi up --stack tnorlund/portfolio/dev
```

### Option 3: Touch a File
```bash
# Modify a file to force hash change
touch receipt_label/receipt_label/vector_store/client/chromadb_client.py
pulumi up --stack tnorlund/portfolio/dev
```

## Root Cause Analysis

The hash calculation is **correct**, but Pulumi's **change detection** failed because:

1. **Hash matches stored value** → Upload skipped
2. **No upload** → No pipeline trigger
3. **No rebuild** → Lambda not updated

The issue is that Pulumi calculated the hash **before** detecting that the files changed, or the hash was already stored from a previous build with the same content.

## Expected Behavior

When code changes:
1. ✅ Pulumi calculates new hash
2. ✅ Compares with S3 stored hash
3. ✅ If different → Uploads context
4. ✅ Triggers pipeline
5. ✅ CodeBuild builds new image
6. ✅ Lambda updates

## Current Behavior

When code changes:
1. ✅ Pulumi calculates hash (but gets same value)
2. ✅ Compares with S3 stored hash
3. ❌ **Hash matches** → Skips upload
4. ❌ **No upload** → No pipeline trigger
5. ❌ **No rebuild** → Lambda not updated

## Solution

**Force Pulumi to recalculate** by either:
- Deleting the hash file in S3
- Using `force-rebuild` config
- Or ensuring hash calculation happens after all changes are committed

