#!/bin/bash
# Docker cache warmer - Pre-pulls and builds base layers for faster Lambda builds
# Run this script before `pulumi up` for optimal caching

set -e

# Configuration
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/../.." && pwd )"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
echo -e "${GREEN}       Docker Cache Warmer for Lambdas${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════${NC}"

# Enable BuildKit
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

# Function to build base layers only
warm_lambda_cache() {
    local lambda_name=$1
    local dockerfile_path=$2
    
    echo -e "${YELLOW}Warming cache for ${lambda_name}...${NC}"
    
    # Build only the base layers (stop before copying handler)
    docker build \
        --target base 2>/dev/null || \
    docker build \
        --cache-from type=inline \
        --build-arg BUILDKIT_INLINE_CACHE=1 \
        --build-arg PYTHON_VERSION=3.12 \
        -f "$dockerfile_path" \
        "$PROJECT_ROOT" \
        --no-cache=false \
        2>&1 | grep -E "(CACHED|Downloading|Installing)" || true
        
    echo -e "${GREEN}✓ Cache warmed for ${lambda_name}${NC}"
}

# Pre-compile Python dependencies
echo -e "${BLUE}Pre-compiling Python dependencies...${NC}"
docker run --rm \
    -v /tmp/pip-cache:/root/.cache/pip \
    public.ecr.aws/lambda/python:3.12-arm64 \
    pip install --cache-dir /root/.cache/pip \
        chromadb>=0.5.0 \
        numpy>=1.24.0 \
        boto3>=1.34.0 \
        openai>=1.0.0 \
        pydantic>=2.0.0 \
    2>&1 | grep -E "(Downloading|Using cached)" || true

echo -e "${GREEN}✓ Python dependencies cached${NC}"

# Warm caches for each Lambda
LAMBDAS=(
    "chromadb_line_polling:chromadb_line_polling_lambda/Dockerfile"
    "chromadb_word_polling:chromadb_word_polling_lambda/Dockerfile"
    "chromadb_compaction:chromadb_compaction_lambda/Dockerfile"
    "find_unembedded_lines:find_unembedded_lines_lambda/Dockerfile"
    "submit_to_openai:submit_to_openai_lambda/Dockerfile"
    "list_pending_batches:list_pending_batches_lambda/Dockerfile"
)

echo ""
echo -e "${BLUE}Warming Lambda build caches...${NC}"
for lambda_info in "${LAMBDAS[@]}"; do
    IFS=':' read -r name path <<< "$lambda_info"
    warm_lambda_cache "$name" "$SCRIPT_DIR/$path"
done

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
echo -e "${GREEN}    Cache warming complete! 🚀${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "  1. Run: export USE_STATIC_BASE_IMAGE=true  (for dev)"
echo "  2. Run: pulumi up"
echo ""