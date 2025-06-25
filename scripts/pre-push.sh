#!/bin/bash
# Pre-push checks to catch issues before CI

set -e

echo "🔍 Running pre-push checks..."

# Check if we're in the right directory
if [ ! -f "Makefile" ]; then
    echo "❌ Error: Must run from repository root"
    exit 1
fi

# 1. Format check
echo "📝 Checking code formatting..."
if ! black --check receipt_dynamo receipt_label infra >/dev/null 2>&1; then
    echo "❌ Black formatting check failed!"
    echo "   Run 'make format' to fix formatting issues"
    exit 1
fi

if ! isort --check-only receipt_dynamo receipt_label infra >/dev/null 2>&1; then
    echo "❌ Import sorting check failed!"
    echo "   Run 'make format' to fix import order"
    exit 1
fi

echo "✅ Code formatting OK"

# 2. Fast tests
echo "🧪 Running fast unit tests..."
cd receipt_dynamo
if ! pytest -m "not integration and not end_to_end" --fail-fast -x -q; then
    echo "❌ receipt_dynamo tests failed!"
    exit 1
fi
cd ..

cd receipt_label
if ! pytest -m "not integration and not end_to_end" --fail-fast -x -q; then
    echo "❌ receipt_label tests failed!"
    exit 1
fi
cd ..

echo "✅ Tests passed"

# 3. Check for large files
echo "📦 Checking for large files..."
large_files=$(find . -type f -size +1M | grep -v -E "node_modules|\.git|\.next|dist|build|coverage" || true)
if [ -n "$large_files" ]; then
    echo "⚠️  Warning: Large files detected (>1MB):"
    echo "$large_files"
    echo "Consider using Git LFS for these files"
fi

# 4. Check for secrets
echo "🔐 Checking for potential secrets..."
if grep -r -E "(AWS_SECRET|API_KEY|PASSWORD|TOKEN)" --include="*.py" --include="*.ts" --include="*.tsx" --exclude-dir=node_modules --exclude-dir=.git . | grep -v -E "(os\.environ|process\.env|example|test)" >/dev/null 2>&1; then
    echo "⚠️  Warning: Potential secrets found in code!"
    echo "   Please review your changes for hardcoded secrets"
fi

echo "✅ All pre-push checks passed!"
echo "📤 Ready to push!"