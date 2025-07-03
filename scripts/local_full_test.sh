#!/bin/bash

# Local Full Test Script
# Mirrors the full test-python behavior from main.yml
# Usage: ./scripts/local_full_test.sh [package] [test_type] [test_group]

set -e

PACKAGE=${1:-"receipt_dynamo"}
TEST_TYPE=${2:-"unit"}
TEST_GROUP=${3:-""}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "🔍 Running full local tests"
echo "📦 Package: $PACKAGE"
echo "🧪 Test type: $TEST_TYPE"
echo "👥 Test group: ${TEST_GROUP:-"all"}"
echo "📁 Project root: $PROJECT_ROOT"

cd "$PROJECT_ROOT"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✅ $2${NC}"
    else
        echo -e "${RED}❌ $2${NC}"
        exit 1
    fi
}

print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

# Step 1: Environment setup
echo ""
echo "🔧 Setting up comprehensive test environment..."

# Install test dependencies with caching simulation
if pip list | grep -q "pytest-xdist"; then
    print_info "Test dependencies already installed"
else
    print_info "Installing test dependencies..."
    pip install pytest pytest-xdist pytest-timeout psutil moto boto3 --disable-pip-version-check
fi

# Install receipt_dynamo first (dependency for other packages)
if [[ "$PACKAGE" != "receipt_dynamo" ]]; then
    if pip list | grep -q "receipt-dynamo"; then
        print_info "receipt_dynamo already installed"
    else
        pip install -e "receipt_dynamo[dev]"
    fi
fi

# Install target package
PACKAGE_NAME=$(echo "$PACKAGE" | tr '_' '-')
if pip list | grep -q "${PACKAGE_NAME}"; then
    print_info "$PACKAGE already installed"
else
    pip install -e "$PACKAGE[test]"
fi

print_status 0 "Environment ready"

# Step 2: Determine test paths based on package and type
echo ""
echo "🎯 Determining test paths..."

if [[ "$PACKAGE" == "receipt_dynamo" ]]; then
    if [[ "$TEST_TYPE" == "unit" ]]; then
        TEST_PATH="tests/unit"
        TEST_MARKERS=""
    elif [[ "$TEST_TYPE" == "integration" ]]; then
        case "$TEST_GROUP" in
            "group-1")
                TEST_PATH="tests/integration/test__receipt_word_label.py tests/integration/test__receipt.py tests/integration/test__receipt_validation_category.py tests/integration/test__geometry.py tests/integration/test__job_metric.py tests/integration/test__receipt_structure_analysis.py tests/integration/test__line.py tests/integration/test__receipt_line.py tests/integration/test__pulumi.py tests/integration/test__cluster.py"
                ;;
            "group-2")
                TEST_PATH="tests/integration/test__receipt_field.py tests/integration/test__receipt_chatgpt_validation.py tests/integration/test__receipt_validation_summary.py tests/integration/test__gpt.py tests/integration/test__job_checkpoint.py tests/integration/test__word.py tests/integration/test__word_tag.py tests/integration/test__letter.py tests/integration/test__label_count_cache.py tests/integration/test_dynamo_client.py"
                ;;
            "group-3")
                TEST_PATH="tests/integration/test__receipt_letter.py tests/integration/test__receipt_line_item_analysis.py tests/integration/test__queue.py tests/integration/test__instance.py tests/integration/test__receipt_word.py tests/integration/test__job_dependency.py tests/integration/test__ocr_job.py tests/integration/test__batch_summary.py tests/integration/test__receipt_section.py tests/integration/test__export_and_import.py"
                ;;
            "group-4")
                TEST_PATH="tests/integration/test__receipt_validation_result.py tests/integration/test__receipt_label_analysis.py tests/integration/test__job.py tests/integration/test__job_resource.py tests/integration/test__image.py tests/integration/test__job_log.py tests/integration/test__places_cache.py tests/integration/test__receipt_word_tag.py tests/integration/test__ocr.py"
                ;;
            *)
                TEST_PATH="tests/integration"
                ;;
        esac
        TEST_MARKERS=""
    fi
elif [[ "$PACKAGE" == "receipt_label" ]]; then
    TEST_PATH="receipt_label/tests"
    if [[ "$TEST_TYPE" == "unit" ]]; then
        TEST_MARKERS="-m unit"
    elif [[ "$TEST_TYPE" == "integration" ]]; then
        TEST_MARKERS="-m integration"
    fi
fi

print_info "Test path: $TEST_PATH"
print_info "Test markers: ${TEST_MARKERS:-"none"}"

# Step 3: Run tests with optimizations
echo ""
echo "🧪 Running tests with CI-like configuration..."

cd "$PACKAGE"

# Build pytest command
PYTEST_CMD="python -m pytest"

# Add test path
if [[ -n "$TEST_PATH" ]]; then
    PYTEST_CMD="$PYTEST_CMD $TEST_PATH"
fi

# Add markers if specified
if [[ -n "$TEST_MARKERS" ]]; then
    PYTEST_CMD="$PYTEST_CMD $TEST_MARKERS"
fi

# Add CI-like options
PYTEST_CMD="$PYTEST_CMD -n auto --timeout=600 --tb=short"

# Add coverage for comprehensive testing
if [[ "$TEST_TYPE" == "unit" ]]; then
    PYTEST_CMD="$PYTEST_CMD --cov=$PACKAGE --cov-report=term-missing --cov-report=xml"
fi

print_info "Running: $PYTEST_CMD"
echo ""

# Execute tests
eval $PYTEST_CMD
TEST_RESULT=$?

# Step 4: Report results
echo ""
echo "📊 Test Results Summary:"
echo "========================"
echo "Package: $PACKAGE"
echo "Test Type: $TEST_TYPE"
echo "Test Group: ${TEST_GROUP:-"all"}"

if [ $TEST_RESULT -eq 0 ]; then
    echo -e "${GREEN}✅ All tests passed!${NC}"
    echo ""
    echo -e "${GREEN}🎉 This should pass the corresponding CI job.${NC}"

    # Provide next steps
    echo ""
    echo "Next steps:"
    echo "1. Run formatting: make format"
    echo "2. Commit your changes: git add -A && git commit"
    echo "3. Push to trigger CI: git push"
else
    echo -e "${RED}❌ Tests failed!${NC}"
    echo ""
    echo -e "${RED}🚫 Fix failures before pushing to CI.${NC}"
fi

exit $TEST_RESULT
