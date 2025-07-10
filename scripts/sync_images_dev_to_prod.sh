#!/bin/bash

# S3 bucket names - must be set as environment variables or passed as arguments
DEV_BUCKET="${DEV_S3_BUCKET:-}"
PROD_BUCKET="${PROD_S3_BUCKET:-}"

# Check if bucket names are provided
if [ -z "$DEV_BUCKET" ] || [ -z "$PROD_BUCKET" ]; then
    echo "Error: S3 bucket names must be set via environment variables or arguments"
    echo "Usage: DEV_S3_BUCKET=<dev-bucket> PROD_S3_BUCKET=<prod-bucket> $0"
    echo "   or: $0 <dev-bucket> <prod-bucket>"
    exit 1
fi

# Allow bucket names to be passed as arguments
if [ $# -eq 2 ]; then
    DEV_BUCKET="$1"
    PROD_BUCKET="$2"
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================="
echo "Image Sync Script: Dev → Prod"
echo "========================================="
echo ""

# Step 1: List all image files in dev bucket
echo -e "${YELLOW}Step 1: Listing image files in DEV bucket...${NC}"
echo "Dev bucket: s3://${DEV_BUCKET}/assets/"
echo ""

# Get list of image files from dev (case-insensitive)
dev_images=$(aws s3 ls s3://${DEV_BUCKET}/assets/ --recursive | grep -iE '\.(jpg|jpeg|png|webp|avif|gif|svg)$' | awk '{print $4}')

if [ -z "$dev_images" ]; then
    echo -e "${RED}No image files found in dev bucket!${NC}"
    exit 1
fi

# Count dev images (handle empty case)
dev_count=$(echo "$dev_images" | wc -l | tr -d ' ')
dev_count=${dev_count:-0}
echo -e "${GREEN}Found ${dev_count} image files in dev bucket${NC}"
echo ""

# Step 2: Check which images are missing in prod
echo -e "${YELLOW}Step 2: Checking which images are missing in PROD bucket...${NC}"
echo "Prod bucket: s3://${PROD_BUCKET}/assets/"
echo ""

missing_files=()
existing_files=()

while IFS= read -r file; do
    # Check if file exists in prod
    if aws s3api head-object --bucket "${PROD_BUCKET}" --key "${file}" >/dev/null 2>&1; then
        existing_files+=("$file")
    else
        missing_files+=("$file")
    fi
done <<< "$dev_images"

# Display results
echo -e "${GREEN}Existing files in prod: ${#existing_files[@]}${NC}"
echo -e "${RED}Missing files in prod: ${#missing_files[@]}${NC}"
echo ""

# Step 3: Show missing files
if [ ${#missing_files[@]} -eq 0 ]; then
    echo -e "${GREEN}All image files from dev already exist in prod!${NC}"
    exit 0
fi

echo -e "${YELLOW}Missing files:${NC}"
for file in "${missing_files[@]}"; do
    echo "  - $file"
done | head -20

if [ ${#missing_files[@]} -gt 20 ]; then
    echo "  ... and $((${#missing_files[@]} - 20)) more files"
fi
echo ""

# Step 4: Ask for confirmation
echo -e "${YELLOW}Do you want to copy these ${#missing_files[@]} missing files from dev to prod? (y/n)${NC}"
read -r response

if [[ ! "$response" =~ ^[Yy]$ ]]; then
    echo "Operation cancelled."
    exit 0
fi

# Step 5: Copy missing files
echo ""
echo -e "${YELLOW}Step 5: Copying missing files...${NC}"

success_count=0
failed_count=0

for file in "${missing_files[@]}"; do
    echo -n "Copying $file... "
    if aws s3 cp "s3://${DEV_BUCKET}/${file}" "s3://${PROD_BUCKET}/${file}" >/dev/null 2>&1; then
        echo -e "${GREEN}✓${NC}"
        ((success_count++))
    else
        echo -e "${RED}✗${NC}"
        ((failed_count++))
    fi
done

# Step 6: Summary
echo ""
echo "========================================="
echo -e "${GREEN}Summary:${NC}"
echo -e "  Files successfully copied: ${GREEN}${success_count}${NC}"
echo -e "  Files failed to copy: ${RED}${failed_count}${NC}"
echo -e "  Files already in prod: ${GREEN}${#existing_files[@]}${NC}"
echo "========================================="

# Step 7: Verify a sample file
if [ ${success_count} -gt 0 ]; then
    echo ""
    echo -e "${YELLOW}Verifying a sample file...${NC}"
    sample_file="${missing_files[0]}"
    
    # Extract just the filename for the URL
    filename=$(basename "$sample_file")
    url="https://www.tylernorlund.com/${sample_file}"
    
    echo "Checking: $url"
    content_type=$(curl -sI "$url" | grep -i "content-type:" | cut -d' ' -f2 | tr -d '\r')
    
    if [[ "$content_type" == image/* ]]; then
        echo -e "${GREEN}✓ Content-Type is correct: $content_type${NC}"
    else
        echo -e "${RED}✗ Content-Type issue detected: $content_type${NC}"
        echo "The CloudFront function might need to be updated or cache might need invalidation."
    fi
fi