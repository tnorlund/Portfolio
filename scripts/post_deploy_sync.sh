#!/bin/bash
# Run this after pulumi up to ensure assets are in prod

DEV_BUCKET="sitebucket-ad92f1f"
PROD_BUCKET="sitebucket-778abc9"

echo "Post-deployment asset sync..."
aws s3 sync s3://${DEV_BUCKET}/assets/ s3://${PROD_BUCKET}/assets/ \
    --exclude "*" \
    --include "*.jpg" --include "*.jpeg" --include "*.png" \
    --include "*.webp" --include "*.avif" --include "*.gif" --include "*.svg"

echo "Asset sync complete!"