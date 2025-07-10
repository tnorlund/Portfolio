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

# Count images in dev (case-insensitive)
dev_count=$(aws s3 ls s3://${DEV_BUCKET}/assets/ --recursive | grep -iE '\.(jpg|jpeg|png|webp|avif|gif|svg)$' | wc -l | tr -d ' ')
# Ensure count is not empty
dev_count=${dev_count:-0}
echo -e "Dev bucket images: ${GREEN}${dev_count}${NC}"

# Count images in prod (case-insensitive)
prod_count=$(aws s3 ls s3://${PROD_BUCKET}/assets/ --recursive | grep -iE '\.(jpg|jpeg|png|webp|avif|gif|svg)$' | wc -l | tr -d ' ')
# Ensure count is not empty
prod_count=${prod_count:-0}
echo -e "Prod bucket images: ${GREEN}${prod_count}${NC}"

echo ""
# Calculate difference, ensuring non-negative
missing_estimate=$((dev_count - prod_count))
if [ $missing_estimate -lt 0 ]; then
    echo -e "${YELLOW}Note: Production has more files than development${NC}"
    echo -e "Dev: $dev_count, Prod: $prod_count (difference: $missing_estimate)"
else
    echo -e "${YELLOW}Estimated missing files: ~$missing_estimate${NC}"
fi
echo ""

# Step 2: Use aws s3 sync with dry run to see what would be copied
echo -e "${YELLOW}Step 2: Running dry-run to see what would be synced...${NC}"
echo ""

# Create a temp file to capture dry-run output
temp_file=$(mktemp)

# Run sync in dry-run mode and capture output (case-insensitive includes)
aws s3 sync s3://${DEV_BUCKET}/assets/ s3://${PROD_BUCKET}/assets/ \
    --exclude "*" \
    --include "*.jpg" --include "*.JPG" \
    --include "*.jpeg" --include "*.JPEG" \
    --include "*.png" --include "*.PNG" \
    --include "*.webp" --include "*.WEBP" \
    --include "*.avif" --include "*.AVIF" \
    --include "*.gif" --include "*.GIF" \
    --include "*.svg" --include "*.SVG" \
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
    --include "*.jpg" --include "*.JPG" \
    --include "*.jpeg" --include "*.JPEG" \
    --include "*.png" --include "*.PNG" \
    --include "*.webp" --include "*.WEBP" \
    --include "*.avif" --include "*.AVIF" \
    --include "*.gif" --include "*.GIF" \
    --include "*.svg" --include "*.SVG"

# Step 5: Verify counts
echo ""
echo -e "${YELLOW}Step 4: Verifying sync...${NC}"

# New count in prod (case-insensitive)
new_prod_count=$(aws s3 ls s3://${PROD_BUCKET}/assets/ --recursive | grep -iE '\.(jpg|jpeg|png|webp|avif|gif|svg)$' | wc -l | tr -d ' ')
new_prod_count=${new_prod_count:-0}
echo -e "Prod bucket images after sync: ${GREEN}${new_prod_count}${NC}"
echo -e "Images added: ${GREEN}$((new_prod_count - prod_count))${NC}"

# Step 6: Test a sample file (if any were synced)
if [ "$new_prod_count" -gt "$prod_count" ]; then
    echo ""
    echo -e "${YELLOW}Step 5: Testing asset accessibility...${NC}"
    
    # Get a sample file that was just synced (case-insensitive)
    sample_file=$(aws s3 ls s3://${PROD_BUCKET}/assets/ --recursive | grep -iE '\.(jpg|jpeg|png|webp|avif)$' | head -1 | awk '{print $4}')
    
    if [ -n "$sample_file" ]; then
        test_url="https://www.tylernorlund.com/$sample_file"
        echo "Testing: $test_url"
        
        response=$(curl -sI "$test_url")
        status_code=$(echo "$response" | head -n1 | cut -d' ' -f2)
        content_type=$(echo "$response" | grep -i "content-type:" | cut -d' ' -f2 | tr -d '\r')
        
        if [ "$status_code" = "200" ]; then
            echo -e "${GREEN}✓ Status: 200 OK${NC}"
            echo -e "  Content-Type: $content_type"
            
            if [[ "$content_type" == image/* ]]; then
                echo -e "${GREEN}✓ Assets are being served correctly!${NC}"
            else
                echo -e "${RED}✗ Content-Type issue detected${NC}"
                echo "CloudFront may need cache invalidation"
            fi
        else
            echo -e "${RED}✗ HTTP Status: $status_code${NC}"
        fi
    fi
fi

echo ""
echo "========================================="
echo -e "${GREEN}Sync complete!${NC}"
echo "========================================="