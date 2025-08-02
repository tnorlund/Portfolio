#!/bin/bash
# Script to run tests in parallel with proper coverage support

# Source .env file if it exists
if [ -f .env ]; then
    export $(cat .env | xargs)
fi

# Clean up any previous coverage data
rm -f .coverage*

# Set environment variable for coverage to work with multiprocessing
export COVERAGE_CORE=sysmon

# Run tests with xdist and coverage
pytest "$@" -n auto --cov=receipt_dynamo --cov-report=term-missing

# Combine coverage data from parallel runs (if needed)
if [ -f .coverage ]; then
    coverage combine
    coverage report
fi