#!/bin/bash
# Script to verify no EC2 instances or spot requests were left by the tests

set -e  # Exit on error

# Set colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

echo "Verifying that no test resources were left running..."

# Set AWS profile if provided
if [ -n "$1" ]; then
    export AWS_PROFILE="$1"
    echo -e "${YELLOW}Using AWS profile: $AWS_PROFILE${NC}"
fi

# Check for running instances with AutoScalingManager tag
echo "Checking for EC2 instances..."
INSTANCES=$(aws ec2 describe-instances \
    --filters "Name=tag:ManagedBy,Values=AutoScalingManager" \
    --query "Reservations[*].Instances[?State.Name!='terminated'].{InstanceId:InstanceId,State:State.Name,Type:InstanceType}" \
    --output json)

INSTANCE_COUNT=$(echo $INSTANCES | jq '. | flatten | length')

if [ "$INSTANCE_COUNT" -gt 0 ]; then
    echo -e "${RED}Found $INSTANCE_COUNT instances still running:${NC}"
    echo "$INSTANCES" | jq -r '.[][]? | "  - " + .InstanceId + " (" + .Type + "): " + .State'

    # Ask if user wants to terminate them
    read -p "Do you want to terminate these instances? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        INSTANCE_IDS=$(echo $INSTANCES | jq -r '.[][]? | .InstanceId' | tr '\n' ' ')
        echo "Terminating instances: $INSTANCE_IDS"
        aws ec2 terminate-instances --instance-ids $INSTANCE_IDS
        echo "Termination initiated. Wait a few moments for instances to fully terminate."
    fi
else
    echo -e "${GREEN}No EC2 instances found with AutoScalingManager tag.${NC}"
fi

# Check for active spot instance requests
echo "Checking for spot instance requests..."
SPOT_REQUESTS=$(aws ec2 describe-spot-instance-requests \
    --filters "Name=state,Values=open,active" \
    --query "SpotInstanceRequests[*].{SpotInstanceRequestId:SpotInstanceRequestId,State:State,InstanceId:InstanceId}" \
    --output json)

SPOT_REQUEST_COUNT=$(echo $SPOT_REQUESTS | jq '. | length')

if [ "$SPOT_REQUEST_COUNT" -gt 0 ]; then
    echo -e "${RED}Found $SPOT_REQUEST_COUNT active spot requests:${NC}"
    echo "$SPOT_REQUESTS" | jq -r '.[] | "  - " + .SpotInstanceRequestId + ": " + .State.Name + (if .InstanceId then " (Instance: " + .InstanceId + ")" else "" end)'

    # Ask if user wants to cancel them
    read -p "Do you want to cancel these spot requests? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        SPOT_REQUEST_IDS=$(echo $SPOT_REQUESTS | jq -r '.[].SpotInstanceRequestId' | tr '\n' ' ')
        echo "Cancelling spot requests: $SPOT_REQUEST_IDS"
        aws ec2 cancel-spot-instance-requests --spot-instance-request-ids $SPOT_REQUEST_IDS
        echo "Spot requests cancelled."
    fi
else
    echo -e "${GREEN}No active spot instance requests found.${NC}"
fi

echo "Cleanup verification complete."

# Provide additional details if instances or spot requests were found
if [ "$INSTANCE_COUNT" -gt 0 ] || [ "$SPOT_REQUEST_COUNT" -gt 0 ]; then
    echo -e "${YELLOW}Note: Found resources may be from other tests or applications.${NC}"
    echo "Always verify in the AWS Console if unsure."
else
    echo -e "${GREEN}All test resources have been properly cleaned up!${NC}"
fi
