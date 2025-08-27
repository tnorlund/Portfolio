#!/bin/bash

# Embedding Step Functions Metrics Collection Script
# Collects metrics for the embedding step functions infrastructure

set -euo pipefail

# Configuration
HOURS_BACK=${1:-6}
REGION="us-east-1"
PERIOD=3600

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

# Check recent executions for each step function
for sf_arn in $(aws stepfunctions list-state-machines --query 'stateMachines[?contains(name, `receipt_processor`)].stateMachineArn' --output text); do
    sf_name=$(echo $sf_arn | cut -d: -f7)
    echo "Recent executions for ${sf_name}:"
    aws stepfunctions list-executions --state-machine-arn "$sf_arn" --max-items 5 --query "executions[].{name:name,status:status,start:startDate,end:stopDate}" --output table 2>/dev/null || echo "No recent executions"
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

# Get embedding function names from Pulumi outputs
if command -v pulumi >/dev/null 2>&1; then
    PULUMI_OUTPUTS=$(pulumi stack output --json 2>/dev/null)
    if [ $? -eq 0 ] && [ -n "$PULUMI_OUTPUTS" ]; then
        if command -v jq >/dev/null 2>&1; then
            ENHANCED_COMPACTION_ARN=$(echo "$PULUMI_OUTPUTS" | jq -r '.enhanced_compaction_function_arn // empty')
            compact_function=$(echo "$ENHANCED_COMPACTION_ARN" | grep -o '[^/]*$')
        else
            ENHANCED_COMPACTION_ARN=$(echo "$PULUMI_OUTPUTS" | grep -o '"enhanced_compaction_function_arn":"[^"]*' | cut -d'"' -f4)
            compact_function=$(echo "$ENHANCED_COMPACTION_ARN" | grep -o '[^/]*$')
        fi
    fi
fi

echo ""
echo "=== COMPACTION FUNCTION METRICS ==="
compact_function="${compact_function:-embedding-vector-compact-lambda-dev-bd077b7}"
echo "Compaction Function: ${compact_function}"
echo "Recent Invocations:"
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Invocations \
  --dimensions Name=FunctionName,Value=${compact_function} \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Sum \
  --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
  --output table 2>/dev/null || echo "No compaction invocations"

echo ""
echo "=== S3 STORAGE CHECK ==="
echo "ChromaDB S3 Bucket Contents (recent snapshots):"
# Try to find ChromaDB bucket
chromadb_bucket=$(aws s3api list-buckets --query 'Buckets[?contains(Name, `chromadb`) || contains(Name, `vectors`)].Name' --output text | head -1)
if [ -n "$chromadb_bucket" ]; then
    echo "Bucket: $chromadb_bucket"
    echo "Recent snapshot activity:"
    aws s3 ls "s3://${chromadb_bucket}/" --recursive | grep -E "(snapshot|latest)" | tail -10 || echo "No snapshot files found"
else
    echo "No ChromaDB bucket found"
fi

echo ""
echo "=== SUMMARY ==="
echo "Script completed at: $(date)"
echo "For more detailed metrics, use: ./infra/get_chromadb_metrics.sh"