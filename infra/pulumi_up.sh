#!/bin/bash
# Wrapper script for 'pulumi up' that ensures optimal Docker caching

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
echo -e "${GREEN}       Pulumi Up with Docker Optimizations${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════${NC}"

# Ensure we're in the infra directory
if [[ ! -f "Pulumi.yaml" ]]; then
    echo -e "${YELLOW}Warning: Not in infra directory, changing to it...${NC}"
    cd "$(dirname "$0")"
fi

# Enable Docker BuildKit
export DOCKER_BUILDKIT=1
echo -e "${GREEN}✓ Docker BuildKit enabled${NC}"

# Check which stack is active
CURRENT_STACK=$(pulumi stack --show-name 2>/dev/null || echo "none")
echo -e "${GREEN}✓ Current stack: ${CURRENT_STACK}${NC}"

# Read configuration from Pulumi
USE_STATIC=$(pulumi config get portfolio:use-static-base-image 2>/dev/null || echo "false")
if [[ "$USE_STATIC" == "true" ]]; then
    echo -e "${GREEN}✓ Using stable base image tags (fast dev mode)${NC}"
else
    echo -e "${GREEN}✓ Using content-based tags (production mode)${NC}"
fi

# Optional: Pre-pull images for even faster builds
if [[ "$1" == "--pull-cache" || "$1" == "-p" ]]; then
    echo -e "${YELLOW}Pre-pulling cache images...${NC}"
    if [[ -f "./pull_base_images.sh" ]]; then
        ./pull_base_images.sh
    fi
fi

echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
echo -e "${GREEN}Starting Pulumi deployment...${NC}"
echo ""

# Run pulumi up with all remaining arguments
pulumi up "$@"