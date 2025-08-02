#!/bin/bash
# Wrapper script for running pytest with parallel execution and coverage

# Always set the required environment variable
export COVERAGE_CORE=sysmon

# Run pytest with all arguments passed to this script
exec pytest "$@" -n auto --cov=receipt_dynamo --cov-report=term-missing