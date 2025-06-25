#!/bin/bash
# Quick test runner with optimizations

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
PACKAGE=""
COVERAGE=false
SLOW=false
VERBOSE=false
WORKERS="auto"

# Help function
show_help() {
    echo "Usage: ./scripts/test.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -p, --package PACKAGE    Test specific package (receipt_dynamo, receipt_label)"
    echo "  -c, --coverage          Enable coverage reporting"
    echo "  -s, --slow              Include slow tests"
    echo "  -v, --verbose           Verbose output"
    echo "  -w, --workers NUM       Number of parallel workers (default: auto)"
    echo "  -h, --help              Show this help message"
    echo ""
    echo "Examples:"
    echo "  ./scripts/test.sh -p receipt_dynamo          # Test receipt_dynamo"
    echo "  ./scripts/test.sh -p receipt_label -c         # Test with coverage"
    echo "  ./scripts/test.sh -p receipt_dynamo -s -v    # Include slow tests with verbose output"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--package)
            PACKAGE="$2"
            shift 2
            ;;
        -c|--coverage)
            COVERAGE=true
            shift
            ;;
        -s|--slow)
            SLOW=true
            shift
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -w|--workers)
            WORKERS="$2"
            shift 2
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Validate package
if [[ -z "$PACKAGE" ]]; then
    echo -e "${RED}Error: Package must be specified${NC}"
    show_help
    exit 1
fi

if [[ "$PACKAGE" != "receipt_dynamo" && "$PACKAGE" != "receipt_label" ]]; then
    echo -e "${RED}Error: Invalid package '$PACKAGE'${NC}"
    echo "Valid packages: receipt_dynamo, receipt_label"
    exit 1
fi

# Build pytest command
PYTEST_CMD="python -m pytest"

# Add parallel execution
PYTEST_CMD="$PYTEST_CMD -n $WORKERS"

# Add package tests directory
PYTEST_CMD="$PYTEST_CMD $PACKAGE/tests"

# Add markers
if [[ "$SLOW" == false ]]; then
    PYTEST_CMD="$PYTEST_CMD -m 'not end_to_end and not slow'"
else
    PYTEST_CMD="$PYTEST_CMD -m 'not end_to_end'"
fi

# Add other options
PYTEST_CMD="$PYTEST_CMD --tb=short --maxfail=5 -x --timeout=60"

# Add verbosity
if [[ "$VERBOSE" == true ]]; then
    PYTEST_CMD="$PYTEST_CMD -v"
else
    PYTEST_CMD="$PYTEST_CMD -q"
fi

# Add coverage if requested
if [[ "$COVERAGE" == true ]]; then
    PYTEST_CMD="$PYTEST_CMD --cov=$PACKAGE --cov-report=term-missing --cov-report=html"
fi

# Show test durations
PYTEST_CMD="$PYTEST_CMD --durations=10"

# Clear previous results
echo -e "${YELLOW}Clearing pytest cache...${NC}"
python -m pytest --cache-clear > /dev/null 2>&1 || true

# Run tests
echo -e "${GREEN}Running tests for $PACKAGE...${NC}"
echo -e "${YELLOW}Command: $PYTEST_CMD${NC}"
echo ""

# Time the execution
START_TIME=$(date +%s)

# Run pytest
if $PYTEST_CMD; then
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    echo ""
    echo -e "${GREEN}✓ All tests passed in ${DURATION}s${NC}"
    
    if [[ "$COVERAGE" == true ]]; then
        echo -e "${YELLOW}Coverage report saved to htmlcov/index.html${NC}"
    fi
else
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    echo ""
    echo -e "${RED}✗ Some tests failed after ${DURATION}s${NC}"
    exit 1
fi