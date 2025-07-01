#!/bin/bash

# Script to set up multiple self-hosted runners for parallelization
# Usage: ./scripts/setup_parallel_runners.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}🚀 Setting up parallel self-hosted runners${NC}"
echo ""

# Check if we're on macOS
if [[ "$OSTYPE" != "darwin"* ]]; then
    echo -e "${YELLOW}⚠️  This script is designed for macOS${NC}"
    exit 1
fi

# Base runner directory
RUNNER_BASE="/Users/$(whoami)/GitHub"

echo -e "${GREEN}Setting up additional runners for parallelization...${NC}"
echo ""

# Function to setup a runner
setup_runner() {
    local runner_num=$1
    local runner_dir="${RUNNER_BASE}/actions-runner-${runner_num}"

    echo -e "${BLUE}Setting up runner ${runner_num}...${NC}"

    # Create directory
    mkdir -p "$runner_dir"
    cd "$runner_dir"

    # Download runner if not exists
    if [[ ! -f "run.sh" ]]; then
        echo "Downloading GitHub Actions runner..."
        curl -o actions-runner-osx-arm64-2.325.0.tar.gz -L https://github.com/actions/runner/releases/download/v2.325.0/actions-runner-osx-arm64-2.325.0.tar.gz
        tar xzf actions-runner-osx-arm64-2.325.0.tar.gz
        rm actions-runner-osx-arm64-2.325.0.tar.gz
    fi

    echo -e "${GREEN}✅ Runner ${runner_num} directory ready: ${runner_dir}${NC}"
    echo -e "${YELLOW}Next steps for runner ${runner_num}:${NC}"
    echo "1. Get a new configuration token from: https://github.com/tnorlund/Portfolio/settings/actions/runners/new"
    echo "2. Run: cd ${runner_dir} && ./config.sh --url https://github.com/tnorlund/Portfolio --token YOUR_TOKEN --name tyler-mac-m1-${runner_num}"
    echo "3. Start: cd ${runner_dir} && ./run.sh"
    echo ""
}

# Setup runners 2 and 3 (assuming runner 1 already exists)
setup_runner 2
setup_runner 3

echo -e "${GREEN}🎉 Parallel runner setup complete!${NC}"
echo ""
echo -e "${BLUE}To start all runners in parallel:${NC}"
echo ""
echo "# Terminal 1 (existing runner):"
echo "cd /Users/$(whoami)/GitHub/actions-runner && ./run.sh"
echo ""
echo "# Terminal 2 (new runner):"
echo "cd /Users/$(whoami)/GitHub/actions-runner-2 && ./run.sh"
echo ""
echo "# Terminal 3 (new runner):"
echo "cd /Users/$(whoami)/GitHub/actions-runner-3 && ./run.sh"
echo ""
echo -e "${YELLOW}💡 Pro tip: Use tmux or separate terminal tabs to manage multiple runners${NC}"
echo ""
echo -e "${GREEN}Expected performance improvement:${NC}"
echo "- Current: 2 Python jobs run sequentially (~4-6 minutes)"
echo "- With 3 runners: 2 Python jobs run in parallel (~2-3 minutes)"
echo "- Same cost: $0 for all self-hosted runners!"
