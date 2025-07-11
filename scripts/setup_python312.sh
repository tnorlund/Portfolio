#!/bin/bash

# Setup Python 3.12 for self-hosted runners

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}🐍 Setting up Python 3.12 for self-hosted runners${NC}"
echo ""

# Check if Python 3.12 is installed
if command -v python3.12 &> /dev/null; then
    echo -e "${GREEN}✅ Python 3.12 is already installed${NC}"
    echo "Location: $(which python3.12)"
    echo "Version: $(python3.12 --version)"
else
    echo -e "${YELLOW}Python 3.12 not found in PATH${NC}"
    
    # Check common locations
    if [[ -f "/opt/homebrew/bin/python3.12" ]]; then
        echo -e "${GREEN}Found Python 3.12 at /opt/homebrew/bin/python3.12${NC}"
        PYTHON_PATH="/opt/homebrew/bin/python3.12"
    elif [[ -f "/usr/local/bin/python3.12" ]]; then
        echo -e "${GREEN}Found Python 3.12 at /usr/local/bin/python3.12${NC}"
        PYTHON_PATH="/usr/local/bin/python3.12"
    elif [[ -f "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12" ]]; then
        echo -e "${GREEN}Found Python 3.12 at /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12${NC}"
        PYTHON_PATH="/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12"
    else
        echo -e "${RED}Python 3.12 not found. Please install it first.${NC}"
        exit 1
    fi
fi

# Update runner environments to use Python 3.12
RUNNER_BASE="/Users/$(whoami)/GitHub"

for runner_dir in "$RUNNER_BASE"/actions-runner*; do
    if [[ -d "$runner_dir" ]]; then
        runner_name=$(basename "$runner_dir")
        echo -e "${BLUE}Configuring $runner_name...${NC}"
        
        ENV_FILE="$runner_dir/.path"
        
        # Ensure Python 3.12 directory is in PATH
        PYTHON_DIR=$(dirname "${PYTHON_PATH:-$(which python3.12)}")
        
        # Check if Python directory is already in .path file
        if [[ -f "$ENV_FILE" ]]; then
            if ! grep -q "$PYTHON_DIR" "$ENV_FILE" 2>/dev/null; then
                # Prepend Python directory to ensure it's found first
                echo "$PYTHON_DIR" | cat - "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
                echo -e "${GREEN}  ✅ Added Python 3.12 directory to PATH${NC}"
            else
                echo -e "${GREEN}  ✅ Python 3.12 directory already in PATH${NC}"
            fi
        else
            echo "$PYTHON_DIR" > "$ENV_FILE"
            echo -e "${GREEN}  ✅ Created .path file with Python 3.12${NC}"
        fi
        
        # Create python3 symlink pointing to python3.12
        RUNNER_BIN="$runner_dir/bin"
        mkdir -p "$RUNNER_BIN"
        if [[ ! -L "$RUNNER_BIN/python3" ]]; then
            ln -sf "${PYTHON_PATH:-$(which python3.12)}" "$RUNNER_BIN/python3"
            echo -e "${GREEN}  ✅ Created python3 symlink${NC}"
        fi
        
        # Add runner bin to .path
        if ! grep -q "$RUNNER_BIN" "$ENV_FILE" 2>/dev/null; then
            echo "$RUNNER_BIN" | cat - "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
            echo -e "${GREEN}  ✅ Added runner bin to PATH${NC}"
        fi
    fi
done

echo ""
echo -e "${GREEN}🎉 Python 3.12 setup complete!${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Restart runners: pkill -f 'Runner.Listener' && ./scripts/start_runners.sh"
echo "2. CI will now use Python 3.12 automatically"
echo ""
echo -e "${BLUE}What was configured:${NC}"
echo "• Python 3.12 added to runner PATH"
echo "• python3 symlink points to python3.12"
echo "• Runners will use Python 3.12 for all operations"