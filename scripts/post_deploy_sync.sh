#!/bin/bash
# Run this after pulumi up to ensure assets are in prod

# S3 bucket names - must be set as environment variables or passed as arguments
DEV_BUCKET="${DEV_S3_BUCKET:-}"
PROD_BUCKET="${PROD_S3_BUCKET:-}"

# Allow bucket names to be passed as arguments
if [ $# -eq 2 ]; then
    DEV_BUCKET="$1"
    PROD_BUCKET="$2"
fi

# Check if bucket names are provided
if [ -z "$DEV_BUCKET" ] || [ -z "$PROD_BUCKET" ]; then
    echo "Error: S3 bucket names must be set via environment variables or arguments"
    echo "Usage: DEV_S3_BUCKET=<dev-bucket> PROD_S3_BUCKET=<prod-bucket> $0"
    echo "   or: $0 <dev-bucket> <prod-bucket>"
    exit 1
fi

echo "Post-deployment asset sync..."
aws s3 sync s3://${DEV_BUCKET}/assets/ s3://${PROD_BUCKET}/assets/ \
    --exclude "*" \
    --include "*.jpg" --include "*.JPG" \
    --include "*.jpeg" --include "*.JPEG" \
    --include "*.png" --include "*.PNG" \
    --include "*.webp" --include "*.WEBP" \
    --include "*.avif" --include "*.AVIF" \
    --include "*.gif" --include "*.GIF" \
    --include "*.svg" --include "*.SVG"

echo "Asset sync complete!"