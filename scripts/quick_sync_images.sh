#!/bin/bash

# Quick sync script without dry-run preview
# S3 bucket names
DEV_BUCKET="sitebucket-ad92f1f"
PROD_BUCKET="sitebucket-778abc9"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================="
echo "Quick Image Sync: Dev → Prod"
echo "========================================="
echo ""

# Count images
echo -e "${YELLOW}Analyzing buckets...${NC}"
dev_count=$(aws s3 ls s3://${DEV_BUCKET}/assets/ --recursive | grep -E '\.(jpg|jpeg|png|webp|avif|gif|svg)$' | wc -l | tr -d ' ')
prod_count=$(aws s3 ls s3://${PROD_BUCKET}/assets/ --recursive | grep -E '\.(jpg|jpeg|png|webp|avif|gif|svg)$' | wc -l | tr -d ' ')

echo -e "Dev images: ${GREEN}${dev_count}${NC}"
echo -e "Prod images: ${GREEN}${prod_count}${NC}"
echo -e "Estimated missing: ~$((dev_count - prod_count))"
echo ""

# Perform sync directly
echo -e "${YELLOW}Starting sync (this may take several minutes)...${NC}"
echo ""

aws s3 sync s3://${DEV_BUCKET}/assets/ s3://${PROD_BUCKET}/assets/ \
    --exclude "*" \
    --include "*.jpg" --include "*.jpeg" --include "*.png" \
    --include "*.webp" --include "*.avif" --include "*.gif" --include "*.svg" \
    --no-progress | head -100

echo ""
echo -e "${GREEN}Sync complete!${NC}"

# Test the specific file
echo ""
echo -e "${YELLOW}Testing your specific WebP file...${NC}"
url="https://www.tylernorlund.com/assets/80d76b93-9e71-42ee-a650-d176895d965a_RECEIPT_00001.webp"
response=$(curl -sI "$url" | head -20)
echo "$response"