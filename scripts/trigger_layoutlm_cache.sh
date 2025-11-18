#!/bin/bash
# Script to manually trigger the LayoutLM inference cache generator Lambda

set -e

STACK="${1:-dev}"

echo "🔍 Getting LayoutLM cache generator Lambda name for stack: $STACK"

# Get the Lambda function name from Pulumi outputs
LAMBDA_NAME=$(pulumi stack output --stack "$STACK" layoutlm_inference_cache_generator_lambda_name 2>/dev/null || echo "")

if [ -z "$LAMBDA_NAME" ]; then
    echo "❌ Could not find Lambda function name. Trying alternative method..."
    # Try to find it by pattern
    LAMBDA_NAME=$(aws lambda list-functions --query "Functions[?contains(FunctionName, 'layoutlm-inference-cache-generator-$STACK')].FunctionName" --output text | head -1)
fi

if [ -z "$LAMBDA_NAME" ]; then
    echo "❌ Could not find LayoutLM cache generator Lambda function"
    echo "   Make sure the infrastructure is deployed: pulumi up --stack $STACK"
    exit 1
fi

echo "✅ Found Lambda: $LAMBDA_NAME"
echo "🚀 Invoking Lambda to generate cache with latest model..."

# Invoke the Lambda
aws lambda invoke \
    --function-name "$LAMBDA_NAME" \
    --payload '{}' \
    /tmp/layoutlm-cache-response.json

echo ""
echo "📋 Response:"
cat /tmp/layoutlm-cache-response.json | python3 -m json.tool 2>/dev/null || cat /tmp/layoutlm-cache-response.json

echo ""
echo "✅ Cache generation triggered!"
echo "   The cache should be available in a few minutes."
echo "   Check CloudWatch logs: aws logs tail /aws/lambda/$LAMBDA_NAME --follow"

