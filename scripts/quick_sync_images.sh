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

# Calculate difference, ensuring non-negative
missing_estimate=$((dev_count - prod_count))
if [ $missing_estimate -lt 0 ]; then
    echo -e "${YELLOW}Note: Production has more files than development${NC}"
else
    echo -e "Estimated missing: ~$missing_estimate"
fi
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

# Test a sample file
echo ""
echo -e "${YELLOW}Verifying sync completed successfully...${NC}"

# Get current count and check if any files were synced
final_count=$(aws s3 ls s3://${PROD_BUCKET}/assets/ --recursive | grep -E '\.(jpg|jpeg|png|webp|avif|gif|svg)$' | wc -l | tr -d ' ')
synced_count=$((final_count - prod_count))

if [ $synced_count -gt 0 ]; then
    echo -e "${GREEN}Successfully synced $synced_count files${NC}"
    
    # Test a sample file
    sample_file=$(aws s3 ls s3://${PROD_BUCKET}/assets/ --recursive | grep -E '\.(jpg|jpeg|png|webp|avif)$' | head -1 | awk '{print $4}')
    if [ -n "$sample_file" ]; then
        url="https://www.tylernorlund.com/$sample_file"
        echo "Testing sample: $url"
        status=$(curl -sI "$url" | head -1 | cut -d' ' -f2)
        if [ "$status" = "200" ]; then
            echo -e "${GREEN}✓ Assets accessible${NC}"
        else
            echo -e "${RED}✗ HTTP $status - check CloudFront${NC}"
        fi
    fi
else
    echo -e "${YELLOW}No files needed to be synced${NC}"
fi