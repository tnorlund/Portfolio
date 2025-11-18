#!/bin/bash
# Script to verify LayoutLM inference API is working

set -e

STACK="${1:-dev}"

echo "🔍 Verifying LayoutLM inference API setup for stack: $STACK"
echo ""

# 1. Check if cache generator Lambda exists
echo "1️⃣  Checking cache generator Lambda..."
LAMBDA_NAME=$(pulumi stack output --stack "$STACK" layoutlm_inference_cache_generator_lambda_name 2>/dev/null || echo "")
if [ -z "$LAMBDA_NAME" ]; then
    LAMBDA_NAME=$(aws lambda list-functions --query "Functions[?contains(FunctionName, 'layoutlm-inference-cache-generator-$STACK')].FunctionName" --output text | head -1)
fi

if [ -z "$LAMBDA_NAME" ]; then
    echo "   ❌ Cache generator Lambda not found"
    echo "   Make sure pulumi up completed successfully"
    exit 1
else
    echo "   ✅ Found: $LAMBDA_NAME"
fi

# 2. Check if cache bucket exists
echo ""
echo "2️⃣  Checking cache bucket..."
BUCKET=$(pulumi stack output --stack "$STACK" layoutlm_inference_cache_bucket 2>/dev/null || echo "")
if [ -z "$BUCKET" ]; then
    echo "   ❌ Cache bucket not found in Pulumi outputs"
    exit 1
else
    echo "   ✅ Found: $BUCKET"
fi

# 3. Check if cache exists
echo ""
echo "3️⃣  Checking if cache exists..."
CACHE_EXISTS=$(aws s3 ls "s3://$BUCKET/layoutlm-inference-cache/latest.json" 2>/dev/null && echo "yes" || echo "no")
if [ "$CACHE_EXISTS" = "yes" ]; then
    echo "   ✅ Cache exists"
    CACHE_TIME=$(aws s3 ls "s3://$BUCKET/layoutlm-inference-cache/latest.json" --recursive | awk '{print $1, $2}')
    echo "   📅 Last updated: $CACHE_TIME"
else
    echo "   ⚠️  Cache not found - will be generated on next Lambda run"
fi

# 4. Check API Lambda
echo ""
echo "4️⃣  Checking API Lambda..."
API_LAMBDA=$(pulumi stack output --stack "$STACK" layoutlm_inference_lambda_name 2>/dev/null || echo "")
if [ -z "$API_LAMBDA" ]; then
    API_LAMBDA=$(aws lambda list-functions --query "Functions[?contains(FunctionName, 'api_layoutlm_inference')].FunctionName" --output text | head -1)
fi

if [ -z "$API_LAMBDA" ]; then
    echo "   ❌ API Lambda not found"
    exit 1
else
    echo "   ✅ Found: $API_LAMBDA"
fi

# 5. Check API Gateway endpoint
echo ""
echo "5️⃣  Checking API Gateway endpoint..."
API_URL=$(pulumi stack output --stack "$STACK" api_gateway_url 2>/dev/null || echo "")
if [ -z "$API_URL" ]; then
    echo "   ⚠️  API Gateway URL not found in outputs"
else
    echo "   ✅ API URL: $API_URL"
    echo "   📍 Endpoint: $API_URL/layoutlm_inference"
fi

# 6. Check recent Lambda invocations
echo ""
echo "6️⃣  Checking recent cache generator runs..."
LAST_INVOCATION=$(aws logs filter-log-events \
    --log-group-name "/aws/lambda/$LAMBDA_NAME" \
    --limit 1 \
    --query 'events[0].message' \
    --output text 2>/dev/null || echo "No logs found")

if [ -n "$LAST_INVOCATION" ] && [ "$LAST_INVOCATION" != "None" ]; then
    echo "   ✅ Recent logs found"
    echo "   📋 Last log: ${LAST_INVOCATION:0:100}..."
else
    echo "   ⚠️  No recent logs (Lambda may not have run yet)"
fi

# Summary
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📋 Summary"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ "$CACHE_EXISTS" = "no" ]; then
    echo ""
    echo "⚠️  Cache not found. To generate it:"
    echo "   ./scripts/trigger_layoutlm_cache.sh $STACK"
    echo ""
    echo "   Or wait for the scheduled run (every 2 minutes)"
fi

echo ""
echo "🧪 To test the API:"
if [ -n "$API_URL" ]; then
    echo "   curl \"$API_URL/layoutlm_inference\""
else
    echo "   Get API URL: pulumi stack output --stack $STACK api_gateway_url"
fi

echo ""
echo "📊 To check logs:"
echo "   aws logs tail /aws/lambda/$LAMBDA_NAME --follow"

echo ""
echo "✅ Verification complete!"

