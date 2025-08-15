#!/bin/bash
# This script ensures Docker BuildKit is enabled for the current shell session
# Source this file (don't execute it) to set the environment variable

export DOCKER_BUILDKIT=1
echo "✓ Docker BuildKit enabled for this shell session"

# Also set it in common shell config files for persistence
if [[ "$SHELL" == *"zsh"* ]]; then
    SHELL_CONFIG="$HOME/.zshrc"
elif [[ "$SHELL" == *"bash"* ]]; then
    SHELL_CONFIG="$HOME/.bashrc"
else
    SHELL_CONFIG="$HOME/.profile"
fi

# Check if it's already in the config
if ! grep -q "export DOCKER_BUILDKIT=1" "$SHELL_CONFIG" 2>/dev/null; then
    echo "" >> "$SHELL_CONFIG"
    echo "# Enable Docker BuildKit for faster builds" >> "$SHELL_CONFIG"
    echo "export DOCKER_BUILDKIT=1" >> "$SHELL_CONFIG"
    echo "✓ Added DOCKER_BUILDKIT=1 to $SHELL_CONFIG"
else
    echo "✓ DOCKER_BUILDKIT=1 already in $SHELL_CONFIG"
fi