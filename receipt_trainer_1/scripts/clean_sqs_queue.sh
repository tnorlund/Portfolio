#!/bin/bash
# Script to clean up SQS queue by purging all messages

set -e # Exit on error
set -o pipefail

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Default environment
ENV=${1:-dev}

echo -e "${YELLOW}Cleaning SQS queue for environment: ${ENV}${NC}"

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

# Purge the queue
echo -e "${YELLOW}Purging all messages from the queue...${NC}"
aws sqs purge-queue --queue-url "$QUEUE_URL"

# Check the queue attributes to verify
echo -e "${YELLOW}Checking queue attributes...${NC}"
aws sqs get-queue-attributes \
    --queue-url "$QUEUE_URL" \
    --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible ApproximateNumberOfMessagesDelayed

echo -e "${GREEN}SQS queue cleanup completed successfully!${NC}"
echo -e "${YELLOW}Note: FIFO queues may take up to 60 seconds to fully purge all messages.${NC}"

# List any in-flight messages (might still be being processed)
echo -e "${YELLOW}Checking for in-flight messages...${NC}"
IN_FLIGHT=$(aws sqs get-queue-attributes \
    --queue-url "$QUEUE_URL" \
    --attribute-names ApproximateNumberOfMessagesNotVisible \
    --query 'Attributes.ApproximateNumberOfMessagesNotVisible' \
    --output text)

if [ "$IN_FLIGHT" -gt 0 ]; then
    echo -e "${YELLOW}Warning: There are still $IN_FLIGHT in-flight messages.${NC}"
    echo -e "${YELLOW}These messages are currently being processed and will return to the queue if not deleted.${NC}"
    echo -e "${YELLOW}You may want to wait for these to complete or run this script again later.${NC}"
else
    echo -e "${GREEN}No in-flight messages detected. Queue is empty!${NC}"
fi
