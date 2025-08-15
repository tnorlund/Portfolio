#!/bin/bash
# Pull base images from ECR to use as Docker build cache
# Run this before 'pulumi up' for faster builds

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}Docker Cache Preparation Script${NC}"
echo "================================="

# Get AWS account info
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=${AWS_REGION:-us-east-1}
ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
STACK=${PULUMI_STACK:-$(pulumi stack --show-name 2>/dev/null || echo "dev")}

# Login to ECR
echo -e "${YELLOW}Logging into ECR...${NC}"
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR_REGISTRY

# Function to pull image if exists
pull_for_cache() {
    local image=$1
    echo -e "${YELLOW}Pulling $image for cache...${NC}"
    
    # Try to pull the image
    if docker pull "$image" 2>/dev/null; then
        echo -e "${GREEN}✓ Pulled $image${NC}"
        # Tag it for BuildKit to use as cache
        docker tag "$image" "$image-cache" 2>/dev/null || true
        return 0
    else
        echo -e "${YELLOW}✗ Image not found (will build fresh): $image${NC}"
        return 1
    fi
}

# Determine tag based on USE_STATIC_BASE_IMAGE
if [ "$USE_STATIC_BASE_IMAGE" = "true" ]; then
    TAG="stable"
    echo -e "${GREEN}Using stable tag for development${NC}"
else
    # Try to get git commit or use latest
    TAG=$(git rev-parse --short HEAD 2>/dev/null || echo "latest")
    if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
        TAG="${TAG}-dirty"
    fi
    echo -e "${GREEN}Using content-based tag: $TAG${NC}"
fi

echo ""
echo -e "${GREEN}Pulling base images for cache...${NC}"
echo "================================="

# Pull base images with the appropriate tag
BASE_IMAGES=(
    "${ECR_REGISTRY}/base-receipt-dynamo-${STACK}:${TAG}"
    "${ECR_REGISTRY}/base-receipt-label-${STACK}:${TAG}"
    "${ECR_REGISTRY}/base-receipt-dynamo-${STACK}:latest"
    "${ECR_REGISTRY}/base-receipt-label-${STACK}:latest"
)

for image in "${BASE_IMAGES[@]}"; do
    pull_for_cache "$image" || true
done

echo ""
echo -e "${GREEN}Cache preparation complete!${NC}"
echo ""
echo -e "${YELLOW}IMPORTANT:${NC} Due to Pulumi Docker provider limitations, cache_from is disabled."
echo "           However, the pulled images are now in your local Docker cache"
echo "           and will be used automatically by BuildKit!"
echo ""
echo -e "${YELLOW}Now run:${NC} pulumi up"
echo ""
echo -e "${YELLOW}Note:${NC} Docker will use these images as cache during the build"
echo "      Look for 'CACHED' in the build output to confirm cache hits"