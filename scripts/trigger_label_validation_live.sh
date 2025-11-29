#!/bin/bash
# Trigger label validation agent Step Function in LIVE mode (actually updates DynamoDB)
# Processes all NEEDS_REVIEW labels using the Label Validation Agent

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=========================================="
echo "🚀 LABEL VALIDATION AGENT - LIVE MODE"
echo "=========================================="
echo ""
echo "⚠️  WARNING: This will ACTUALLY UPDATE DynamoDB records!"
echo "   - dry_run: false"
echo "   - Will update validation_status and reasoning for NEEDS_REVIEW labels"
echo ""

# Get Pulumi stack (default to 'dev')
STACK="${1:-dev}"
echo "Stack: $STACK"
echo ""

# Get Step Function ARN (try Pulumi first, then AWS CLI)
echo "Fetching Step Function ARN..."
SF_ARN=$(pulumi stack output --stack "$STACK" label_validation_agent_sf_arn 2>/dev/null || echo "")

# Fallback: find via AWS CLI
if [ -z "$SF_ARN" ]; then
    echo "Trying AWS CLI..."
    SF_ARN=$(aws stepfunctions list-state-machines --region us-east-1 \
        --query "stateMachines[?contains(name, 'label-validation-agent') && contains(name, '$STACK')].stateMachineArn | [0]" \
        --output text 2>/dev/null)
fi

# Final fallback: any label-validation-agent
if [ -z "$SF_ARN" ] || [ "$SF_ARN" = "None" ]; then
    SF_ARN=$(aws stepfunctions list-state-machines --region us-east-1 \
        --query "stateMachines[?contains(name, 'label-validation-agent')].stateMachineArn | [0]" \
        --output text 2>/dev/null)
fi

if [ -z "$SF_ARN" ] || [ "$SF_ARN" = "None" ]; then
    echo -e "${RED}Error: Could not find label validation agent Step Function${NC}"
    exit 1
fi

echo "Step Function ARN: $SF_ARN"
echo ""

# Parse optional parameters
DRY_RUN="${2:-false}"
LABEL_TYPES="${3:-all}"  # "all" means null (process all label types)
MAX_LABELS="${4:-}"      # Empty means no limit (process all)
MIN_CONFIDENCE="${5:-0.8}"
PROJECT_NAME="${6:-label-validation-agent-live}"

# Validate dry_run
if [ "$DRY_RUN" != "false" ] && [ "$DRY_RUN" != "true" ]; then
    echo -e "${RED}Error: dry_run must be 'true' or 'false'${NC}"
    exit 1
fi

if [ "$DRY_RUN" = "true" ]; then
    echo -e "${YELLOW}⚠️  DRY RUN MODE - No DynamoDB updates will be made${NC}"
else
    echo -e "${GREEN}✅ LIVE MODE - DynamoDB will be updated${NC}"
fi
echo ""

# Build input JSON
INPUT_JSON="{"
INPUT_JSON+="\"dry_run\": $DRY_RUN"
INPUT_JSON+=", \"min_confidence\": $MIN_CONFIDENCE"

# Handle max_labels: empty means null (no limit)
if [ -n "$MAX_LABELS" ]; then
    INPUT_JSON+=", \"max_labels\": $MAX_LABELS"
else
    INPUT_JSON+=", \"max_labels\": null"
fi

# Handle label_types: "all" or empty means null (process all label types)
if [ "$LABEL_TYPES" = "all" ] || [ -z "$LABEL_TYPES" ]; then
    INPUT_JSON+=", \"label_types\": null"
else
    # Convert comma-separated label types to JSON array
    IFS=',' read -ra TYPES <<< "$LABEL_TYPES"
    INPUT_JSON+=", \"label_types\": ["
    for i in "${!TYPES[@]}"; do
        if [ $i -gt 0 ]; then
            INPUT_JSON+=", "
        fi
        INPUT_JSON+="\"${TYPES[$i]}\""
    done
    INPUT_JSON+="]"
fi

INPUT_JSON+=", \"langchain_project\": \"$PROJECT_NAME\""
INPUT_JSON+="}"

echo "Input JSON:"
echo "$INPUT_JSON" | jq '.' 2>/dev/null || echo "$INPUT_JSON"
echo ""

# Confirm before proceeding
if [ "$DRY_RUN" = "false" ]; then
    echo ""
    echo -e "${YELLOW}⚠️  FINAL CONFIRMATION REQUIRED${NC}"
    echo "This will update DynamoDB records for all NEEDS_REVIEW labels."
    echo "Press ENTER to continue, or Ctrl+C to cancel..."
    read -r
fi

# Trigger execution
echo ""
echo "Triggering Step Function execution..."
EXECUTION_ARN=$(aws stepfunctions start-execution \
    --state-machine-arn "$SF_ARN" \
    --input "$INPUT_JSON" \
    --query 'executionArn' \
    --output text 2>&1)

if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Failed to start execution${NC}"
    echo "$EXECUTION_ARN"
    exit 1
fi

EXECUTION_NAME=$(echo "$EXECUTION_ARN" | cut -d: -f7)

echo ""
echo -e "${GREEN}✅ Execution started!${NC}"
echo ""
echo "Execution ARN: $EXECUTION_ARN"
echo "Execution Name: $EXECUTION_NAME"
echo ""
echo "Monitor execution:"
echo "  aws stepfunctions describe-execution --execution-arn $EXECUTION_ARN"
echo ""
echo "View in AWS Console:"
REGION=$(echo "$SF_ARN" | cut -d: -f4)
echo "  https://console.aws.amazon.com/states/home?region=$REGION#/executions/details/$EXECUTION_ARN"
echo ""
echo "Check LangSmith:"
echo "  python -c \"from receipt_langsmith import summarize_project; print(summarize_project('$PROJECT_NAME'))\""
echo ""
echo "Check metrics:"
echo "  ./scripts/check_label_validation_metrics.sh 24"
echo ""

