#!/bin/bash

# ChromaDB Compaction Metrics Collection Script
# Collects comprehensive metrics for all ChromaDB compaction infrastructure components

set -e

# Configuration
HOURS_BACK=${1:-24}
OUTPUT_FILE=${2:-""}
REGION="us-east-1"
PERIOD=3600

# If output file specified, redirect all output
if [ -n "$OUTPUT_FILE" ]; then
    exec > "$OUTPUT_FILE" 2>&1
fi

# Resource names (auto-discovered from infrastructure)
STREAM_PROCESSOR="chromadb-dev-lambdas-stream-processor-e79a370"
ENHANCED_COMPACTION="chromadb-dev-lambdas-enhanced-compaction-79f6426"
LINES_QUEUE="chromadb-dev-queues-lines-queue-b3d38e1"
WORDS_QUEUE="chromadb-dev-queues-words-queue-6e2171c"
LINES_DLQ="chromadb-dev-queues-lines-dlq-67a9812"
WORDS_DLQ="chromadb-dev-queues-words-dlq-0b5f487"
CHROMADB_BUCKET="chromadb-dev-shared-buckets-vectors-c239843"

# Time calculation (macOS compatible)
START_TIME=$(date -u -v-${HOURS_BACK}H +%Y-%m-%dT%H:%M:%S)
END_TIME=$(date -u +%Y-%m-%dT%H:%M:%S)

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
  --query 'Datapoints[?Sum != `0`].[Timestamp,Sum]' \
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
  --query 'Datapoints[?Average != `0`].[Timestamp,Average]' \
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
  --query 'Datapoints[?Sum != `0`].[Timestamp,Sum]' \
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
  --query 'Datapoints[?Sum != `0`].[Timestamp,Sum]' \
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
  --query 'Datapoints[?Average != `0`].[Timestamp,Average]' \
  --output table

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
  --query 'Datapoints[?Sum != `0`].[Timestamp,Sum]' \
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
  --query 'Datapoints[?Sum != `0`].[Timestamp,Sum]' \
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
  --query 'Datapoints[?Sum != `0`].[Timestamp,Sum]' \
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
  --query 'Datapoints[?Sum != `0`].[Timestamp,Sum]' \
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
  --query 'Datapoints[?Sum != `0`].[Timestamp,Sum]' \
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
  --query 'Datapoints[?Sum != `0`].[Timestamp,Sum]' \
  --output table || echo "No DLQ messages"

echo ""

# S3 Storage Metrics
echo "=== S3 STORAGE ==="

echo "ChromaDB Bucket (${CHROMADB_BUCKET}):"
echo "Current Contents:"
aws s3 ls s3://${CHROMADB_BUCKET} --summarize

echo ""
echo "Bucket Structure (first 10 files):"
aws s3 ls s3://${CHROMADB_BUCKET}/ --recursive 2>/dev/null | head -10 || echo "Unable to list bucket contents"

# Try to get S3 CloudWatch metrics (may be empty)
echo ""
echo "S3 CloudWatch Metrics (if available):"
aws cloudwatch get-metric-statistics \
  --namespace AWS/S3 \
  --metric-name BucketSizeBytes \
  --dimensions Name=BucketName,Value=${CHROMADB_BUCKET} Name=StorageType,Value=StandardStorage \
  --start-time ${START_TIME} \
  --end-time ${END_TIME} \
  --period 86400 \
  --statistics Average \
  --query 'Datapoints[*].[Timestamp,Average]' \
  --output table || echo "No S3 metrics available (normal for infrequent access)"

echo ""

# Custom Metrics Check
echo "=== CUSTOM METRICS ==="
aws cloudwatch list-metrics --namespace "ChromaDB/Compaction" --query 'Metrics[*].{MetricName:MetricName,Dimensions:Dimensions}' --output table || echo "No custom ChromaDB metrics found"

echo ""
echo "=== SUMMARY ==="
echo "Script completed at: $(date)"
echo "Usage: $0 [hours_back] [output_file]"
echo "  hours_back: Time range in hours (default: 24)"
echo "  output_file: Save to file instead of stdout (optional)"