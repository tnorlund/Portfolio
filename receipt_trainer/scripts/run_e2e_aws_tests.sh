#!/bin/bash
# Script to run end-to-end tests with real AWS resources
# CAUTION: This will create actual AWS resources and incur costs

set -e  # Exit on error

# Set colors for output
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Display warning
echo -e "${YELLOW}WARNING: This will create REAL AWS resources and may incur costs.${NC}"
echo "Make sure you have AWS credentials configured for the right account."
echo "Press Ctrl+C now to cancel, or Enter to continue..."

read -r

# Enable real AWS tests
export ENABLE_REAL_AWS_TESTS=1

# Set AWS profile if provided
if [ -n "$1" ]; then
    export AWS_PROFILE="$1"
    echo -e "${YELLOW}Using AWS profile: $AWS_PROFILE${NC}"
fi

# Run just the real AWS tests
echo -e "${GREEN}Running end-to-end tests...${NC}"
python -m pytest receipt_trainer/tests/end_to_end/test_utils/test_auto_scaling.py::test_real_auto_scaling_e2e -v -s

# Get the test result status
TEST_STATUS=$?

# Print status based on test results
echo ""
if [ $TEST_STATUS -eq 0 ]; then
    echo -e "${GREEN}Tests completed successfully.${NC}"
else
    echo -e "${RED}Tests failed with exit code $TEST_STATUS.${NC}"
fi

# Automatically verify cleanup, regardless of test result
echo ""
echo -e "${YELLOW}Verifying cleanup of AWS resources...${NC}"
echo "Waiting 10 seconds to allow final termination steps to complete..."
sleep 10  # Give AWS a moment to process any termination requests

# Run the verify cleanup script with the same profile
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$SCRIPT_DIR/verify_cleanup.sh" "$1"

# Final reminder
echo ""
echo -e "${YELLOW}Test process complete.${NC}"
echo "Double-check your AWS Console to make sure no resources were left running."

# Exit with the original test status
exit $TEST_STATUS 