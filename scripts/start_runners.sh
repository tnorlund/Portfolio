#!/bin/bash

# Start all GitHub Actions self-hosted runners for Portfolio project
# Optimized for cost savings and parallel execution

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

echo -e "${BLUE}🚀 Starting GitHub Actions Self-Hosted Runners${NC}"
echo -e "${GREEN}This will save ~\$48/month in GitHub Actions costs!${NC}"
echo ""

# Function to check if runner is already running
check_runner() {
    local runner_num=$1
    if ps aux | grep -v grep | grep -q "actions-runner${runner_num:+-$runner_num}/bin/Runner.Listener"; then
        return 0
    else
        return 1
    fi
}

# Function to start a runner
start_runner() {
    local runner_num=$1
    local runner_dir="${RUNNER_BASE}/actions-runner${runner_num:+-$runner_num}"
    local runner_name="runner${runner_num}"

    if [[ ! -d "$runner_dir" ]]; then
        echo -e "${RED}❌ Runner directory not found: $runner_dir${NC}"
        echo "   Run ./scripts/quick_runner_setup.sh first"
        return 1
    fi

    if check_runner "$runner_num"; then
        echo -e "${YELLOW}⚠️  Runner $runner_name is already running${NC}"
        return 0
    fi

    echo -e "${BLUE}Starting runner $runner_name...${NC}"
    cd "$runner_dir"
    nohup ./run.sh > "$runner_dir/runner.log" 2>&1 &

    # Wait a moment for startup
    sleep 2

    if check_runner "$runner_num"; then
        echo -e "${GREEN}✅ Runner $runner_name started successfully${NC}"
    else
        echo -e "${RED}❌ Failed to start runner $runner_name${NC}"
        echo "   Check logs at: $runner_dir/runner.log"
        return 1
    fi
}

# Check system resources
CORES=$(sysctl -n hw.ncpu)
MEMORY_GB=$(sysctl -n hw.memsize | awk '{print int($1/1024/1024/1024)}')

echo -e "${BLUE}System Resources:${NC}"
echo "CPU Cores: $CORES"
echo "Memory: ${MEMORY_GB}GB"
echo ""

# Determine optimal number of runners
if [[ $CORES -ge 10 ]]; then
    RECOMMENDED_RUNNERS=4
elif [[ $CORES -ge 8 ]]; then
    RECOMMENDED_RUNNERS=3
else
    RECOMMENDED_RUNNERS=2
fi

echo -e "${GREEN}Recommended runners for your system: $RECOMMENDED_RUNNERS${NC}"
echo ""

# Start runners
echo -e "${BLUE}Starting runners...${NC}"

# Start primary runner (no suffix) if it exists
if [ -d "${RUNNER_BASE}/actions-runner" ]; then
    start_runner ""
else
    echo -e "${YELLOW}ℹ️  Primary runner not found at ${RUNNER_BASE}/actions-runner, skipping...${NC}"
fi

# Start additional runners based on recommendation
# Check which runners actually exist before starting
for i in $(seq 2 $RECOMMENDED_RUNNERS); do
    start_runner "$i"
done

echo ""
echo -e "${BLUE}Runner Status:${NC}"
ps aux | grep "Runner.Listener" | grep -v grep || echo "No runners found"

echo ""
echo -e "${GREEN}🎉 Runners started!${NC}"
echo ""
echo -e "${YELLOW}Monitoring Commands:${NC}"
echo "• Check status: ps aux | grep 'Runner.Listener' | grep -v grep"
echo "• View logs: tail -f ${RUNNER_BASE}/actions-runner/runner.log"
echo "• Stop all: pkill -f 'Runner.Listener'"
echo ""
echo -e "${BLUE}Cost Savings:${NC}"
echo "• GitHub-hosted: ~\$48/month for your usage"
echo "• Self-hosted: \$0/month (using your Mac)"
echo "• Annual savings: ~\$576/year! 💰"
echo ""
echo -e "${YELLOW}Performance Benefits:${NC}"
echo "• Faster builds (local SSD, no network latency)"
echo "• Better caching (persistent between runs)"
echo "• Parallel execution (${RECOMMENDED_RUNNERS} concurrent jobs)"
