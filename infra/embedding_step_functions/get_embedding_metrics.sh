#!/bin/bash

# Embedding Step Functions Metrics Collection Script
# Collects metrics for the embedding step functions infrastructure

set -euo pipefail

# Help message
if [[ "${1:-}" =~ ^(-h|--help)$ ]]; then
    cat <<EOF
Embedding Step Functions Metrics Collection Script

Usage: $0 [hours_back] [output_file] [pulumi_stack]
       $0 -h|--help

Parameters:
  hours_back    Number of hours to look back (default: 6, must be positive integer)
  output_file   Save output to file instead of stdout (optional)
  pulumi_stack  Pulumi stack to use: 'dev' or 'prod' (default: current active stack)

Environment Variables (override resource names):
  PULUMI_STACK           Pulumi stack to use (dev/prod)
  ENHANCED_COMPACTION    Lambda function name for enhanced compaction
  CHROMADB_BUCKET        S3 bucket name for ChromaDB storage
  STEP_FUNCTION_ARN      Step function ARN to monitor

Examples:
  $0                           # Last 6 hours, current stack
  $0 12                        # Last 12 hours, current stack
  $0 24 embedding_report.txt   # Last 24 hours to file, current stack
  $0 6 - dev                   # Last 6 hours, dev stack (- = stdout)
  $0 24 prod_report.txt prod   # Last 24 hours to file, prod stack
  PULUMI_STACK=prod $0 12      # Override stack via environment

EOF
    exit 0
fi

# Parse command line arguments
HOURS_BACK=${1:-6}
OUTPUT_FILE=${2:-""}
PULUMI_STACK=${3:-${PULUMI_STACK:-""}}  # Third param or env var
REGION="us-east-1"
PERIOD=3600

# Handle special case where output_file is "-" (stdout)
if [ "$OUTPUT_FILE" = "-" ]; then
    OUTPUT_FILE=""
fi

# Redirect output to file if specified
if [ -n "$OUTPUT_FILE" ]; then
    # Check if directory exists and is writable
    OUTPUT_DIR=$(dirname "$OUTPUT_FILE")
    if [ ! -d "$OUTPUT_DIR" ] || [ ! -w "$OUTPUT_DIR" ]; then
        echo "Error: Output directory '$OUTPUT_DIR' does not exist or is not writable" >&2
        exit 2
    fi
    # Redirect all output to file
    exec > "$OUTPUT_FILE" 2>&1
fi

# Get resource names from Pulumi outputs (can be overridden via environment variables)
echo "Fetching embedding infrastructure resource names from Pulumi stack outputs..."

# Try to get Pulumi outputs, with fallback to hardcoded values if Pulumi isn't available
if command -v pulumi >/dev/null 2>&1; then
    # Use specified stack or current active stack
    if [ -n "$PULUMI_STACK" ]; then
        echo "Using Pulumi stack: $PULUMI_STACK"
        PULUMI_OUTPUTS=$(pulumi stack output --stack "$PULUMI_STACK" --json 2>/dev/null)
    else
        CURRENT_STACK=$(pulumi stack --show-name 2>/dev/null || echo "unknown")
        echo "Using current Pulumi stack: $CURRENT_STACK"
        PULUMI_OUTPUTS=$(pulumi stack output --json 2>/dev/null)
    fi
    if [ $? -eq 0 ] && [ -n "$PULUMI_OUTPUTS" ]; then
        # Extract resource names from Pulumi outputs
        if command -v jq >/dev/null 2>&1; then
            # Get step function ARN
            STEP_FUNCTION_ARN=$(echo "$PULUMI_OUTPUTS" | jq -r '.enhanced_receipt_processor_arn // empty')
            
            # Get function ARNs and extract names
            ENHANCED_COMPACTION_ARN=$(echo "$PULUMI_OUTPUTS" | jq -r '.enhanced_compaction_function_arn // empty')
            ENHANCED_COMPACTION_DEFAULT=$(echo "$ENHANCED_COMPACTION_ARN" | grep -o '[^/]*$')
            
            # Get S3 bucket name
            CHROMADB_BUCKET_NAME=$(echo "$PULUMI_OUTPUTS" | jq -r '.embedding_chromadb_bucket_name // empty')
        else
            # Fallback without jq
            STEP_FUNCTION_ARN=$(echo "$PULUMI_OUTPUTS" | grep -o '"enhanced_receipt_processor_arn":"[^"]*' | cut -d'"' -f4)
            ENHANCED_COMPACTION_ARN=$(echo "$PULUMI_OUTPUTS" | grep -o '"enhanced_compaction_function_arn":"[^"]*' | cut -d'"' -f4)
            ENHANCED_COMPACTION_DEFAULT=$(echo "$ENHANCED_COMPACTION_ARN" | grep -o '[^/]*$')
            CHROMADB_BUCKET_NAME=$(echo "$PULUMI_OUTPUTS" | grep -o '"embedding_chromadb_bucket_name":"[^"]*' | cut -d'"' -f4)
        fi
        
        echo "✓ Successfully retrieved embedding infrastructure resource names from Pulumi"
    else
        echo "⚠ Pulumi stack outputs unavailable, using fallback detection methods"
    fi
else
    echo "⚠ Pulumi not available, using fallback detection methods"
fi

# Resource names with Pulumi defaults (can still be overridden via environment variables)
ENHANCED_COMPACTION="${ENHANCED_COMPACTION:-${ENHANCED_COMPACTION_DEFAULT:-chromadb-dev-lambdas-enhanced-compaction-79f6426}}"
CHROMADB_BUCKET="${CHROMADB_BUCKET:-${CHROMADB_BUCKET_NAME:-chromadb-dev-shared-buckets-vectors-c239843}}"
STEP_FUNCTION_ARN="${STEP_FUNCTION_ARN:-arn:aws:states:us-east-1:681647709217:stateMachine:receipt_processor_enhanced_step_function-bc8ffad}"

# AWS Configuration
export AWS_DEFAULT_REGION="${REGION}"
export AWS_PAGER=""

# Time calculation
if [[ "$OSTYPE" == "darwin"* ]]; then
    START_TIME=$(date -u -v-${HOURS_BACK}H +%Y-%m-%dT%H:%M:%S)
    END_TIME=$(date -u +%Y-%m-%dT%H:%M:%S)
else
    START_TIME=$(date -u -d "${HOURS_BACK} hours ago" +%Y-%m-%dT%H:%M:%S)
    END_TIME=$(date -u +%Y-%m-%dT%H:%M:%S)
fi

echo "=== Embedding Step Functions Metrics ==="
echo "Time Range: ${START_TIME} to ${END_TIME} (${HOURS_BACK} hours)"
echo "Generated: $(date)"
echo ""

# Step Functions
echo "=== STEP FUNCTIONS ==="
echo "Recent Step Function Executions:"
aws stepfunctions list-state-machines --query 'stateMachines[?contains(name, `receipt_processor`)].{Name:name,Arn:stateMachineArn}' --output table

echo ""

# Check recent executions for the main step function (from Pulumi outputs)
if [ -n "$STEP_FUNCTION_ARN" ]; then
    sf_name=$(echo "$STEP_FUNCTION_ARN" | cut -d: -f7)
    echo "Recent executions for enhanced step function (${sf_name}):"
    aws stepfunctions list-executions --state-machine-arn "$STEP_FUNCTION_ARN" --max-items 5 --query "executions[].{name:name,status:status,start:startDate,end:stopDate}" --output table 2>/dev/null || echo "No recent executions"
    echo ""
else
    echo "No step function ARN found in Pulumi outputs"
    echo ""
fi

# Also check all step functions for completeness
echo "All receipt processor step functions:"
for sf_arn in $(aws stepfunctions list-state-machines --query 'stateMachines[?contains(name, `receipt_processor`)].stateMachineArn' --output text); do
    sf_name=$(echo $sf_arn | cut -d: -f7)
    echo "Recent executions for ${sf_name}:"
    aws stepfunctions list-executions --state-machine-arn "$sf_arn" --max-items 3 --query "executions[].{name:name,status:status,start:startDate}" --output table 2>/dev/null || echo "No recent executions"
    echo ""
done

echo "=== EMBEDDING LAMBDA FUNCTIONS ==="
echo "Recent Lambda Invocations (last ${HOURS_BACK} hours):"

# Get the most recently updated embedding functions
recent_functions=$(aws lambda list-functions --query 'Functions[?contains(FunctionName, `embedding`) && LastModified > `2025-08-27T00:00:00`].FunctionName' --output text)

for func_name in $recent_functions; do
    echo ""
    echo "${func_name}:"
    echo "  Invocations:"
    aws cloudwatch get-metric-statistics \
      --namespace AWS/Lambda \
      --metric-name Invocations \
      --dimensions Name=FunctionName,Value=${func_name} \
      --start-time ${START_TIME} \
      --end-time ${END_TIME} \
      --period ${PERIOD} \
      --statistics Sum \
      --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
      --output table 2>/dev/null || echo "    No invocations"

    echo "  Average Duration (ms):"
    aws cloudwatch get-metric-statistics \
      --namespace AWS/Lambda \
      --metric-name Duration \
      --dimensions Name=FunctionName,Value=${func_name} \
      --start-time ${START_TIME} \
      --end-time ${END_TIME} \
      --period ${PERIOD} \
      --statistics Average \
      --query 'sort_by(Datapoints[?Average != `0`], &Timestamp)[].[Timestamp,Average]' \
      --output table 2>/dev/null || echo "    No duration metrics"

    echo "  Errors:"
    aws cloudwatch get-metric-statistics \
      --namespace AWS/Lambda \
      --metric-name Errors \
      --dimensions Name=FunctionName,Value=${func_name} \
      --start-time ${START_TIME} \
      --end-time ${END_TIME} \
      --period ${PERIOD} \
      --statistics Sum \
      --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
      --output table 2>/dev/null || echo "    No errors (good!)"
done

echo ""
echo "=== COMPACTION FUNCTION METRICS ==="
echo "Enhanced Compaction Function: ${ENHANCED_COMPACTION}"
echo "Recent Invocations:"
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Invocations \
  --dimensions Name=FunctionName,Value=${ENHANCED_COMPACTION} \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Sum \
  --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
  --output table 2>/dev/null || echo "No compaction invocations"

echo ""
echo "=== S3 STORAGE CHECK ==="
echo "ChromaDB S3 Bucket Contents (recent snapshots):"
echo "Bucket: ${CHROMADB_BUCKET}"

# Check for recent snapshot activity using the Pulumi-resolved bucket name
if [ -n "$CHROMADB_BUCKET" ]; then
    echo "Recent snapshot activity:"
    aws s3 ls "s3://${CHROMADB_BUCKET}/" --recursive | grep -E "(snapshot|latest)" | tail -10 || echo "No snapshot files found"
    
    echo ""
    echo "Recent intermediate processing files:"
    aws s3 ls "s3://${CHROMADB_BUCKET}/intermediate/" --recursive | tail -5 || echo "No intermediate files found"
else
    echo "No ChromaDB bucket configured"
fi

echo ""
echo "=== SUMMARY ==="
echo "Script completed at: $(date)"
echo "For more detailed metrics, use: ./infra/get_chromadb_metrics.sh"