#!/bin/bash
# Build optimization script for Lambda containers with caching
# This script pulls existing images for cache and enables BuildKit

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}Docker Build Optimization Script${NC}"
echo "================================="

# Enable Docker BuildKit
export DOCKER_BUILDKIT=1
export BUILDKIT_PROGRESS=plain
echo -e "${YELLOW}✓ BuildKit enabled${NC}"

# For local development, use static base image tags
if [ "$1" == "--dev" ] || [ "$1" == "--development" ]; then
    export USE_STATIC_BASE_IMAGE=true
    echo -e "${YELLOW}✓ Development mode: Using static base image tags${NC}"
fi

# Get AWS account info
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=${AWS_REGION:-us-east-1}
ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
STACK=${PULUMI_STACK:-$(pulumi stack --show-name 2>/dev/null || echo "dev")}

echo -e "${GREEN}Configuration:${NC}"
echo "  Account: $ACCOUNT_ID"
echo "  Region: $REGION"
echo "  Stack: $STACK"
echo ""

# Login to ECR
echo -e "${YELLOW}Logging into ECR...${NC}"
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR_REGISTRY

# Function to pull image if exists
pull_if_exists() {
    local image=$1
    echo -e "${YELLOW}Attempting to pull: $image${NC}"
    if docker pull "$image" 2>/dev/null; then
        echo -e "${GREEN}✓ Pulled $image for cache${NC}"
        return 0
    else
        echo -e "${YELLOW}✗ Image not found (will build fresh): $image${NC}"
        return 1
    fi
}

# Pull base images for caching
echo -e "${GREEN}Pulling base images for cache...${NC}"
echo "================================="

# Base images
BASE_IMAGES=(
    "${ECR_REGISTRY}/base-receipt-dynamo-${STACK}:stable"
    "${ECR_REGISTRY}/base-receipt-dynamo-${STACK}:latest"
    "${ECR_REGISTRY}/base-receipt-label-${STACK}:stable"
    "${ECR_REGISTRY}/base-receipt-label-${STACK}:latest"
)

for image in "${BASE_IMAGES[@]}"; do
    pull_if_exists "$image" || true
done

# Lambda images
echo -e "${GREEN}Pulling Lambda images for cache...${NC}"
echo "================================="

LAMBDA_IMAGES=(
    "${ECR_REGISTRY}/line-embedding-poll-${STACK}:latest"
    "${ECR_REGISTRY}/line-embedding-compact-${STACK}:latest"
    "${ECR_REGISTRY}/line-embedding-line-poll-${STACK}:latest"
)

for image in "${LAMBDA_IMAGES[@]}"; do
    pull_if_exists "$image" || true
done

echo ""
echo -e "${GREEN}Cache preparation complete!${NC}"
echo "================================="
echo ""
echo -e "${GREEN}Now run:${NC}"
echo "  pulumi up"
echo ""
echo -e "${YELLOW}Tips for faster builds:${NC}"
echo "  • Use --dev flag for development (static base tags)"
echo "  • Keep Docker daemon running between builds"
echo "  • Run 'docker system prune' if disk space is low"
echo ""