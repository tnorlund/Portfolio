#!/bin/bash
# Create CloudWatch alarms for Lambda memory monitoring

FUNCTION_NAME="embedding-word-poll-lambda-dev-a18caf9"
SNS_TOPIC_ARN="arn:aws:sns:us-east-1:681647709217:lambda-alerts" # Replace with your SNS topic

echo "Creating CloudWatch alarms for $FUNCTION_NAME..."

# 1. High Duration Alarm (indicates potential memory pressure)
aws cloudwatch put-metric-alarm \
    --alarm-name "${FUNCTION_NAME}-high-duration" \
    --alarm-description "Lambda function duration is high (potential memory pressure)" \
    --metric-name Duration \
    --namespace AWS/Lambda \
    --statistic Average \
    --period 300 \
    --threshold 10000 \
    --comparison-operator GreaterThanThreshold \
    --evaluation-periods 2 \
    --alarm-actions "$SNS_TOPIC_ARN" \
    --dimensions Name=FunctionName,Value="$FUNCTION_NAME"

# 2. Error Rate Alarm
aws cloudwatch put-metric-alarm \
    --alarm-name "${FUNCTION_NAME}-error-rate" \
    --alarm-description "Lambda function error rate is high" \
    --metric-name Errors \
    --namespace AWS/Lambda \
    --statistic Sum \
    --period 300 \
    --threshold 5 \
    --comparison-operator GreaterThanThreshold \
    --evaluation-periods 1 \
    --alarm-actions "$SNS_TOPIC_ARN" \
    --dimensions Name=FunctionName,Value="$FUNCTION_NAME"

# 3. Throttles Alarm
aws cloudwatch put-metric-alarm \
    --alarm-name "${FUNCTION_NAME}-throttles" \
    --alarm-description "Lambda function is being throttled" \
    --metric-name Throttles \
    --namespace AWS/Lambda \
    --statistic Sum \
    --period 300 \
    --threshold 1 \
    --comparison-operator GreaterThanThreshold \
    --evaluation-periods 1 \
    --alarm-actions "$SNS_TOPIC_ARN" \
    --dimensions Name=FunctionName,Value="$FUNCTION_NAME"

echo "Alarms created successfully!"
echo "Note: AWS doesn't provide a direct 'MemoryUtilization' metric, but high duration often indicates memory pressure."