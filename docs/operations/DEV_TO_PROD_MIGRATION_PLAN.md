# Dev to Prod Migration Plan

## Overview

This document outlines the plan to migrate all data and resources from the dev stack to the prod stack, including:
- DynamoDB records (receipts, images, labels, metadata)
- S3 raw images
- S3 CDN images
- ChromaDB embeddings (S3 snapshots/deltas)
- LayoutLM trained model

## Prerequisites

1. **Access to both stacks**
   ```bash
   # Verify access to both stacks
   cd infra
   pulumi stack select dev
   pulumi stack output

   pulumi stack select prod
   pulumi stack output
   ```

2. **AWS CLI configured** with appropriate credentials for both environments

3. **Python dependencies** installed:
   ```bash
   pip install boto3 pulumi pulumi-aws
   ```

## Step 1: Get Resource Names

First, we need to identify all the resource names from both stacks:

```bash
cd infra

# Get DEV resources
pulumi stack select dev
DEV_TABLE=$(pulumi stack output dynamodb_table_name)
DEV_RAW_BUCKET=$(pulumi stack output raw_bucket_name)
DEV_CDN_BUCKET=$(pulumi stack output cdn_bucket_name)
DEV_CHROMADB_BUCKET=$(pulumi stack output embedding_chromadb_bucket_name)
DEV_LAYOUTLM_BUCKET=$(pulumi stack output layoutlm_training_bucket)

# Get PROD resources
pulumi stack select prod
PROD_TABLE=$(pulumi stack output dynamodb_table_name)
PROD_RAW_BUCKET=$(pulumi stack output raw_bucket_name)
PROD_CDN_BUCKET=$(pulumi stack output cdn_bucket_name)
PROD_CHROMADB_BUCKET=$(pulumi stack output embedding_chromadb_bucket_name)
PROD_LAYOUTLM_BUCKET=$(pulumi stack output layoutlm_training_bucket)

# Save to file for reference
cat > migration_resources.txt <<EOF
DEV_TABLE=$DEV_TABLE
DEV_RAW_BUCKET=$DEV_RAW_BUCKET
DEV_CDN_BUCKET=$DEV_CDN_BUCKET
DEV_CHROMADB_BUCKET=$DEV_CHROMADB_BUCKET
DEV_LAYOUTLM_BUCKET=$DEV_LAYOUTLM_BUCKET

PROD_TABLE=$PROD_TABLE
PROD_RAW_BUCKET=$PROD_RAW_BUCKET
PROD_CDN_BUCKET=$PROD_CDN_BUCKET
PROD_CHROMADB_BUCKET=$PROD_CHROMADB_BUCKET
PROD_LAYOUTLM_BUCKET=$PROD_LAYOUTLM_BUCKET
EOF
```

## Step 2: DynamoDB Migration

### 2.1 Export All Entities from Dev

We'll use the existing `copy_entities_dev_to_prod.py` script as a reference, but we need a comprehensive migration script.

**Strategy:**
- Scan all items from dev table
- Filter by entity type (Image, Receipt, ReceiptWordLabel, ReceiptMetadata, etc.)
- Update bucket references in S3 URLs
- Batch write to prod table

**Script:** `scripts/migrate_dynamodb_dev_to_prod.py`

```python
# Key considerations:
# 1. Update S3 bucket names in all fields (raw_bucket, cdn_bucket)
# 2. Preserve all relationships (PK/SK structure)
# 3. Handle TTL fields correctly
# 4. Batch writes for efficiency (25 items per batch)
# 5. Dry-run mode first
```

### 2.2 Migration Steps

```bash
# Dry run first
python scripts/migrate_dynamodb_dev_to_prod.py \
  --dev-table $DEV_TABLE \
  --prod-table $PROD_TABLE \
  --dev-raw-bucket $DEV_RAW_BUCKET \
  --prod-raw-bucket $PROD_RAW_BUCKET \
  --dev-cdn-bucket $DEV_CDN_BUCKET \
  --prod-cdn-bucket $PROD_CDN_BUCKET \
  --dry-run

# Review the output, then run for real
python scripts/migrate_dynamodb_dev_to_prod.py \
  --dev-table $DEV_TABLE \
  --prod-table $PROD_TABLE \
  --dev-raw-bucket $DEV_RAW_BUCKET \
  --prod-raw-bucket $PROD_RAW_BUCKET \
  --dev-cdn-bucket $DEV_CDN_BUCKET \
  --prod-cdn-bucket $PROD_CDN_BUCKET
```

## Step 3: S3 Raw Images Migration

### 3.1 Sync Raw Images

```bash
# Sync all raw images from dev to prod
aws s3 sync \
  s3://$DEV_RAW_BUCKET/ \
  s3://$PROD_RAW_BUCKET/ \
  --exclude "*.json" \
  --exclude "*.ndjson" \
  --exclude "*/delta/*" \
  --exclude "*/snapshot/*"

# Verify count matches
DEV_COUNT=$(aws s3 ls s3://$DEV_RAW_BUCKET/ --recursive | wc -l)
PROD_COUNT=$(aws s3 ls s3://$PROD_RAW_BUCKET/ --recursive | wc -l)
echo "Dev images: $DEV_COUNT, Prod images: $PROD_COUNT"
```

**Note:** This preserves the exact S3 key structure, so DynamoDB references will work after bucket name updates.

## Step 4: S3 CDN Images Migration

### 4.1 Sync CDN Images

```bash
# Sync all CDN images from dev to prod
aws s3 sync \
  s3://$DEV_CDN_BUCKET/ \
  s3://$PROD_CDN_BUCKET/ \
  --exclude "*.json" \
  --exclude "*.ndjson"

# Verify count matches
DEV_CDN_COUNT=$(aws s3 ls s3://$DEV_CDN_BUCKET/ --recursive | wc -l)
PROD_CDN_COUNT=$(aws s3 ls s3://$PROD_CDN_BUCKET/ --recursive | wc -l)
echo "Dev CDN images: $DEV_CDN_COUNT, Prod CDN images: $PROD_CDN_COUNT"
```

## Step 5: ChromaDB Embeddings Migration

### 5.1 Understanding ChromaDB Storage

ChromaDB stores embeddings in two places:
1. **S3 Snapshots**: Full database snapshots (`snapshots/{collection}/{timestamp}/`)
2. **S3 Deltas**: Incremental changes (`lines/delta/{run_id}/`, `words/delta/{run_id}/`)
3. **EFS** (if used): Live ChromaDB database on EFS mount

### 5.2 Migration Strategy

**Option A: Copy Latest Snapshot (Recommended)**
- Copy the most recent snapshot from dev to prod
- This gives a clean starting point
- New deltas will be created as new receipts are processed

**Option B: Copy All Snapshots + Deltas**
- More complete but larger transfer
- May need to replay deltas in order

### 5.3 Migration Steps

```bash
# Find latest snapshot
LATEST_SNAPSHOT=$(aws s3 ls s3://$DEV_CHROMADB_BUCKET/snapshots/lines/ --recursive | sort | tail -1 | awk '{print $4}' | cut -d'/' -f1-3)

# Copy latest snapshots for both lines and words
aws s3 sync \
  s3://$DEV_CHROMADB_BUCKET/snapshots/lines/$LATEST_SNAPSHOT/ \
  s3://$PROD_CHROMADB_BUCKET/snapshots/lines/$LATEST_SNAPSHOT/

aws s3 sync \
  s3://$DEV_CHROMADB_BUCKET/snapshots/words/$LATEST_SNAPSHOT/ \
  s3://$PROD_CHROMADB_BUCKET/snapshots/words/$LATEST_SNAPSHOT/

# If EFS is used, we may need to restore from snapshot
# This would require running a compaction job in prod to restore from S3
```

**Note:** After migration, you may need to trigger a compaction in prod to load the snapshot into EFS (if EFS is used).

## Step 6: LayoutLM Model Migration

### 6.1 Find Latest Model

The LayoutLM model is stored in the training bucket under `runs/{run_id}/best/` or `runs/{run_id}/checkpoint-{step}/`.

```bash
# Find the latest/best model in dev
LATEST_RUN=$(aws s3 ls s3://$DEV_LAYOUTLM_BUCKET/runs/ | sort | tail -1 | awk '{print $2}' | tr -d '/')

# Check if there's a 'best' directory
aws s3 ls s3://$DEV_LAYOUTLM_BUCKET/runs/$LATEST_RUN/best/ || echo "No 'best' directory, checking checkpoints"

# If no 'best', find latest checkpoint
LATEST_CHECKPOINT=$(aws s3 ls s3://$DEV_LAYOUTLM_BUCKET/runs/$LATEST_RUN/ | grep checkpoint | sort | tail -1 | awk '{print $2}' | tr -d '/')
```

### 6.2 Copy Model Files

```bash
# Copy the entire run directory (includes model, tokenizer, config)
aws s3 sync \
  s3://$DEV_LAYOUTLM_BUCKET/runs/$LATEST_RUN/ \
  s3://$PROD_LAYOUTLM_BUCKET/runs/$LATEST_RUN/ \
  --exclude "*.log" \
  --exclude "*.txt"

# Verify model files exist
aws s3 ls s3://$PROD_LAYOUTLM_BUCKET/runs/$LATEST_RUN/best/ --recursive
# Should see: model.safetensors, tokenizer files, config.json, etc.
```

### 6.3 Update Inference Lambda Environment

After copying the model, the inference Lambda will automatically pick it up if using `auto_from_bucket_env="LAYOUTLM_TRAINING_BUCKET"`. Verify the environment variable is set correctly in prod.

## Step 7: Verification

### 7.1 Verify DynamoDB

```bash
# Count entities in both tables
python scripts/verify_migration.py \
  --dev-table $DEV_TABLE \
  --prod-table $PROD_TABLE
```

### 7.2 Verify S3 Buckets

```bash
# Compare object counts
echo "Raw images:"
aws s3 ls s3://$DEV_RAW_BUCKET/ --recursive | wc -l
aws s3 ls s3://$PROD_RAW_BUCKET/ --recursive | wc -l

echo "CDN images:"
aws s3 ls s3://$DEV_CDN_BUCKET/ --recursive | wc -l
aws s3 ls s3://$PROD_CDN_BUCKET/ --recursive | wc -l
```

### 7.3 Verify ChromaDB

```bash
# Check snapshot exists
aws s3 ls s3://$PROD_CHROMADB_BUCKET/snapshots/ --recursive | tail -5
```

### 7.4 Verify LayoutLM Model

```bash
# Check model files exist
aws s3 ls s3://$PROD_LAYOUTLM_BUCKET/runs/ --recursive | grep -E "(model.safetensors|config.json|tokenizer)"
```

## Step 8: Post-Migration Tasks

### 8.1 Update CloudFront Cache

If CDN images are served via CloudFront, invalidate the cache:

```bash
# Get CloudFront distribution ID from Pulumi outputs
DIST_ID=$(pulumi stack output cloudfront_distribution_id --stack prod)

# Invalidate all paths
aws cloudfront create-invalidation \
  --distribution-id $DIST_ID \
  --paths "/*"
```

### 8.2 Trigger ChromaDB Compaction (if using EFS)

If prod uses EFS for ChromaDB, trigger a compaction to restore from S3 snapshot:

```bash
# This depends on your compaction setup
# May need to manually trigger via Lambda or Step Function
```

### 8.3 Test LayoutLM Inference

Test that the inference API works with the migrated model:

```bash
# Call the inference API
curl https://api.tylernorlund.com/layoutlm_inference

# Should return inference results using the migrated model
```

## Migration Script Template

Create a comprehensive migration script: `scripts/migrate_all_dev_to_prod.py`

```python
#!/usr/bin/env python3
"""
Comprehensive migration script to copy all data from dev to prod.

Usage:
    python migrate_all_dev_to_prod.py --dry-run
    python migrate_all_dev_to_prod.py  # Actually migrate
"""

import argparse
import boto3
from pulumi import automation as auto

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Get resources from Pulumi
    work_dir = "infra"

    dev_stack = auto.create_or_select_stack(
        stack_name="tnorlund/portfolio/dev",
        work_dir=work_dir,
    )
    dev_outputs = dev_stack.outputs()

    prod_stack = auto.create_or_select_stack(
        stack_name="tnorlund/portfolio/prod",
        work_dir=work_dir,
    )
    prod_outputs = prod_stack.outputs()

    # Extract resource names
    resources = {
        "dev": {
            "table": dev_outputs["dynamodb_table_name"].value,
            "raw_bucket": dev_outputs["raw_bucket_name"].value,
            "cdn_bucket": dev_outputs["cdn_bucket_name"].value,
            "chromadb_bucket": dev_outputs["embedding_chromadb_bucket_name"].value,
            "layoutlm_bucket": dev_outputs.get("layoutlm_training_bucket", {}).value,
        },
        "prod": {
            "table": prod_outputs["dynamodb_table_name"].value,
            "raw_bucket": prod_outputs["raw_bucket_name"].value,
            "cdn_bucket": prod_outputs["cdn_bucket_name"].value,
            "chromadb_bucket": prod_outputs["embedding_chromadb_bucket_name"].value,
            "layoutlm_bucket": prod_outputs.get("layoutlm_training_bucket", {}).value,
        },
    }

    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE MIGRATION'}")
    print(f"Resources: {resources}")

    # Step 1: DynamoDB
    # Step 2: S3 Raw Images
    # Step 3: S3 CDN Images
    # Step 4: ChromaDB Embeddings
    # Step 5: LayoutLM Model

    if args.dry_run:
        print("\nDry run complete. Review above and run without --dry-run to migrate.")
    else:
        print("\nMigration complete!")

if __name__ == "__main__":
    main()
```

## Estimated Time and Costs

### Time Estimates
- **DynamoDB Migration**: 1-2 hours (depending on table size)
- **S3 Raw Images**: 30 minutes - 2 hours (depending on image count/size)
- **S3 CDN Images**: 30 minutes - 2 hours
- **ChromaDB Snapshots**: 30 minutes - 1 hour
- **LayoutLM Model**: 5-10 minutes
- **Verification**: 30 minutes
- **Total**: ~3-8 hours

### Cost Estimates
- **S3 Transfer**: ~$0.01-0.05 per GB (inter-region transfer if different regions)
- **DynamoDB Writes**: ~$1.25 per million writes
- **S3 Storage**: Minimal (already exists in dev)
- **Total**: ~$10-50 depending on data volume

## Rollback Plan

If something goes wrong:

1. **DynamoDB**: Delete migrated items (use script to identify by migration timestamp)
2. **S3**: Delete synced objects (use S3 lifecycle or manual deletion)
3. **ChromaDB**: Delete snapshots (can restore from dev if needed)
4. **LayoutLM**: Delete model files (can re-copy from dev)

## Notes

- **Bucket Name Updates**: DynamoDB records contain S3 bucket names in URLs. The migration script must update these references.
- **EFS ChromaDB**: If prod uses EFS for ChromaDB, you may need to restore from S3 snapshot after migration.
- **Incremental Migration**: Consider doing this in phases (DynamoDB first, then S3, then embeddings) to reduce risk.
- **Backup First**: Consider taking backups of prod before migration (though prod should be empty or minimal).

