#!/bin/bash
# Script to fix Content-Type metadata for existing images in S3

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}S3 Image Metadata Fix Script${NC}"
echo "============================"

# Function to get content type from file extension
get_content_type() {
    case "${1##*.}" in
        avif) echo "image/avif" ;;
        webp) echo "image/webp" ;;
        jpg|jpeg) echo "image/jpeg" ;;
        png) echo "image/png" ;;
        *) echo "application/octet-stream" ;;
    esac
}

# Find the S3 bucket
echo -e "\n${GREEN}Finding S3 buckets...${NC}"
BUCKETS=$(aws s3api list-buckets --query "Buckets[?contains(Name, 'sitebucket')].Name" --output text)

if [ -z "$BUCKETS" ]; then
    echo -e "${RED}No site buckets found${NC}"
    echo "Looking for all buckets..."
    aws s3api list-buckets --query "Buckets[].Name" --output table
    echo -e "\n${YELLOW}Please specify the bucket name:${NC}"
    read -r BUCKET_NAME
else
    BUCKET_NAME=$(echo $BUCKETS | awk '{print $1}')
    echo -e "${GREEN}Found bucket: $BUCKET_NAME${NC}"
fi

# List all objects in assets/ directory
echo -e "\n${YELLOW}Scanning images in s3://$BUCKET_NAME/assets/...${NC}"
OBJECTS=$(aws s3api list-objects-v2 --bucket "$BUCKET_NAME" --prefix "assets/" --query "Contents[?ends_with(Key, '.avif') || ends_with(Key, '.webp') || ends_with(Key, '.jpg') || ends_with(Key, '.jpeg') || ends_with(Key, '.png')].Key" --output text)

if [ -z "$OBJECTS" ]; then
    echo -e "${RED}No image files found in assets/ directory${NC}"
    exit 1
fi

# Count total files
TOTAL=$(echo "$OBJECTS" | wc -w)
echo -e "${GREEN}Found $TOTAL image files to process${NC}"

# Process each file
FIXED=0
FAILED=0

for KEY in $OBJECTS; do
    # Get current metadata
    CURRENT_METADATA=$(aws s3api head-object --bucket "$BUCKET_NAME" --key "$KEY" --query "ContentType" --output text 2>/dev/null)
    EXPECTED_TYPE=$(get_content_type "$KEY")

    if [ "$CURRENT_METADATA" != "$EXPECTED_TYPE" ]; then
        echo -n "Fixing $KEY (current: $CURRENT_METADATA, expected: $EXPECTED_TYPE)... "

        # Copy object to itself with correct metadata
        if aws s3api copy-object \
            --bucket "$BUCKET_NAME" \
            --copy-source "$BUCKET_NAME/$KEY" \
            --key "$KEY" \
            --metadata-directive REPLACE \
            --content-type "$EXPECTED_TYPE" \
            --cache-control "public, max-age=2592000" \
            --acl bucket-owner-full-control \
            >/dev/null 2>&1; then
            echo -e "${GREEN}✓${NC}"
            ((FIXED++))
        else
            echo -e "${RED}✗${NC}"
            ((FAILED++))
        fi
    else
        echo "Skipping $KEY - already has correct content-type: $CURRENT_METADATA"
    fi
done

echo -e "\n${GREEN}Summary:${NC}"
echo "- Total files: $TOTAL"
echo "- Fixed: $FIXED"
echo "- Failed: $FAILED"
echo "- Already correct: $((TOTAL - FIXED - FAILED))"

if [ $FIXED -gt 0 ]; then
    echo -e "\n${YELLOW}Important: You must now invalidate CloudFront cache!${NC}"
    echo "Run: ./scripts/invalidate-cloudfront-images.sh"
fi
