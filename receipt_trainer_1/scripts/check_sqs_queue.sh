#!/bin/bash
# Script to check the status of the SQS queue

set -e # Exit on error
set -o pipefail

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Default environment
ENV=${1:-dev}

echo -e "${YELLOW}Checking SQS queue for environment: ${ENV}${NC}"

# Get the queue URL from Pulumi stack
get_queue_url() {
    local env=$1
    python -c "from receipt_dynamo.data._pulumi import load_env; env_vars = load_env('$env'); print(env_vars.get('job_queue_url', ''))"
}

QUEUE_URL=$(get_queue_url $ENV)

if [ -z "$QUEUE_URL" ]; then
    echo -e "${RED}Error: Could not find queue URL for environment: $ENV${NC}"
    exit 1
fi

echo -e "Queue URL: $QUEUE_URL"

# Check the queue attributes
echo -e "${YELLOW}Checking queue attributes...${NC}"
aws sqs get-queue-attributes \
    --queue-url "$QUEUE_URL" \
    --attribute-names All

# Check message counts
echo -e "\n${YELLOW}Message counts:${NC}"
VISIBLE=$(aws sqs get-queue-attributes \
    --queue-url "$QUEUE_URL" \
    --attribute-names ApproximateNumberOfMessages \
    --query 'Attributes.ApproximateNumberOfMessages' \
    --output text)

IN_FLIGHT=$(aws sqs get-queue-attributes \
    --queue-url "$QUEUE_URL" \
    --attribute-names ApproximateNumberOfMessagesNotVisible \
    --query 'Attributes.ApproximateNumberOfMessagesNotVisible' \
    --output text)

DELAYED=$(aws sqs get-queue-attributes \
    --queue-url "$QUEUE_URL" \
    --attribute-names ApproximateNumberOfMessagesDelayed \
    --query 'Attributes.ApproximateNumberOfMessagesDelayed' \
    --output text)

echo -e "Available messages: ${GREEN}$VISIBLE${NC}"
echo -e "In-flight messages: ${YELLOW}$IN_FLIGHT${NC}"
echo -e "Delayed messages: ${YELLOW}$DELAYED${NC}"

# If this is a FIFO queue, check message groups
if [[ "$QUEUE_URL" == *".fifo" ]]; then
    echo -e "\n${YELLOW}This is a FIFO queue${NC}"
    echo -e "${YELLOW}Note: FIFO queues deliver messages in order and may have message group locks${NC}"
    echo -e "${YELLOW}If you're having issues with messages not being received, one group might be locked${NC}"
fi

# Check if SQS has a consumer
echo -e "\n${YELLOW}Checking if there are active consumers...${NC}"
# This is an indirect way to check - if there are in-flight messages, there's probably a consumer
if [ "$IN_FLIGHT" -gt 0 ]; then
    echo -e "${YELLOW}There appear to be active consumers (in-flight messages detected)${NC}"
else
    echo -e "${GREEN}No in-flight messages detected, likely no active consumers${NC}"
fi

echo -e "\n${GREEN}Queue check completed.${NC}"
