#!/bin/bash

# Quick setup for multiple GitHub Actions runners
# Optimized for development velocity on high-performance Mac

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Use environment variable if set, otherwise default to $HOME/.github-runners
# To use a custom location, set: export GITHUB_RUNNERS_DIR="/path/to/runners"
RUNNER_BASE="${GITHUB_RUNNERS_DIR:-$HOME/.github-runners}"

echo -e "${BLUE}🚀 Quick Parallel Runner Setup for Development Velocity${NC}"
echo -e "${GREEN}Target: 4 runners for maximum parallelization${NC}"
echo ""

# Check system resources
CORES=$(sysctl -n hw.ncpu)
MEMORY_GB=$(sysctl -n hw.memsize | awk '{print int($1/1024/1024/1024)}')

echo -e "${BLUE}System Resources:${NC}"
echo "CPU Cores: $CORES"
echo "Memory: ${MEMORY_GB}GB"
echo "Architecture: $(uname -m)"
echo ""

if [[ $CORES -lt 8 ]] || [[ $MEMORY_GB -lt 16 ]]; then
    echo -e "${YELLOW}⚠️  System might be underpowered for 4 runners${NC}"
    echo "Recommended: 2-3 runners for your system"
else
    echo -e "${GREEN}✅ Excellent system for 4 parallel runners!${NC}"
fi
echo ""

# Function to setup runner directory
setup_runner_dir() {
    local runner_num=$1
    local runner_dir="${RUNNER_BASE}/actions-runner-${runner_num}"

    echo -e "${BLUE}Setting up runner ${runner_num} directory...${NC}"

    if [[ -d "$runner_dir" ]]; then
        echo -e "${YELLOW}Directory exists: $runner_dir${NC}"
        return 0
    fi

    mkdir -p "$runner_dir"
    cd "$runner_dir"

    # Download and extract runner
    echo "Downloading GitHub Actions runner..."
    curl -s -o actions-runner-osx-arm64.tar.gz -L https://github.com/actions/runner/releases/download/v2.325.0/actions-runner-osx-arm64-2.325.0.tar.gz
    tar xzf actions-runner-osx-arm64.tar.gz
    rm actions-runner-osx-arm64.tar.gz

    echo -e "${GREEN}✅ Runner ${runner_num} directory ready${NC}"
}

# Setup runners 2, 3, and 4 (assuming 1 exists)
echo -e "${BLUE}Creating runner directories...${NC}"
setup_runner_dir 2
setup_runner_dir 3
setup_runner_dir 4

echo ""
echo -e "${GREEN}🎉 Runner directories created!${NC}"
echo ""
echo -e "${YELLOW}Next Steps (do these in order):${NC}"
echo ""

# Generate registration commands
for i in {2..4}; do
    echo -e "${BLUE}=== Runner $i Configuration ===${NC}"
    echo "1. Open: https://github.com/tnorlund/Portfolio/settings/actions/runners/new"
    echo "2. Copy the token and run:"
    echo "   cd ${RUNNER_BASE}/actions-runner-${i}"
    echo "   ./config.sh --url https://github.com/tnorlund/Portfolio --token YOUR_TOKEN_HERE --name tyler-mac-m1-${i}"
    echo ""
done

echo -e "${BLUE}=== Start All Runners (after configuration) ===${NC}"
echo ""
echo "Option A: Manual (4 terminal windows):"
for i in {1..4}; do
    echo "Terminal $i: cd ${RUNNER_BASE}/actions-runner$([ $i -eq 1 ] && echo "" || echo "-$i") && ./run.sh"
done

echo ""
echo "Option B: Background processes:"
cat << EOF
# Start all runners in background
cd ${RUNNER_BASE}/actions-runner && nohup ./run.sh > runner1.log 2>&1 &
cd ${RUNNER_BASE}/actions-runner-2 && nohup ./run.sh > runner2.log 2>&1 &
cd ${RUNNER_BASE}/actions-runner-3 && nohup ./run.sh > runner3.log 2>&1 &
cd ${RUNNER_BASE}/actions-runner-4 && nohup ./run.sh > runner4.log 2>&1 &

# Check status
ps aux | grep "Runner.Listener" | grep -v grep
EOF

echo ""
echo -e "${GREEN}Expected Performance Gains:${NC}"
echo "• Current: 2 Python jobs sequential = ~4-6 minutes"
echo "• With 4 runners: All jobs parallel = ~2-3 minutes"
echo "• Multiple PRs: Can test 4 PRs simultaneously!"
echo "• Cost: Still $0 for all self-hosted runners"
echo ""
echo -e "${YELLOW}💡 Pro Tips:${NC}"
echo "• Use 'dev_workflow.sh' option 17 to check all runner status"
echo "• Monitor CPU usage: top -o cpu"
echo "• Each runner uses ~1-2GB RAM during active testing"
