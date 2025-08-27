#!/bin/bash

# ChromaDB Compaction Metrics Collection Script
# Collects comprehensive metrics for all ChromaDB compaction infrastructure components

set -euo pipefail

# Help message
if [[ "${1:-}" =~ ^(-h|--help)$ ]]; then
    cat <<EOF
ChromaDB Compaction Metrics Collection Script

Usage: $0 [hours_back] [output_file]
       $0 -h|--help

Parameters:
  hours_back    Number of hours to look back (default: 24, must be positive integer)
  output_file   Save output to file instead of stdout (optional)

Environment Variables (override resource names):
  STREAM_PROCESSOR     Lambda function name for stream processor
  ENHANCED_COMPACTION  Lambda function name for enhanced compaction
  LINES_QUEUE         SQS queue name for lines processing
  WORDS_QUEUE         SQS queue name for words processing  
  LINES_DLQ           Dead letter queue for lines
  WORDS_DLQ           Dead letter queue for words
  CHROMADB_BUCKET     S3 bucket name for ChromaDB storage

Examples:
  $0                           # Last 24 hours to stdout
  $0 6                         # Last 6 hours to stdout
  $0 48 report.txt             # Last 48 hours to file
  HOURS_BACK=12 $0 12 metrics_\$(date +%Y%m%d).txt

EOF
    exit 0
fi

# Configuration
HOURS_BACK=${1:-24}
OUTPUT_FILE=${2:-""}
REGION="us-east-1"
PERIOD=3600

# Input validation
if ! [[ "${HOURS_BACK}" =~ ^[0-9]+$ ]] || [ "${HOURS_BACK}" -le 0 ]; then
    echo "Error: HOURS_BACK must be a positive integer, got: ${HOURS_BACK}" >&2
    exit 2
fi

# Validate output file path if provided
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
echo "Fetching resource names from Pulumi stack outputs..."

# Try to get Pulumi outputs, with fallback to hardcoded values if Pulumi isn't available
if command -v pulumi >/dev/null 2>&1; then
    PULUMI_OUTPUTS=$(pulumi stack output --json 2>/dev/null)
    if [ $? -eq 0 ] && [ -n "$PULUMI_OUTPUTS" ]; then
        # Extract function names from ARNs using jq (if available) or grep/awk fallback
        if command -v jq >/dev/null 2>&1; then
            STREAM_PROCESSOR_ARN=$(echo "$PULUMI_OUTPUTS" | jq -r '.stream_processor_function_arn // empty')
            ENHANCED_COMPACTION_ARN=$(echo "$PULUMI_OUTPUTS" | jq -r '.enhanced_compaction_function_arn // empty')
            CHROMADB_BUCKET_NAME=$(echo "$PULUMI_OUTPUTS" | jq -r '.chromadb_bucket_name // empty')
            
            # Extract function names from ARNs
            STREAM_PROCESSOR_DEFAULT=$(echo "$STREAM_PROCESSOR_ARN" | grep -o '[^/]*$')
            ENHANCED_COMPACTION_DEFAULT=$(echo "$ENHANCED_COMPACTION_ARN" | grep -o '[^/]*$')
            
            # Extract queue names from URLs
            LINES_QUEUE_URL=$(echo "$PULUMI_OUTPUTS" | jq -r '.chromadb_lines_queue_url // empty')
            WORDS_QUEUE_URL=$(echo "$PULUMI_OUTPUTS" | jq -r '.chromadb_words_queue_url // empty')
            LINES_QUEUE_DEFAULT=$(echo "$LINES_QUEUE_URL" | grep -o '[^/]*$')
            WORDS_QUEUE_DEFAULT=$(echo "$WORDS_QUEUE_URL" | grep -o '[^/]*$')
        else
            # Fallback without jq - use grep/sed
            STREAM_PROCESSOR_ARN=$(echo "$PULUMI_OUTPUTS" | grep -o '"stream_processor_function_arn":"[^"]*' | cut -d'"' -f4)
            ENHANCED_COMPACTION_ARN=$(echo "$PULUMI_OUTPUTS" | grep -o '"enhanced_compaction_function_arn":"[^"]*' | cut -d'"' -f4)
            CHROMADB_BUCKET_NAME=$(echo "$PULUMI_OUTPUTS" | grep -o '"chromadb_bucket_name":"[^"]*' | cut -d'"' -f4)
            
            STREAM_PROCESSOR_DEFAULT=$(echo "$STREAM_PROCESSOR_ARN" | grep -o '[^/]*$')
            ENHANCED_COMPACTION_DEFAULT=$(echo "$ENHANCED_COMPACTION_ARN" | grep -o '[^/]*$')
            
            LINES_QUEUE_URL=$(echo "$PULUMI_OUTPUTS" | grep -o '"chromadb_lines_queue_url":"[^"]*' | cut -d'"' -f4)
            WORDS_QUEUE_URL=$(echo "$PULUMI_OUTPUTS" | grep -o '"chromadb_words_queue_url":"[^"]*' | cut -d'"' -f4)
            LINES_QUEUE_DEFAULT=$(echo "$LINES_QUEUE_URL" | grep -o '[^/]*$')
            WORDS_QUEUE_DEFAULT=$(echo "$WORDS_QUEUE_URL" | grep -o '[^/]*$')
        fi
        
        echo "✓ Successfully retrieved resource names from Pulumi"
    else
        echo "⚠ Pulumi stack outputs unavailable, using fallback values"
    fi
else
    echo "⚠ Pulumi not available, using fallback values"
fi

# Resource names with Pulumi defaults (can still be overridden via environment variables)
STREAM_PROCESSOR="${STREAM_PROCESSOR:-${STREAM_PROCESSOR_DEFAULT:-chromadb-dev-lambdas-stream-processor-e79a370}}"
ENHANCED_COMPACTION="${ENHANCED_COMPACTION:-${ENHANCED_COMPACTION_DEFAULT:-chromadb-dev-lambdas-enhanced-compaction-79f6426}}"
LINES_QUEUE="${LINES_QUEUE:-${LINES_QUEUE_DEFAULT:-chromadb-dev-queues-lines-queue-b3d38e1}}"
WORDS_QUEUE="${WORDS_QUEUE:-${WORDS_QUEUE_DEFAULT:-chromadb-dev-queues-words-queue-6e2171c}}"
LINES_DLQ="${LINES_DLQ:-chromadb-dev-queues-lines-dlq-67a9812}"  # TODO: Add to Pulumi outputs
WORDS_DLQ="${WORDS_DLQ:-chromadb-dev-queues-words-dlq-0b5f487}"  # TODO: Add to Pulumi outputs
CHROMADB_BUCKET="${CHROMADB_BUCKET:-${CHROMADB_BUCKET_NAME:-chromadb-dev-shared-buckets-vectors-c239843}}"

# AWS Configuration
export AWS_DEFAULT_REGION="${REGION}"
export AWS_PAGER=""

# Time calculation (cross-platform compatible)
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS
    START_TIME=$(date -u -v-${HOURS_BACK}H +%Y-%m-%dT%H:%M:%S)
    END_TIME=$(date -u +%Y-%m-%dT%H:%M:%S)
else
    # Linux/GNU date
    START_TIME=$(date -u -d "${HOURS_BACK} hours ago" +%Y-%m-%dT%H:%M:%S)
    END_TIME=$(date -u +%Y-%m-%dT%H:%M:%S)
fi

echo "=== ChromaDB Compaction Metrics Report ==="
echo "Time Range: ${START_TIME} to ${END_TIME} (${HOURS_BACK} hours)"
echo "Generated: $(date)"
echo ""

# Lambda Function Metrics
echo "=== LAMBDA FUNCTIONS ==="

echo "Stream Processor (${STREAM_PROCESSOR}):"
echo "Invocations:"
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Invocations \
  --dimensions Name=FunctionName,Value=${STREAM_PROCESSOR} \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Sum \
  --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
  --output table

echo "Duration (ms) - Average:"
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Duration \
  --dimensions Name=FunctionName,Value=${STREAM_PROCESSOR} \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Average \
  --query 'sort_by(Datapoints[?Average != `0`], &Timestamp)[].[Timestamp,Average]' \
  --output table

echo "Errors:"
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Errors \
  --dimensions Name=FunctionName,Value=${STREAM_PROCESSOR} \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Sum \
  --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
  --output table || echo "No errors found"

echo ""
echo "Enhanced Compaction (${ENHANCED_COMPACTION}):"
echo "Invocations:"
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Invocations \
  --dimensions Name=FunctionName,Value=${ENHANCED_COMPACTION} \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Sum \
  --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
  --output table

echo "Duration (ms) - Average:"
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Duration \
  --dimensions Name=FunctionName,Value=${ENHANCED_COMPACTION} \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Average \
  --query 'sort_by(Datapoints[?Average != `0`], &Timestamp)[].[Timestamp,Average]' \
  --output table

echo "Errors:"
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Errors \
  --dimensions Name=FunctionName,Value=${ENHANCED_COMPACTION} \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Sum \
  --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
  --output table || echo "No errors found"

echo ""

# SQS Queue Metrics
echo "=== SQS QUEUES ==="

echo "Lines Queue (${LINES_QUEUE}):"
echo "Messages Sent:"
aws cloudwatch get-metric-statistics \
  --namespace AWS/SQS \
  --metric-name NumberOfMessagesSent \
  --dimensions Name=QueueName,Value=${LINES_QUEUE} \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Sum \
  --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
  --output table || echo "No messages sent"

echo "Messages Received:"
aws cloudwatch get-metric-statistics \
  --namespace AWS/SQS \
  --metric-name NumberOfMessagesReceived \
  --dimensions Name=QueueName,Value=${LINES_QUEUE} \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Sum \
  --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
  --output table || echo "No messages received"

echo ""
echo "Words Queue (${WORDS_QUEUE}):"
echo "Messages Sent:"
aws cloudwatch get-metric-statistics \
  --namespace AWS/SQS \
  --metric-name NumberOfMessagesSent \
  --dimensions Name=QueueName,Value=${WORDS_QUEUE} \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Sum \
  --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
  --output table || echo "No messages sent"

echo "Messages Received:"
aws cloudwatch get-metric-statistics \
  --namespace AWS/SQS \
  --metric-name NumberOfMessagesReceived \
  --dimensions Name=QueueName,Value=${WORDS_QUEUE} \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Sum \
  --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
  --output table || echo "No messages received"

echo ""

# Dead Letter Queues
echo "=== DEAD LETTER QUEUES ==="

echo "Lines DLQ (${LINES_DLQ}):"
aws cloudwatch get-metric-statistics \
  --namespace AWS/SQS \
  --metric-name NumberOfMessagesReceived \
  --dimensions Name=QueueName,Value=${LINES_DLQ} \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Sum \
  --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
  --output table || echo "No DLQ messages"

echo "Words DLQ (${WORDS_DLQ}):"
aws cloudwatch get-metric-statistics \
  --namespace AWS/SQS \
  --metric-name NumberOfMessagesReceived \
  --dimensions Name=QueueName,Value=${WORDS_DLQ} \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Sum \
  --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
  --output table || echo "No DLQ messages"

echo ""

# S3 Storage Metrics
echo "=== S3 STORAGE ==="

echo "ChromaDB Bucket (${CHROMADB_BUCKET}):"
echo "Current Contents:"
aws s3 ls "s3://${CHROMADB_BUCKET}" --summarize || echo "Unable to list bucket (missing or access denied)"

echo ""
echo "Bucket Structure (first 10 files):"
aws s3api list-objects-v2 --bucket "${CHROMADB_BUCKET}" --max-keys 10 --query 'Contents[].[LastModified,Size,Key]' --output table 2>/dev/null || echo "Unable to list bucket contents"

# Try to get S3 CloudWatch metrics (may be empty)
echo ""
echo "S3 CloudWatch Metrics (if available):"
aws --region us-east-1 cloudwatch get-metric-statistics \
  --namespace AWS/S3 \
  --metric-name BucketSizeBytes \
  --dimensions Name=BucketName,Value=${CHROMADB_BUCKET} Name=StorageType,Value=StandardStorage \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period 86400 \
  --statistics Average \
  --query 'sort_by(Datapoints, &Timestamp)[].[Timestamp,Average]' \
  --output table || echo "No S3 metrics available (normal for infrequent access)"

echo ""

# Custom Metrics Check
echo "=== CUSTOM METRICS ==="

# Check for custom ChromaDB compaction metrics (try multiple namespaces)
echo "Available Custom Metrics:"
aws cloudwatch list-metrics --namespace "EmbeddingWorkflow" --query 'Metrics[?contains(MetricName, `Compaction`)].[MetricName,Dimensions[0].Value]' --output table 2>/dev/null || echo "No EmbeddingWorkflow metrics found"

echo ""
echo "=== LOCK METRICS ==="

# Lock Acquisition Success
echo "Lock Acquisitions (by collection):"
aws cloudwatch get-metric-statistics \
  --namespace EmbeddingWorkflow \
  --metric-name CompactionLockAcquired \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Sum \
  --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
  --output table 2>/dev/null || echo "No lock acquisition metrics found"

# Lock Acquisition Failures  
echo "Lock Acquisition Failures (indicates contention):"
aws cloudwatch get-metric-statistics \
  --namespace EmbeddingWorkflow \
  --metric-name CompactionLockAcquisitionFailed \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Sum \
  --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
  --output table 2>/dev/null || echo "No lock failures found (good!)"

# Heartbeat Failures
echo "Heartbeat Failures (network/connectivity issues):"
aws cloudwatch get-metric-statistics \
  --namespace EmbeddingWorkflow \
  --metric-name CompactionHeartbeatFailed \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Sum \
  --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
  --output table 2>/dev/null || echo "No heartbeat failures found (good!)"

# Lock Expiration Events
echo "Lock Expiration Events (critical - indicates data race risk):"
aws cloudwatch get-metric-statistics \
  --namespace EmbeddingWorkflow \
  --metric-name CompactionLockExpired \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Sum \
  --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
  --output table 2>/dev/null || echo "No lock expirations found (good!)"

# Lock Validation Failures
echo "Lock Validation Failures (operations canceled for safety):"
aws cloudwatch get-metric-statistics \
  --namespace EmbeddingWorkflow \
  --metric-name CompactionLockValidationFailed \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Sum \
  --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
  --output table 2>/dev/null || echo "No validation failures found (good!)"

echo ""
echo "=== PROCESSING METRICS ==="

# Batch Processing Success
echo "Batch Processing Success:"
aws cloudwatch get-metric-statistics \
  --namespace EmbeddingWorkflow \
  --metric-name CompactionBatchProcessingSuccess \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Sum \
  --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
  --output table 2>/dev/null || echo "No batch processing metrics found"

# Collection Processing Errors
echo "Collection Processing Errors:"
aws cloudwatch get-metric-statistics \
  --namespace EmbeddingWorkflow \
  --metric-name CompactionCollectionProcessingError \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period ${PERIOD} \
  --statistics Sum \
  --query 'sort_by(Datapoints[?Sum != `0`], &Timestamp)[].[Timestamp,Sum]' \
  --output table 2>/dev/null || echo "No collection processing errors found (good!)"

echo ""
echo "=== SUMMARY ==="
echo "Script completed at: $(date)"
echo "Usage: $0 [hours_back] [output_file]"
echo "  hours_back: Time range in hours (default: 24)"
echo "  output_file: Save to file instead of stdout (optional)"