#!/bin/bash
# Trigger label harmonizer Step Function in dry run mode with all CORE_LABELS
#
# Usage:
#   ./scripts/trigger_harmonizer_dry_run.sh
#   ./scripts/trigger_harmonizer_dry_run.sh --max-merchants 10
#   ./scripts/trigger_harmonizer_dry_run.sh --project label-harmonizer-v2 --max-merchants 5

set -e

# Default values
STACK="${STACK:-dev}"
PROJECT_NAME="${PROJECT_NAME:-label-harmonizer-v2}"
MAX_MERCHANTS="${MAX_MERCHANTS:-}"
LABEL_TYPES="${LABEL_TYPES:-}"  # Empty = all CORE_LABELS

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
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
        --stack)
            STACK="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--project PROJECT] [--max-merchants N] [--label-types 'TYPE1,TYPE2'] [--stack STACK]"
            exit 1
            ;;
    esac
done

echo "=========================================="
echo "Label Harmonizer Dry Run"
echo "=========================================="
echo "Stack: $STACK"
echo "Project: $PROJECT_NAME"
echo "Max Merchants: ${MAX_MERCHANTS:-unlimited}"
echo "Label Types: ${LABEL_TYPES:-all CORE_LABELS}"
echo ""

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Set project name in Pulumi config
echo "Setting LangSmith project name in Pulumi config..."
cd "$REPO_ROOT/infra"
pulumi config set langchain_project "$PROJECT_NAME" --stack "$STACK" || {
    echo "Warning: Failed to set langchain_project config. Continuing..."
}

# Get Step Function ARN
echo "Getting Step Function ARN..."
SF_ARN=$(pulumi stack output label_harmonizer_sf_arn --stack "$STACK" 2>/dev/null || echo "")

if [ -z "$SF_ARN" ]; then
    echo "Error: Could not get Step Function ARN from Pulumi"
    echo "Make sure you're in the correct stack and the infrastructure is deployed"
    exit 1
fi

echo "Step Function ARN: $SF_ARN"
echo ""

# Build input JSON
# Always include optional fields (even if null) to avoid JSONPath errors
INPUT_JSON="{"
INPUT_JSON+="\"dry_run\": true"

# Always include max_merchants (null if not specified)
if [ -n "$MAX_MERCHANTS" ]; then
    INPUT_JSON+=", \"max_merchants\": $MAX_MERCHANTS"
else
    INPUT_JSON+=", \"max_merchants\": null"
fi

# Always include label_types (null if not specified, will default to all CORE_LABELS)
if [ -n "$LABEL_TYPES" ]; then
    # Convert comma-separated to JSON array
    IFS=',' read -ra TYPES <<< "$LABEL_TYPES"
    INPUT_JSON+=", \"label_types\": ["
    for i in "${!TYPES[@]}"; do
        if [ $i -gt 0 ]; then
            INPUT_JSON+=", "
        fi
        INPUT_JSON+="\"${TYPES[$i]}\""
    done
    INPUT_JSON+="]"
else
    INPUT_JSON+=", \"label_types\": null"
fi

# Always include langchain_project (null if not specified, will default to env var or "label-harmonizer")
if [ -n "$PROJECT_NAME" ]; then
    INPUT_JSON+=", \"langchain_project\": \"$PROJECT_NAME\""
else
    INPUT_JSON+=", \"langchain_project\": null"
fi

INPUT_JSON+="}"

echo "Input JSON:"
echo "$INPUT_JSON" | jq '.' 2>/dev/null || echo "$INPUT_JSON"
echo ""

# Trigger execution
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

echo "✅ Execution started!"
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
echo "After completion, analyze edge cases:"
echo "  python scripts/analyze_langsmith_edge_cases.py --project $PROJECT_NAME --hours 2"

