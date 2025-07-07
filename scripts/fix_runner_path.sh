#!/bin/bash

# Fix PATH for self-hosted runners to include system utilities

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}🔧 Fixing PATH for self-hosted runners${NC}"
echo ""

RUNNER_BASE="/Users/$(whoami)/GitHub"

# Essential system paths that need to be in PATH
SYSTEM_PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin"

for runner_dir in "$RUNNER_BASE"/actions-runner*; do
    if [[ -d "$runner_dir" ]]; then
        runner_name=$(basename "$runner_dir")
        echo -e "${BLUE}Configuring $runner_name...${NC}"
        
        ENV_FILE="$runner_dir/.env"
        
        # Backup existing .env file
        if [[ -f "$ENV_FILE" ]]; then
            cp "$ENV_FILE" "$ENV_FILE.backup"
        fi
        
        # Remove old PATH if exists
        grep -v "^PATH=" "$ENV_FILE" > "$ENV_FILE.tmp" || true
        mv "$ENV_FILE.tmp" "$ENV_FILE"
        
        # Add complete PATH with system utilities first
        echo "PATH=$SYSTEM_PATH:\$PATH" >> "$ENV_FILE"
        echo -e "${GREEN}  ✅ Fixed PATH${NC}"
        
        # Ensure other environment variables are present
        if ! grep -q "RUNNER_TOOL_CACHE" "$ENV_FILE"; then
            echo "RUNNER_TOOL_CACHE=$HOME/.github-runner-tool-cache" >> "$ENV_FILE"
        fi
        
        if ! grep -q "AGENT_TOOLSDIRECTORY" "$ENV_FILE"; then
            echo "AGENT_TOOLSDIRECTORY=$HOME/.github-runner-tool-cache" >> "$ENV_FILE"
        fi
        
        echo -e "${GREEN}  ✅ Environment configured${NC}"
    fi
done

echo ""
echo -e "${GREEN}🎉 PATH configuration complete!${NC}"
echo ""
echo -e "${YELLOW}Essential utilities now available:${NC}"
echo "• tar (for action downloads)"
echo "• sh/bash (for scripts)"
echo "• git (for checkout)"
echo "• python3.12 (via system/homebrew)"
echo ""
echo -e "${RED}IMPORTANT: Restart runners for changes to take effect:${NC}"
echo "1. pkill -f 'Runner.Listener'"
echo "2. ./scripts/start_runners.sh"