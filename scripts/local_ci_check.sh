#!/bin/bash

# Local CI Check Script
# Mirrors the quick-tests behavior from pr-checks.yml
# Usage: ./scripts/local_ci_check.sh [package_name]

set -e  # Exit on any error

PACKAGE=${1:-"receipt_dynamo"}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "🔍 Running local CI checks for: $PACKAGE"
echo "📁 Project root: $PROJECT_ROOT"

cd "$PROJECT_ROOT"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print status
print_status() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✅ $2${NC}"
    else
        echo -e "${RED}❌ $2${NC}"
        exit 1
    fi
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

# Step 1: Check formatting
echo ""
echo "🎨 Checking code formatting..."
if command -v black &> /dev/null && command -v isort &> /dev/null; then
    black --check "$PACKAGE" 2>/dev/null && isort --check-only "$PACKAGE" 2>/dev/null
    FORMAT_RESULT=$?
    if [ $FORMAT_RESULT -eq 0 ]; then
        print_status 0 "Code is properly formatted"
    else
        print_warning "Code needs formatting. Run: make format"
        echo "  black $PACKAGE && isort $PACKAGE"
        FORMAT_RESULT=0  # Don't fail on formatting, just warn
    fi
else
    print_warning "black/isort not installed. Installing..."
    pip install black isort
    black --check "$PACKAGE" && isort --check-only "$PACKAGE"
    FORMAT_RESULT=$?
fi

# Step 2: Set up environment (install all key packages with test/dev extras)
echo ""
echo "🔧 Setting up test environment (all core packages)..."

echo "Installing receipt_dynamo with test/dev extras..."
pip install -e "./receipt_dynamo[test,dev]"

echo "Installing receipt_upload with test/dev extras..."
pip install -e "./receipt_upload[test,dev]"

echo "Installing receipt_label with test/dev extras..."
pip install -e "./receipt_label[test,dev]"

echo "Installing portfolio npm deps..."
npm ci --prefix ./portfolio

print_status 0 "Environment ready"

# Step 3: Run tests based on package type
echo ""
echo "🧪 Running tests for $PACKAGE..."

cd "$PACKAGE"

if [[ "$PACKAGE" == "portfolio" ]]; then
    # TypeScript/Node.js tests
    echo "Running TypeScript checks..."
    npm ci --prefer-offline --silent
    npm run lint
    npm run type-check
    TEST_RESULT=$?
elif [[ "$PACKAGE" == "receipt_label" ]]; then
    # Marker-based tests for receipt_label
    echo "Running Python unit tests (marker-based)..."
    pytest tests -n auto -m "unit and not slow" --tb=short -q -o addopts="" --disable-warnings
    TEST_RESULT=$?
else
    # Directory-based tests for receipt_dynamo
    echo "Running Python unit tests (directory-based)..."
    pytest tests/unit -n auto -m "not slow" --tb=short -q -o addopts="" --disable-warnings
    TEST_RESULT=$?
fi

print_status $TEST_RESULT "Tests completed"

# Step 4: Summary
echo ""
echo "📊 Local CI Check Summary for $PACKAGE:"
echo "=================================="
if [ $FORMAT_RESULT -eq 0 ]; then
    echo -e "${GREEN}✅ Formatting: PASS${NC}"
else
    echo -e "${YELLOW}⚠️  Formatting: NEEDS ATTENTION${NC}"
fi

if [ $TEST_RESULT -eq 0 ]; then
    echo -e "${GREEN}✅ Tests: PASS${NC}"
else
    echo -e "${RED}❌ Tests: FAIL${NC}"
fi

echo ""
if [ $TEST_RESULT -eq 0 ]; then
    echo -e "${GREEN}🎉 Ready to push! This should pass CI quick-tests.${NC}"
else
    echo -e "${RED}🚫 Fix test failures before pushing.${NC}"
    exit 1
fi
