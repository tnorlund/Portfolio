#!/bin/bash

# Setup script for GitHub Actions self-hosted runner
# This script downloads, extracts, and configures a runner

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Use environment variable if set, otherwise default to $HOME/.github-runners
RUNNER_BASE="${GITHUB_RUNNERS_DIR:-$HOME/.github-runners}"

echo -e "${BLUE}🚀 GitHub Actions Runner Setup${NC}"
echo ""
echo -e "Runner base directory: ${YELLOW}${RUNNER_BASE}${NC}"
echo ""

# Detect architecture
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ]; then
    RUNNER_ARCH="arm64"
    RUNNER_OS="osx"
elif [ "$ARCH" = "x86_64" ]; then
    RUNNER_ARCH="x64"
    RUNNER_OS="osx"
else
    echo -e "${RED}Unsupported architecture: $ARCH${NC}"
    exit 1
fi

# Get runner number
read -p "Enter runner number (1, 2, 3, etc.) or press Enter for runner-1: " RUNNER_NUM
RUNNER_NUM=${RUNNER_NUM:-1}

if [ "$RUNNER_NUM" = "1" ]; then
    RUNNER_DIR="${RUNNER_BASE}/actions-runner"
    RUNNER_NAME="runner-${RUNNER_NUM}"
else
    RUNNER_DIR="${RUNNER_BASE}/actions-runner-${RUNNER_NUM}"
    RUNNER_NAME="runner-${RUNNER_NUM}"
fi

echo -e "${BLUE}Setting up runner at: ${RUNNER_DIR}${NC}"
echo ""

# Create base directory
mkdir -p "$RUNNER_BASE"

# Check if runner already exists
if [ -d "$RUNNER_DIR" ]; then
    echo -e "${YELLOW}⚠️  Runner directory already exists: $RUNNER_DIR${NC}"
    read -p "Do you want to reconfigure it? (y/N): " RECONFIGURE
    if [ "$RECONFIGURE" != "y" ] && [ "$RECONFIGURE" != "Y" ]; then
        echo "Exiting..."
        exit 0
    fi
    # Remove old config if reconfiguring
    rm -rf "$RUNNER_DIR/.runner"
else
    # Create runner directory
    mkdir -p "$RUNNER_DIR"
fi

cd "$RUNNER_DIR"

# Download runner
echo -e "${BLUE}Downloading GitHub Actions runner...${NC}"
RUNNER_VERSION="2.325.0"
RUNNER_URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-${RUNNER_OS}-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
curl -L -o "actions-runner.tar.gz" "$RUNNER_URL"

# Extract
echo -e "${BLUE}Extracting runner...${NC}"
tar xzf actions-runner.tar.gz
rm actions-runner.tar.gz

# Get registration token
echo ""
echo -e "${YELLOW}Getting registration token from GitHub...${NC}"
TOKEN=$(gh api repos/tnorlund/Portfolio/actions/runners/registration-token --method POST --jq '.token')

if [ -z "$TOKEN" ]; then
    echo -e "${RED}Failed to get registration token. Please get it manually from:${NC}"
    echo "https://github.com/tnorlund/Portfolio/settings/actions/runners/new"
    read -p "Enter registration token: " TOKEN
fi

# Configure runner
echo ""
echo -e "${BLUE}Configuring runner...${NC}"
echo -e "${YELLOW}Runner will be configured with:${NC}"
echo "  Name: $RUNNER_NAME"
echo "  URL: https://github.com/tnorlund/Portfolio"
echo ""

./config.sh --url https://github.com/tnorlund/Portfolio --token "$TOKEN" --name "$RUNNER_NAME" --work _work --replace

echo ""
echo -e "${GREEN}✅ Runner configured successfully!${NC}"
echo ""
echo -e "${BLUE}Next steps:${NC}"
echo "1. Set GITHUB_RUNNERS_DIR if using a custom location:"
echo "   export GITHUB_RUNNERS_DIR=\"${RUNNER_BASE}\""
echo ""
echo "2. Start the runner:"
echo "   cd ${RUNNER_DIR} && ./run.sh"
echo ""
echo "Or use the start script:"
echo "   ./scripts/start_runners.sh"
echo ""

