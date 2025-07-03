#!/bin/bash

# Code Formatting Script
# Equivalent to "make format" - formats all Python code
# Usage: ./scripts/format_code.sh [package_or_directory]

set -e

TARGET=${1:-"."}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "🎨 Formatting Python code in: $TARGET"

# Install formatters if not present
if ! command -v black &> /dev/null || ! command -v isort &> /dev/null; then
    echo "📦 Installing formatters..."
    pip install black isort
fi

# Run formatters
echo -e "${BLUE}🔧 Running isort (import sorting)...${NC}"
isort "$TARGET"

echo -e "${BLUE}🎨 Running black (code formatting)...${NC}"
black "$TARGET"

echo -e "${GREEN}✅ Code formatting complete!${NC}"
echo ""
echo "Files that were changed:"
git diff --name-only 2>/dev/null || echo "  (No git repository or no changes)"
echo ""
echo "Next steps:"
echo "1. Review changes: git diff"
echo "2. Test locally: ./scripts/local_ci_check.sh [package]"
echo "3. Commit: git add -A && git commit -m 'style: format code'"
