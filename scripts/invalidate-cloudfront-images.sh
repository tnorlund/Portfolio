#!/bin/bash
# Script to invalidate CloudFront cache for image files with incorrect Content-Type headers

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}CloudFront Cache Invalidation Script${NC}"
echo "======================================"

# Get CloudFront distribution ID
echo -e "\n${GREEN}Finding CloudFront distributions...${NC}"
DISTRIBUTIONS=$(aws cloudfront list-distributions --query "DistributionList.Items[?contains(Aliases.Items[0], 'tylernorlund.com') || contains(DomainName, 'tylernorlund.com')].{Id:Id,DomainName:DomainName,Aliases:Aliases.Items}" --output json)

echo "$DISTRIBUTIONS" | jq -r '.[] | "ID: \(.Id) - Domain: \(.DomainName) - Aliases: \(.Aliases | join(", "))"'

# For production (www.tylernorlund.com)
PROD_DIST_ID=$(echo "$DISTRIBUTIONS" | jq -r '.[] | select(.Aliases[]? | contains("www.tylernorlund.com")) | .Id')
DEV_DIST_ID=$(echo "$DISTRIBUTIONS" | jq -r '.[] | select(.Aliases[]? | contains("dev.tylernorlund.com")) | .Id')

if [ -z "$PROD_DIST_ID" ]; then
    echo -e "${RED}Could not find production CloudFront distribution${NC}"
    exit 1
fi

echo -e "\n${GREEN}Production Distribution ID: $PROD_DIST_ID${NC}"
[ ! -z "$DEV_DIST_ID" ] && echo -e "${GREEN}Development Distribution ID: $DEV_DIST_ID${NC}"

# Create invalidation for all image files
echo -e "\n${YELLOW}Creating invalidation for all image files in /assets/...${NC}"

# Invalidate production
echo -e "${GREEN}Invalidating production distribution...${NC}"
PROD_INVALIDATION=$(aws cloudfront create-invalidation \
    --distribution-id "$PROD_DIST_ID" \
    --paths "/assets/*" \
    --query "Invalidation.{Id:Id,Status:Status,CreateTime:CreateTime}" \
    --output json)

echo "Production Invalidation ID: $(echo $PROD_INVALIDATION | jq -r '.Id')"
echo "Status: $(echo $PROD_INVALIDATION | jq -r '.Status')"

# Invalidate dev if it exists
if [ ! -z "$DEV_DIST_ID" ]; then
    echo -e "\n${GREEN}Invalidating development distribution...${NC}"
    DEV_INVALIDATION=$(aws cloudfront create-invalidation \
        --distribution-id "$DEV_DIST_ID" \
        --paths "/assets/*" \
        --query "Invalidation.{Id:Id,Status:Status,CreateTime:CreateTime}" \
        --output json)

    echo "Dev Invalidation ID: $(echo $DEV_INVALIDATION | jq -r '.Id')"
    echo "Status: $(echo $DEV_INVALIDATION | jq -r '.Status')"
fi

echo -e "\n${YELLOW}Invalidation created!${NC}"
echo "CloudFront will now serve fresh copies of all images with correct Content-Type headers."
echo "Note: Invalidation typically takes 10-15 minutes to complete globally."

# Optional: Check a sample image to verify the fix will work
echo -e "\n${YELLOW}Testing a sample image after invalidation completes...${NC}"
echo "You can test with:"
echo "curl -I https://www.tylernorlund.com/assets/0a345fdd-ecc3-4577-b3d3-869140451f43.avif | grep content-type"
