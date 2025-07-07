#!/bin/bash

# Setup permissions and directories for GitHub Actions self-hosted runners
# Fixes permission issues with setup-python and other actions

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}🔧 Setting up runner permissions and directories${NC}"
echo ""

# Create runner user directory if it doesn't exist
if [[ ! -d "/Users/runner" ]]; then
    echo -e "${YELLOW}Creating /Users/runner directory...${NC}"
    sudo mkdir -p /Users/runner
    sudo chown $(whoami):staff /Users/runner
    echo -e "${GREEN}✅ Created /Users/runner${NC}"
else
    echo -e "${GREEN}✅ /Users/runner already exists${NC}"
fi

# Create hostedtoolcache directory for Python installations
if [[ ! -d "/Users/runner/hostedtoolcache" ]]; then
    echo -e "${YELLOW}Creating hostedtoolcache directory...${NC}"
    sudo mkdir -p /Users/runner/hostedtoolcache
    sudo chown -R $(whoami):staff /Users/runner/hostedtoolcache
    echo -e "${GREEN}✅ Created /Users/runner/hostedtoolcache${NC}"
else
    echo -e "${GREEN}✅ /Users/runner/hostedtoolcache already exists${NC}"
fi

# Set proper permissions
echo -e "${BLUE}Setting permissions...${NC}"
sudo chmod -R 755 /Users/runner
sudo chown -R $(whoami):staff /Users/runner

# Create symlink from runner home to actual user home (optional)
if [[ ! -L "/Users/runner/.npm" ]] && [[ -d "$HOME/.npm" ]]; then
    echo -e "${YELLOW}Creating npm cache symlink...${NC}"
    ln -s "$HOME/.npm" "/Users/runner/.npm" 2>/dev/null || true
fi

# Create work directory structure for each runner
RUNNER_BASE="/Users/$(whoami)/GitHub"
for runner_dir in "$RUNNER_BASE"/actions-runner*; do
    if [[ -d "$runner_dir" ]]; then
        runner_name=$(basename "$runner_dir")
        echo -e "${BLUE}Checking $runner_name...${NC}"
        
        # Ensure _work directory exists and has proper permissions
        if [[ ! -d "$runner_dir/_work" ]]; then
            mkdir -p "$runner_dir/_work"
            echo -e "${GREEN}  ✅ Created _work directory${NC}"
        fi
        
        # Set permissions
        chmod -R 755 "$runner_dir/_work" 2>/dev/null || true
    fi
done

echo ""
echo -e "${GREEN}🎉 Runner permissions setup complete!${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Restart your runners: pkill -f 'Runner.Listener' && ./scripts/start_runners.sh"
echo "2. Re-run the failed CI jobs on PR #174"
echo ""
echo -e "${BLUE}What this fixed:${NC}"
echo "• Python setup-python action can now install Python versions"
echo "• Node setup-node action can cache dependencies"
echo "• Tools can be cached between runs for faster builds"