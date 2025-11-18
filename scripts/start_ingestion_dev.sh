#!/bin/bash

# Script to start ingestion workflows on the dev stack
# Usage: ./scripts/start_ingestion_dev.sh [line|word|both] [--continuous]

# Don't use set -e in continuous mode as we want to handle errors gracefully

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
INFRA_DIR="$PROJECT_ROOT/infra"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

# Parse arguments
WORKFLOW_TYPE="both"
CONTINUOUS=false

for arg in "$@"; do
    case $arg in
        line|word|both)
            WORKFLOW_TYPE="$arg"
            ;;
        --continuous|-c)
            CONTINUOUS=true
            ;;
        --help|-h)
            echo "Usage: $0 [line|word|both] [--continuous]"
            echo ""
            echo "Arguments:"
            echo "  line|word|both    Which ingestion workflow(s) to start (default: both)"
            echo "  --continuous, -c   Run continuously, restarting workflows when they complete"
            echo ""
            echo "Examples:"
            echo "  $0                    # Start both workflows once"
            echo "  $0 line               # Start only line ingestion once"
            echo "  $0 --continuous       # Start both workflows continuously"
            echo "  $0 word --continuous  # Start word ingestion continuously"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown argument: $arg${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

echo -e "${BLUE}🚀 Starting Ingestion Workflows on Dev Stack${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

cd "$INFRA_DIR"

# Get Step Function ARNs - try Pulumi first, then AWS CLI
echo -e "${YELLOW}Fetching Step Function ARNs...${NC}"

# Try Pulumi first
PULUMI_OUTPUTS=$(pulumi stack output --stack tnorlund/portfolio/dev --json 2>&1)
if [ $? -eq 0 ]; then
    LINE_INGEST_ARN=$(echo "$PULUMI_OUTPUTS" | python3 -c "import sys, json; data = json.load(sys.stdin); val = data.get('embedding_line_ingest_sf_arn'); print(val.get('value', '') if isinstance(val, dict) else val if val else '')" 2>/dev/null)
    WORD_INGEST_ARN=$(echo "$PULUMI_OUTPUTS" | python3 -c "import sys, json; data = json.load(sys.stdin); val = data.get('embedding_word_ingest_sf_arn'); print(val.get('value', '') if isinstance(val, dict) else val if val else '')" 2>/dev/null)
fi

# Fallback to AWS CLI if Pulumi didn't work
if [ -z "$LINE_INGEST_ARN" ] || [ -z "$WORD_INGEST_ARN" ]; then
    echo -e "${YELLOW}Using AWS CLI to find Step Functions...${NC}"
    if [ -z "$LINE_INGEST_ARN" ]; then
        LINE_INGEST_ARN=$(aws stepfunctions list-state-machines --query 'stateMachines[?contains(name, `line-ingest`) && contains(name, `dev`)].stateMachineArn' --output text 2>/dev/null | head -1)
    fi
    if [ -z "$WORD_INGEST_ARN" ]; then
        WORD_INGEST_ARN=$(aws stepfunctions list-state-machines --query 'stateMachines[?contains(name, `word-ingest`) && contains(name, `dev`)].stateMachineArn' --output text 2>/dev/null | head -1)
    fi
fi

if [ -z "$LINE_INGEST_ARN" ] && [ "$WORKFLOW_TYPE" != "word" ]; then
    echo -e "${RED}❌ Could not find line ingestion Step Function ARN${NC}"
    exit 1
fi

if [ -z "$WORD_INGEST_ARN" ] && [ "$WORKFLOW_TYPE" != "line" ]; then
    echo -e "${RED}❌ Could not find word ingestion Step Function ARN${NC}"
    exit 1
fi

# Function to start a workflow
start_workflow() {
    local ARN=$1
    local TYPE=$2

    if [ -z "$ARN" ]; then
        echo -e "${YELLOW}⚠️  Skipping $TYPE ingestion (ARN not found)${NC}"
        echo ""
        return
    fi

    local TIMESTAMP=$(date +%s)
    local EXECUTION_NAME="manual-run-$TIMESTAMP-$TYPE"

    echo -e "${GREEN}Starting $TYPE ingestion workflow...${NC}"
    echo "  ARN: $ARN"
    echo "  Execution Name: $EXECUTION_NAME"

    local EXECUTION_ARN=$(aws stepfunctions start-execution \
        --state-machine-arn "$ARN" \
        --name "$EXECUTION_NAME" \
        --query 'executionArn' \
        --output text 2>&1)

    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ $TYPE ingestion started successfully!${NC}"
        echo "  Execution ARN: $EXECUTION_ARN"
        echo "$EXECUTION_ARN"
    else
        echo -e "${RED}❌ Failed to start $TYPE ingestion${NC}"
        echo "Error: $EXECUTION_ARN"
        return 1
    fi
}

# Function to check execution status
check_execution_status() {
    local EXECUTION_ARN=$1
    aws stepfunctions describe-execution \
        --execution-arn "$EXECUTION_ARN" \
        --query 'status' \
        --output text 2>/dev/null
}

# Function to wait for execution to complete
wait_for_execution() {
    local EXECUTION_ARN=$1
    local TYPE=$2

    echo -e "${CYAN}Waiting for $TYPE ingestion to complete...${NC}"

    while true; do
        local STATUS=$(check_execution_status "$EXECUTION_ARN")

        case "$STATUS" in
            RUNNING)
                echo -e "${CYAN}  $TYPE ingestion still running... (checking again in 30s)${NC}"
                sleep 30
                ;;
            SUCCEEDED)
                echo -e "${GREEN}✅ $TYPE ingestion completed successfully!${NC}"
                return 0
                ;;
            FAILED|TIMED_OUT|ABORTED)
                echo -e "${RED}❌ $TYPE ingestion failed with status: $STATUS${NC}"
                echo "  Execution ARN: $EXECUTION_ARN"
                echo "  Check details: aws stepfunctions describe-execution --execution-arn \"$EXECUTION_ARN\""
                return 1
                ;;
            *)
                echo -e "${YELLOW}⚠️  Unknown status for $TYPE ingestion: $STATUS${NC}"
                sleep 30
                ;;
        esac
    done
}

# Start workflows
if [ "$CONTINUOUS" = true ]; then
    echo -e "${CYAN}Running in continuous mode - workflows will restart automatically when they complete${NC}"
    echo -e "${YELLOW}Press Ctrl+C to stop${NC}"
    echo ""

    # Track execution ARNs
    LINE_EXECUTION_ARN=""
    WORD_EXECUTION_ARN=""
    ITERATION=0

    # Trap Ctrl+C to exit gracefully
    trap 'echo ""; echo -e "${YELLOW}Stopping continuous mode...${NC}"; exit 0' INT

    while true; do
        ITERATION=$((ITERATION + 1))
        echo -e "${BLUE}=== Iteration $ITERATION ===${NC}"
        echo ""

        # Start workflows based on type
        case "$WORKFLOW_TYPE" in
            line)
                if [ -z "$LINE_EXECUTION_ARN" ] || [ "$(check_execution_status "$LINE_EXECUTION_ARN" 2>/dev/null)" != "RUNNING" ]; then
                    LINE_EXECUTION_ARN=$(start_workflow "$LINE_INGEST_ARN" "line")
                    echo ""
                fi
                wait_for_execution "$LINE_EXECUTION_ARN" "line"
                LINE_EXECUTION_ARN=""  # Reset to restart
                echo ""
                ;;
            word)
                if [ -z "$WORD_EXECUTION_ARN" ] || [ "$(check_execution_status "$WORD_EXECUTION_ARN" 2>/dev/null)" != "RUNNING" ]; then
                    WORD_EXECUTION_ARN=$(start_workflow "$WORD_INGEST_ARN" "word")
                    echo ""
                fi
                wait_for_execution "$WORD_EXECUTION_ARN" "word"
                WORD_EXECUTION_ARN=""  # Reset to restart
                echo ""
                ;;
            both)
                # Start both if not already running
                if [ -z "$LINE_EXECUTION_ARN" ] || [ "$(check_execution_status "$LINE_EXECUTION_ARN" 2>/dev/null)" != "RUNNING" ]; then
                    LINE_EXECUTION_ARN=$(start_workflow "$LINE_INGEST_ARN" "line")
                    echo ""
                fi
                if [ -z "$WORD_EXECUTION_ARN" ] || [ "$(check_execution_status "$WORD_EXECUTION_ARN" 2>/dev/null)" != "RUNNING" ]; then
                    WORD_EXECUTION_ARN=$(start_workflow "$WORD_INGEST_ARN" "word")
                    echo ""
                fi

                # Wait for both to complete
                wait_for_execution "$LINE_EXECUTION_ARN" "line" &
                LINE_PID=$!
                wait_for_execution "$WORD_EXECUTION_ARN" "word" &
                WORD_PID=$!

                wait $LINE_PID
                LINE_EXECUTION_ARN=""  # Reset to restart
                wait $WORD_PID
                WORD_EXECUTION_ARN=""  # Reset to restart
                echo ""
                ;;
        esac

        echo -e "${CYAN}Waiting 5 seconds before restarting...${NC}"
        sleep 5
        echo ""
    done
else
    # One-time execution
    case "$WORKFLOW_TYPE" in
        line)
            EXECUTION_ARN=$(start_workflow "$LINE_INGEST_ARN" "line")
            if [ -n "$EXECUTION_ARN" ]; then
                echo ""
                echo "  Monitor execution:"
                echo "    aws stepfunctions describe-execution --execution-arn \"$EXECUTION_ARN\""
            fi
            ;;
        word)
            EXECUTION_ARN=$(start_workflow "$WORD_INGEST_ARN" "word")
            if [ -n "$EXECUTION_ARN" ]; then
                echo ""
                echo "  Monitor execution:"
                echo "    aws stepfunctions describe-execution --execution-arn \"$EXECUTION_ARN\""
            fi
            ;;
        both)
            LINE_EXECUTION_ARN=$(start_workflow "$LINE_INGEST_ARN" "line")
            echo ""
            WORD_EXECUTION_ARN=$(start_workflow "$WORD_INGEST_ARN" "word")
            if [ -n "$LINE_EXECUTION_ARN" ] || [ -n "$WORD_EXECUTION_ARN" ]; then
                echo ""
                echo "  Monitor executions:"
                [ -n "$LINE_EXECUTION_ARN" ] && echo "    Line: aws stepfunctions describe-execution --execution-arn \"$LINE_EXECUTION_ARN\""
                [ -n "$WORD_EXECUTION_ARN" ] && echo "    Word: aws stepfunctions describe-execution --execution-arn \"$WORD_EXECUTION_ARN\""
            fi
            ;;
        *)
            echo -e "${RED}❌ Invalid workflow type: $WORKFLOW_TYPE${NC}"
            echo "Usage: $0 [line|word|both] [--continuous]"
            exit 1
            ;;
    esac

    echo ""
    echo -e "${GREEN}🎉 Done!${NC}"
fi

