#!/bin/bash
# Script to fix missing receipt images by processing them from raw bucket to CDN formats

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Receipt Image Fix Script${NC}"
echo -e "${BLUE}========================================${NC}"

# Check if we're in the right directory
if [ ! -d "receipt_upload" ] || [ ! -f "receipt_upload/pyproject.toml" ]; then
    echo -e "${RED}Error: This script must be run from the project root directory${NC}"
    echo "Please cd to /Users/tnorlund/GitHub/Portfolio-phase2-batch1"
    exit 1
fi

# Step 1: Install dependencies
echo -e "\n${YELLOW}Step 1: Installing dependencies...${NC}"
pip install -q -r scripts/requirements-process-images.txt
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Dependencies installed${NC}"
else
    echo -e "${RED}✗ Failed to install dependencies${NC}"
    exit 1
fi

# Step 2: Test with a dry run first
echo -e "\n${YELLOW}Step 2: Running dry run to check what will be processed...${NC}"
python scripts/process-raw-images-to-cdn.py --dry-run --limit 5

echo -e "\n${YELLOW}Do you want to proceed with processing all images? (y/n)${NC}"
read -r response
if [[ ! "$response" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

# Step 3: Process all receipt images
echo -e "\n${YELLOW}Step 3: Processing receipt images...${NC}"
echo "This may take several minutes depending on the number of images..."
python scripts/process-raw-images-to-cdn.py

if [ $? -ne 0 ]; then
    echo -e "${RED}Error during image processing${NC}"
    exit 1
fi

# Step 4: Invalidate CloudFront cache
echo -e "\n${YELLOW}Step 4: Invalidating CloudFront cache...${NC}"
./scripts/invalidate-cloudfront-images.sh

if [ $? -eq 0 ]; then
    echo -e "\n${GREEN}========================================${NC}"
    echo -e "${GREEN}✓ Image fix completed successfully!${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo -e "\n${YELLOW}What happens next:${NC}"
    echo "1. CloudFront invalidation takes 10-15 minutes to propagate"
    echo "2. After that, your receipt images should load correctly"
    echo "3. Test at: https://www.tylernorlund.com/receipt"
    echo -e "\n${YELLOW}Test a specific image:${NC}"
    echo "curl -I https://www.tylernorlund.com/assets/2c9b770c-9407-4cdc-b0eb-3a5b27f0af15_RECEIPT_00001.jpg | grep content-type"
else
    echo -e "${RED}CloudFront invalidation failed${NC}"
    echo "You may need to run: ./scripts/invalidate-cloudfront-images.sh manually"
fi
