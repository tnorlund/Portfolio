#!/bin/bash
# Trigger label harmonizer Step Function in LIVE mode (actually updates DynamoDB)
#
# ⚠️  WARNING: This will modify DynamoDB records!
#
# Usage:
#   ./scripts/trigger_harmonizer_live.sh --max-merchants 1 --label-types "MERCHANT_NAME" --confirm
#
# Required:
#   --confirm              Required flag to prevent accidental runs
#
# Options:
#   --max-merchants N      Limit to N merchants (REQUIRED for safety)
#   --label-types "X,Y"    Limit to specific label types (REQUIRED for safety)
#   --min-confidence N     Minimum confidence threshold (default: 75.0)
#   --max-concurrent-llm-calls N  Max concurrent LLM calls (default: 10)
#   --project NAME         LangSmith project name

set -e

# Default values
STACK="${STACK:-dev}"
PROJECT_NAME="${PROJECT_NAME:-label-harmonizer-live}"
MAX_MERCHANTS=""
LABEL_TYPES=""
MIN_CONFIDENCE="75.0"
MAX_CONCURRENT_LLM_CALLS="10"
CONFIRMED=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --confirm)
            CONFIRMED=true
            shift
            ;;
        --project)
            PROJECT_NAME="$2"
            shift 2
            ;;
        --max-merchants)
            MAX_MERCHANTS="$2"
            shift 2
            ;;
        --label-types)
            LABEL_TYPES="$2"
            shift 2
            ;;
        --min-confidence)
            MIN_CONFIDENCE="$2"
            shift 2
            ;;
        --max-concurrent-llm-calls)
            MAX_CONCURRENT_LLM_CALLS="$2"
            shift 2
            ;;
        --stack)
            STACK="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo ""
            echo "Usage: $0 --max-merchants N --label-types 'TYPE1,TYPE2' --confirm [options]"
            echo ""
            echo "Required:"
            echo "  --confirm              Required flag to prevent accidental runs"
            echo "  --max-merchants N      Limit to N merchants (required for safety)"
            echo "  --label-types 'X,Y'    Limit to specific label types (required for safety)"
            echo ""
            echo "Options:"
            echo "  --min-confidence N     Minimum confidence (default: 75.0)"
            echo "  --max-concurrent-llm-calls N  Max concurrent LLM calls (default: 10)"
            echo "  --project NAME         LangSmith project name"
            echo "  --stack STACK          Pulumi stack (default: dev)"
            exit 1
            ;;
    esac
done

# Safety checks
if [ "$CONFIRMED" != "true" ]; then
    echo "❌ Error: --confirm flag is required for live runs"
    echo ""
    echo "This script will MODIFY DynamoDB records!"
    echo "Add --confirm to proceed."
    exit 1
fi

# Allow "all" for both parameters to process everything
if [ -z "$MAX_MERCHANTS" ]; then
    echo "❌ Error: --max-merchants is required for live runs"
    echo ""
    echo "Example: --max-merchants 1"
    echo "         --max-merchants all  (process all merchants)"
    exit 1
fi

if [ -z "$LABEL_TYPES" ]; then
    echo "❌ Error: --label-types is required for live runs"
    echo ""
    echo "Example: --label-types 'MERCHANT_NAME'"
    echo "         --label-types 'MERCHANT_NAME,PRODUCT_NAME'"
    echo "         --label-types all  (process all label types)"
    exit 1
fi

echo "=========================================="
echo "⚠️  LABEL HARMONIZER LIVE RUN"
echo "=========================================="
echo "Stack: $STACK"
echo "Project: $PROJECT_NAME"
if [ "$MAX_MERCHANTS" = "all" ]; then
    echo "Max Merchants: ALL (no limit)"
else
    echo "Max Merchants: $MAX_MERCHANTS"
fi
if [ "$LABEL_TYPES" = "all" ]; then
    echo "Label Types: ALL (all CORE_LABELS)"
else
    echo "Label Types: $LABEL_TYPES"
fi
echo "Min Confidence: $MIN_CONFIDENCE"
echo "Max Concurrent LLM Calls: $MAX_CONCURRENT_LLM_CALLS"
echo "dry_run: FALSE - WILL MODIFY DYNAMODB"
echo ""

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Get Step Function ARN
echo ""
echo "Getting Step Function ARN..."
cd "$REPO_ROOT/infra"
SF_ARN=$(pulumi stack output label_harmonizer_sf_arn --stack "$STACK" 2>/dev/null || echo "")

if [ -z "$SF_ARN" ]; then
    echo "Error: Could not get Step Function ARN from Pulumi"
    exit 1
fi

echo "Step Function ARN: $SF_ARN"
echo ""

# Build input JSON
INPUT_JSON="{"
INPUT_JSON+="\"dry_run\": false"

# Handle max_merchants: "all" or empty means null (process all merchants)
if [ "$MAX_MERCHANTS" = "all" ] || [ -z "$MAX_MERCHANTS" ]; then
    INPUT_JSON+=", \"max_merchants\": null"
else
    INPUT_JSON+=", \"max_merchants\": $MAX_MERCHANTS"
fi

INPUT_JSON+=", \"min_confidence\": $MIN_CONFIDENCE"
INPUT_JSON+=", \"max_concurrent_llm_calls\": $MAX_CONCURRENT_LLM_CALLS"

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

# Trigger execution
echo ""
echo "Triggering Step Function execution..."
EXECUTION_ARN=$(aws stepfunctions start-execution \
    --state-machine-arn "$SF_ARN" \
    --input "$INPUT_JSON" \
    --query 'executionArn' \
    --output text 2>&1)

if [ $? -ne 0 ]; then
    echo "Error: Failed to start execution"
    echo "$EXECUTION_ARN"
    exit 1
fi

EXECUTION_NAME=$(echo "$EXECUTION_ARN" | cut -d: -f7)

echo ""
echo "✅ LIVE Execution started!"
echo ""
echo "Execution ARN: $EXECUTION_ARN"
echo "Execution Name: $EXECUTION_NAME"
echo ""
echo "Monitor execution:"
echo "  aws stepfunctions describe-execution --execution-arn $EXECUTION_ARN"
echo ""
echo "View in AWS Console:"
echo "  https://console.aws.amazon.com/states/home?region=us-east-1#/executions/details/$EXECUTION_ARN"
echo ""
echo "Check LangSmith:"
echo "  python -c \"from receipt_langsmith import summarize_project; print(summarize_project('$PROJECT_NAME'))\""

