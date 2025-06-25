#!/bin/bash
# Quick test runner for local development with optimized settings

set -e

PACKAGE=""
TEST_TYPE="unit"
VERBOSE=false
COVERAGE=false
PARALLEL=true
FAIL_FAST=false

usage() {
    echo "Usage: $0 [OPTIONS] PACKAGE"
    echo ""
    echo "Options:"
    echo "  -t, --type TYPE        Test type: unit, integration, end_to_end, all (default: unit)"
    echo "  -v, --verbose          Enable verbose output"
    echo "  -c, --coverage         Enable coverage reporting"
    echo "  -s, --sequential       Run tests sequentially (no parallelization)"
    echo "  -x, --fail-fast        Stop on first failure"
    echo "  -h, --help             Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 receipt_dynamo                    # Run unit tests"
    echo "  $0 -t integration receipt_dynamo     # Run integration tests"
    echo "  $0 -t all -c receipt_dynamo          # Run all tests with coverage"
    echo "  $0 -v -x receipt_label               # Run unit tests verbose, fail fast"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -t|--type)
            TEST_TYPE="$2"
            shift 2
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -c|--coverage)
            COVERAGE=true
            shift
            ;;
        -s|--sequential)
            PARALLEL=false
            shift
            ;;
        -x|--fail-fast)
            FAIL_FAST=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "Unknown option $1"
            usage
            exit 1
            ;;
        *)
            PACKAGE="$1"
            shift
            ;;
    esac
done

if [[ -z "$PACKAGE" ]]; then
    echo "Error: Package name is required"
    usage
    exit 1
fi

if [[ ! -d "$PACKAGE" ]]; then
    echo "Error: Package directory '$PACKAGE' not found"
    exit 1
fi

# Determine test paths
case $TEST_TYPE in
    unit)
        TEST_PATH="tests/unit"
        ;;
    integration)
        TEST_PATH="tests/integration"
        ;;
    end_to_end)
        TEST_PATH="tests/end_to_end"
        ;;
    all)
        TEST_PATH="tests"
        ;;
    *)
        echo "Error: Invalid test type '$TEST_TYPE'"
        echo "Valid types: unit, integration, end_to_end, all"
        exit 1
        ;;
esac

# Check if test directory exists
if [[ ! -d "$PACKAGE/$TEST_PATH" ]]; then
    echo "Error: Test directory '$PACKAGE/$TEST_PATH' not found"
    exit 1
fi

echo "🧪 Running $TEST_TYPE tests for $PACKAGE"
echo "📁 Test path: $PACKAGE/$TEST_PATH"

# Change to package directory
cd "$PACKAGE"

# Build pytest command
PYTEST_CMD="python -m pytest $TEST_PATH"

# Add parallelization
if [[ "$PARALLEL" == true && "$TEST_TYPE" != "end_to_end" ]]; then
    if [[ "$TEST_TYPE" == "unit" ]]; then
        PYTEST_CMD="$PYTEST_CMD -n auto"
    else
        PYTEST_CMD="$PYTEST_CMD -n 2"  # Conservative for integration tests
    fi
fi

# Add common flags
PYTEST_CMD="$PYTEST_CMD -q --tb=short"

# Add test markers
if [[ "$TEST_TYPE" != "end_to_end" ]]; then
    PYTEST_CMD="$PYTEST_CMD -m 'not end_to_end and not slow'"
fi

# Add timeout
if [[ "$TEST_TYPE" == "unit" ]]; then
    PYTEST_CMD="$PYTEST_CMD --timeout=30"
elif [[ "$TEST_TYPE" == "integration" ]]; then
    PYTEST_CMD="$PYTEST_CMD --timeout=300"
fi

# Add fail-fast
if [[ "$FAIL_FAST" == true ]]; then
    PYTEST_CMD="$PYTEST_CMD -x"
else
    PYTEST_CMD="$PYTEST_CMD --maxfail=3"
fi

# Add verbose
if [[ "$VERBOSE" == true ]]; then
    PYTEST_CMD="$PYTEST_CMD -v --tb=long"
fi

# Add coverage
if [[ "$COVERAGE" == true ]]; then
    PYTEST_CMD="$PYTEST_CMD --cov=$PACKAGE --cov-report=term-missing --cov-report=html"
fi

# Add durations
PYTEST_CMD="$PYTEST_CMD --durations=10"

echo "🚀 Command: $PYTEST_CMD"
echo ""

# Run tests
eval $PYTEST_CMD

# Show results
if [[ $? -eq 0 ]]; then
    echo ""
    echo "✅ Tests passed!"
    if [[ "$COVERAGE" == true ]]; then
        echo "📊 Coverage report generated in htmlcov/"
    fi
else
    echo ""
    echo "❌ Tests failed!"
    exit 1
fi