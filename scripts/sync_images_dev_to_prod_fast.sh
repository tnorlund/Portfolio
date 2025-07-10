#!/bin/bash

# S3 bucket names
DEV_BUCKET="sitebucket-ad92f1f"
PROD_BUCKET="sitebucket-778abc9"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================="
echo "Image Sync Script: Dev → Prod (Fast Version)"
echo "========================================="
echo ""

# Step 1: Get counts
echo -e "${YELLOW}Step 1: Analyzing buckets...${NC}"

# Count images in dev
dev_count=$(aws s3 ls s3://${DEV_BUCKET}/assets/ --recursive | grep -E '\.(jpg|jpeg|png|webp|avif|gif|svg)$' | wc -l | tr -d ' ')
echo -e "Dev bucket images: ${GREEN}${dev_count}${NC}"

# Count images in prod
prod_count=$(aws s3 ls s3://${PROD_BUCKET}/assets/ --recursive | grep -E '\.(jpg|jpeg|png|webp|avif|gif|svg)$' | wc -l | tr -d ' ')
echo -e "Prod bucket images: ${GREEN}${prod_count}${NC}"

echo ""
echo -e "${YELLOW}Estimated missing files: ~$((dev_count - prod_count))${NC}"
echo ""

# Step 2: Use aws s3 sync with dry run to see what would be copied
echo -e "${YELLOW}Step 2: Running dry-run to see what would be synced...${NC}"
echo ""

# Create a temp file to capture dry-run output
temp_file=$(mktemp)

# Run sync in dry-run mode and capture output
aws s3 sync s3://${DEV_BUCKET}/assets/ s3://${PROD_BUCKET}/assets/ \
    --exclude "*" \
    --include "*.jpg" --include "*.jpeg" --include "*.png" \
    --include "*.webp" --include "*.avif" --include "*.gif" --include "*.svg" \
    --dryrun | tee "$temp_file"

# Count files that would be copied
files_to_copy=$(grep -c "copy:" "$temp_file" || echo "0")

echo ""
echo -e "${YELLOW}Files that would be copied: ${files_to_copy}${NC}"
echo ""

# Clean up temp file
rm -f "$temp_file"

# Step 3: Ask for confirmation
if [ "$files_to_copy" -eq "0" ]; then
    echo -e "${GREEN}All image files from dev already exist in prod!${NC}"
    exit 0
fi

echo -e "${YELLOW}Do you want to sync these ${files_to_copy} files from dev to prod? (y/n)${NC}"
read -r response

if [[ ! "$response" =~ ^[Yy]$ ]]; then
    echo "Operation cancelled."
    exit 0
fi

# Step 4: Perform the actual sync
echo ""
echo -e "${YELLOW}Step 3: Syncing files...${NC}"
echo ""

aws s3 sync s3://${DEV_BUCKET}/assets/ s3://${PROD_BUCKET}/assets/ \
    --exclude "*" \
    --include "*.jpg" --include "*.jpeg" --include "*.png" \
    --include "*.webp" --include "*.avif" --include "*.gif" --include "*.svg"

# Step 5: Verify counts
echo ""
echo -e "${YELLOW}Step 4: Verifying sync...${NC}"

# New count in prod
new_prod_count=$(aws s3 ls s3://${PROD_BUCKET}/assets/ --recursive | grep -E '\.(jpg|jpeg|png|webp|avif|gif|svg)$' | wc -l | tr -d ' ')
echo -e "Prod bucket images after sync: ${GREEN}${new_prod_count}${NC}"
echo -e "Images added: ${GREEN}$((new_prod_count - prod_count))${NC}"

# Step 6: Test a specific WebP file
echo ""
echo -e "${YELLOW}Step 5: Testing the specific WebP file you mentioned...${NC}"
test_url="https://www.tylernorlund.com/assets/80d76b93-9e71-42ee-a650-d176895d965a_RECEIPT_00001.webp"

echo "Checking: $test_url"
response=$(curl -sI "$test_url")
status_code=$(echo "$response" | head -n1 | cut -d' ' -f2)
content_type=$(echo "$response" | grep -i "content-type:" | cut -d' ' -f2 | tr -d '\r')
content_length=$(echo "$response" | grep -i "content-length:" | cut -d' ' -f2 | tr -d '\r')

if [ "$status_code" = "200" ]; then
    echo -e "${GREEN}✓ Status: 200 OK${NC}"
    echo -e "  Content-Type: $content_type"
    echo -e "  Content-Length: $content_length bytes"
    
    if [[ "$content_type" == image/* ]]; then
        echo -e "${GREEN}✓ File is being served correctly!${NC}"
    else
        echo -e "${RED}✗ Content-Type issue: Expected image/webp but got $content_type${NC}"
        echo ""
        echo "You may need to:"
        echo "1. Wait a few minutes for CloudFront cache to update"
        echo "2. Create a CloudFront invalidation: aws cloudfront create-invalidation --distribution-id E3RH4PZ3LNS1SL --paths '/assets/*'"
    fi
else
    echo -e "${RED}✗ HTTP Status: $status_code${NC}"
fi

echo ""
echo "========================================="
echo -e "${GREEN}Sync complete!${NC}"
echo "========================================="