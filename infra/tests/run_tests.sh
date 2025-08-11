#!/bin/bash

# Test runner script for embedding pipeline
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test modes
MODE=${1:-unit}
COVERAGE=${2:-false}

echo -e "${GREEN}=== Embedding Pipeline Test Runner ===${NC}"
echo "Mode: $MODE"
echo "Coverage: $COVERAGE"
echo ""

# Install test dependencies if needed
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements-test.txt
else
    source .venv/bin/activate
fi

# Start LocalStack if needed for integration tests
start_localstack() {
    echo -e "${YELLOW}Starting LocalStack...${NC}"
    docker-compose -f docker-compose.test.yml up -d
    
    # Wait for LocalStack to be ready
    echo "Waiting for LocalStack to be ready..."
    for i in {1..30}; do
        if curl -s http://localhost:4566/_localstack/health | grep -q '"services":'; then
            echo -e "${GREEN}LocalStack is ready!${NC}"
            break
        fi
        sleep 1
    done
}

# Stop LocalStack
stop_localstack() {
    echo -e "${YELLOW}Stopping LocalStack...${NC}"
    docker-compose -f docker-compose.test.yml down
}

# Run tests based on mode
case $MODE in
    unit)
        echo -e "${GREEN}Running unit tests...${NC}"
        if [ "$COVERAGE" = "true" ]; then
            pytest tests/unit -v --cov=embedding_step_functions --cov-report=html --cov-report=term
        else
            pytest tests/unit -v
        fi
        ;;
    
    integration)
        echo -e "${GREEN}Running integration tests...${NC}"
        start_localstack
        
        if [ "$COVERAGE" = "true" ]; then
            pytest tests/integration -v --cov=embedding_step_functions --cov-report=html --cov-report=term
        else
            pytest tests/integration -v
        fi
        
        stop_localstack
        ;;
    
    infrastructure)
        echo -e "${GREEN}Running infrastructure tests...${NC}"
        export PULUMI_ACCESS_TOKEN=${PULUMI_ACCESS_TOKEN:-"local"}
        
        if [ "$COVERAGE" = "true" ]; then
            pytest tests/infrastructure -v --cov=embedding_step_functions --cov-report=html
        else
            pytest tests/infrastructure -v
        fi
        ;;
    
    e2e)
        echo -e "${GREEN}Running end-to-end tests...${NC}"
        start_localstack
        
        # Set up test environment
        export AWS_ACCESS_KEY_ID=test
        export AWS_SECRET_ACCESS_KEY=test
        export AWS_DEFAULT_REGION=us-east-1
        export LOCALSTACK_ENDPOINT=http://localhost:4566
        
        if [ "$COVERAGE" = "true" ]; then
            pytest tests/e2e -v --cov=embedding_step_functions --cov-report=html
        else
            pytest tests/e2e -v
        fi
        
        stop_localstack
        ;;
    
    all)
        echo -e "${GREEN}Running all tests...${NC}"
        
        # Run unit tests first (fastest)
        echo -e "\n${YELLOW}Unit Tests:${NC}"
        pytest tests/unit -v
        
        # Run integration tests
        echo -e "\n${YELLOW}Integration Tests:${NC}"
        start_localstack
        pytest tests/integration -v
        stop_localstack
        
        # Run infrastructure tests
        echo -e "\n${YELLOW}Infrastructure Tests:${NC}"
        pytest tests/infrastructure -v
        
        # Run e2e tests
        echo -e "\n${YELLOW}End-to-End Tests:${NC}"
        start_localstack
        pytest tests/e2e -v
        stop_localstack
        
        if [ "$COVERAGE" = "true" ]; then
            echo -e "\n${YELLOW}Generating coverage report...${NC}"
            pytest tests/ -v --cov=embedding_step_functions --cov-report=html --cov-report=term
            echo -e "${GREEN}Coverage report generated in htmlcov/index.html${NC}"
        fi
        ;;
    
    performance)
        echo -e "${GREEN}Running performance tests...${NC}"
        pytest tests/ -v -m performance --durations=10
        ;;
    
    *)
        echo -e "${RED}Invalid mode: $MODE${NC}"
        echo "Usage: ./run_tests.sh [mode] [coverage]"
        echo "Modes: unit, integration, infrastructure, e2e, all, performance"
        echo "Coverage: true or false (default: false)"
        exit 1
        ;;
esac

echo -e "\n${GREEN}Tests completed!${NC}"

# Generate test report
if [ "$COVERAGE" = "true" ] && [ -d "htmlcov" ]; then
    echo -e "${YELLOW}Coverage report available at: htmlcov/index.html${NC}"
fi