# Deployment Asset Preservation

## Issue

When deploying the Next.js site to S3, the `aws s3 sync` command with the `--delete` flag was removing all files in the S3 bucket that weren't part of the Next.js build output. This included all receipt images uploaded by the `receipt_upload` package to the `/assets/` directory.

## Root Cause

The deployment workflow used:
```bash
aws s3 sync ../portfolio/out "s3://$BUCKET" \
  --delete \
  --cache-control "public, max-age=3600"
```

The `--delete` flag removes any files in the destination (S3) that don't exist in the source (Next.js build output).

## Solution

Added `--exclude "assets/*"` to preserve the assets directory:
```bash
aws s3 sync ../portfolio/out "s3://$BUCKET" \
  --delete \
  --exclude "assets/*" \
  --cache-control "public, max-age=3600"
```

## Alternative Solutions

1. **Remove --delete flag entirely**: Simpler but may leave old Next.js files
2. **Separate buckets**: Use different S3 buckets for static site vs uploaded assets
3. **Upload assets to build**: Include assets in Next.js build (not practical for dynamic uploads)

## Recovery Process

If assets are deleted, use the sync scripts:
```bash
# Get bucket names from Pulumi
DEV_BUCKET=$(pulumi stack output cdn_bucket_name --stack dev --cwd infra)
PROD_BUCKET=$(pulumi stack output cdn_bucket_name --stack prod --cwd infra)

# Quick recovery using environment variables
DEV_S3_BUCKET=$DEV_BUCKET PROD_S3_BUCKET=$PROD_BUCKET ./scripts/quick_sync_images.sh

# Or pass as arguments
./scripts/sync_images_dev_to_prod.sh $DEV_BUCKET $PROD_BUCKET
```

## Prevention

- Always use `--exclude "assets/*"` when syncing with `--delete`
- Consider enabling S3 versioning for recovery
- Add bucket protection (`protect=True` in Pulumi)
- Monitor deployments to ensure assets remain accessible