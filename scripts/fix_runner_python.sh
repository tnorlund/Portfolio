#!/bin/bash

# Fix Python setup for self-hosted runners by using environment variables
# This avoids the need for sudo permissions

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}🔧 Configuring Python for self-hosted runners${NC}"
echo ""

# Create local tool cache in user directory
TOOL_CACHE_DIR="$HOME/.github-runner-tool-cache"
mkdir -p "$TOOL_CACHE_DIR"

# Add environment variables to each runner
RUNNER_BASE="/Users/$(whoami)/GitHub"

for runner_dir in "$RUNNER_BASE"/actions-runner*; do
    if [[ -d "$runner_dir" ]]; then
        runner_name=$(basename "$runner_dir")
        echo -e "${BLUE}Configuring $runner_name...${NC}"
        
        # Create .env file if it doesn't exist
        ENV_FILE="$runner_dir/.env"
        
        # Add tool cache directory
        if ! grep -q "RUNNER_TOOL_CACHE" "$ENV_FILE" 2>/dev/null; then
            echo "RUNNER_TOOL_CACHE=$TOOL_CACHE_DIR" >> "$ENV_FILE"
            echo -e "${GREEN}  ✅ Added RUNNER_TOOL_CACHE${NC}"
        fi
        
        # Add Python path workaround
        if ! grep -q "AGENT_TOOLSDIRECTORY" "$ENV_FILE" 2>/dev/null; then
            echo "AGENT_TOOLSDIRECTORY=$TOOL_CACHE_DIR" >> "$ENV_FILE"
            echo -e "${GREEN}  ✅ Added AGENT_TOOLSDIRECTORY${NC}"
        fi
        
        # Ensure PATH includes homebrew Python
        if ! grep -q "PATH=" "$ENV_FILE" 2>/dev/null; then
            echo 'PATH=/opt/homebrew/bin:/usr/local/bin:$PATH' >> "$ENV_FILE"
            echo -e "${GREEN}  ✅ Added homebrew to PATH${NC}"
        fi
    fi
done

echo ""
echo -e "${YELLOW}Installing Python 3.12 via Homebrew...${NC}"
# Check if Python 3.12 is installed
if ! brew list python@3.12 &>/dev/null; then
    brew install python@3.12
    echo -e "${GREEN}✅ Installed Python 3.12${NC}"
else
    echo -e "${GREEN}✅ Python 3.12 already installed${NC}"
fi

# Note: python3.12 should be available directly from Homebrew
# Creating symlinks in Homebrew directories is not recommended
# The workflow will use the full python3.12 path directly

echo ""
echo -e "${GREEN}🎉 Python setup complete!${NC}"
echo ""
echo -e "${YELLOW}Now restart your runners:${NC}"
echo "1. Stop runners: pkill -f 'Runner.Listener'"
echo "2. Start runners: ./scripts/start_runners.sh"
echo "3. Re-run any failed CI jobs"
echo ""
echo -e "${BLUE}What this fixed:${NC}"
echo "• Runners will use local tool cache in $TOOL_CACHE_DIR"
echo "• Python 3.12 available via Homebrew"
echo "• No sudo permissions required"