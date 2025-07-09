#!/bin/bash
# Script to verify receipt images are properly served with correct content-type

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}Receipt Image Verification Script${NC}"
echo "=================================="

# Test URLs - add more as needed
TEST_URLS=(
    "https://www.tylernorlund.com/assets/2c9b770c-9407-4cdc-b0eb-3a5b27f0af15_RECEIPT_00001.jpg"
    "https://www.tylernorlund.com/assets/2c9b770c-9407-4cdc-b0eb-3a5b27f0af15_RECEIPT_00001.webp"
    "https://www.tylernorlund.com/assets/2c9b770c-9407-4cdc-b0eb-3a5b27f0af15_RECEIPT_00001.avif"
    "https://www.tylernorlund.com/assets/0a345fdd-ecc3-4577-b3d3-869140451f43.avif"
)

# Function to check a single URL
check_url() {
    local url=$1
    local filename=$(basename "$url")
    local expected_type=""

    # Determine expected content-type based on extension
    case "${filename##*.}" in
        jpg|jpeg) expected_type="image/jpeg" ;;
        webp) expected_type="image/webp" ;;
        avif) expected_type="image/avif" ;;
        png) expected_type="image/png" ;;
    esac

    echo -e "\nChecking: ${BLUE}$filename${NC}"
    echo "URL: $url"

    # Get headers
    response=$(curl -s -I "$url")
    http_code=$(echo "$response" | head -n 1 | awk '{print $2}')
    content_type=$(echo "$response" | grep -i "content-type:" | awk '{print $2}' | tr -d '\r')

    # Check HTTP status
    if [ "$http_code" = "200" ]; then
        echo -e "Status: ${GREEN}200 OK${NC}"
    else
        echo -e "Status: ${RED}$http_code${NC}"
        return 1
    fi

    # Check content-type
    if [ "$content_type" = "$expected_type" ]; then
        echo -e "Content-Type: ${GREEN}$content_type ✓${NC}"
    else
        echo -e "Content-Type: ${RED}$content_type${NC} (expected: $expected_type)"
        return 1
    fi

    return 0
}

# Run checks
echo -e "\n${YELLOW}Checking receipt images...${NC}"

success_count=0
fail_count=0

for url in "${TEST_URLS[@]}"; do
    if check_url "$url"; then
        ((success_count++))
    else
        ((fail_count++))
    fi
done

# Summary
echo -e "\n${BLUE}========================================${NC}"
echo -e "${BLUE}Summary:${NC}"
echo -e "  ${GREEN}Successful: $success_count${NC}"
echo -e "  ${RED}Failed: $fail_count${NC}"

if [ $fail_count -eq 0 ]; then
    echo -e "\n${GREEN}✓ All images are properly configured!${NC}"
else
    echo -e "\n${YELLOW}Some images are still not working.${NC}"
    echo "Possible reasons:"
    echo "1. CloudFront invalidation still in progress (takes 10-15 minutes)"
    echo "2. Images not yet processed from raw bucket"
    echo "3. CloudFront function not yet deployed"
    echo -e "\n${YELLOW}To fix:${NC}"
    echo "1. Run: ./scripts/fix-missing-receipt-images.sh"
    echo "2. Wait for CloudFront invalidation to complete"
    echo "3. Deploy PR #179 for CloudFront Content-Type fix"
fi

# Check if we can get an actual image
echo -e "\n${YELLOW}Testing actual image download...${NC}"
test_url="${TEST_URLS[0]}"
if curl -s "$test_url" | file - | grep -q "JPEG image data"; then
    echo -e "${GREEN}✓ Successfully downloaded and verified JPEG image${NC}"
else
    echo -e "${RED}✗ Failed to download valid JPEG image${NC}"
fi
