#!/bin/bash
# Script to test the FastLambdaLayer component independently

set -e

echo "🧪 Testing FastLambdaLayer Component"
echo "===================================="

# Check if we're in the infra directory
if [ ! -f "fast_lambda_layer.py" ]; then
    echo "❌ Error: Must run from the infra/ directory"
    exit 1
fi

# Get current git branch name for isolation
BRANCH_NAME=$(git rev-parse --abbrev-ref HEAD 2>/dev/null | sed 's/[^a-zA-Z0-9-]/-/g' | tr '[:upper:]' '[:lower:]' || echo "nobranch")
USER_NAME=$(whoami | sed 's/[^a-zA-Z0-9-]/-/g' | tr '[:upper:]' '[:lower:]')

# Parse command line arguments
# Default stack name includes branch and user for complete isolation
STACK_NAME="test-layer-${USER_NAME}-${BRANCH_NAME}"
SYNC_MODE="false"
FORCE_REBUILD="false"
DESTROY_AFTER="false"

while [[ $# -gt 0 ]]; do
    case $1 in
        --sync)
            SYNC_MODE="true"
            shift
            ;;
        --force-rebuild)
            FORCE_REBUILD="true"
            shift
            ;;
        --destroy-after)
            DESTROY_AFTER="true"
            shift
            ;;
        --stack)
            STACK_NAME="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--sync] [--force-rebuild] [--destroy-after] [--stack NAME]"
            exit 1
            ;;
    esac
done

echo "Configuration:"
echo "  Stack: $STACK_NAME"
echo "  Sync Mode: $SYNC_MODE"
echo "  Force Rebuild: $FORCE_REBUILD"
echo "  Destroy After: $DESTROY_AFTER"
echo ""

# Initialize the stack if it doesn't exist
if ! pulumi stack ls --json | jq -r '.[].name' | grep -q "^$STACK_NAME$"; then
    echo "📦 Creating new stack: $STACK_NAME"
    pulumi stack init $STACK_NAME
else
    echo "✅ Using existing stack: $STACK_NAME"
    pulumi stack select $STACK_NAME
fi

# Set configuration
echo "⚙️  Setting configuration..."
pulumi config set aws:region us-east-1
pulumi config set lambda-layer:sync-mode $SYNC_MODE
pulumi config set lambda-layer:force-rebuild $FORCE_REBUILD
pulumi config set test-package receipt_dynamo

# Run pulumi up
echo ""
echo "🚀 Running pulumi up..."
pulumi up --yes --stack $STACK_NAME -f test_fast_lambda_layer.py

# Get outputs
echo ""
echo "📊 Stack outputs:"
pulumi stack output --json

# Optional: Test the Lambda function
echo ""
echo "🧪 Testing Lambda function..."
LAMBDA_NAME=$(pulumi stack output test_lambda_name 2>/dev/null | tr -d '"')

if [ ! -z "$LAMBDA_NAME" ]; then
    echo "Invoking Lambda function: $LAMBDA_NAME"
    aws lambda invoke \
        --function-name $LAMBDA_NAME \
        --payload '{"test": true}' \
        /tmp/lambda-response.json \
        --cli-binary-format raw-in-base64-out
    
    echo "Lambda response:"
    cat /tmp/lambda-response.json
    echo ""
fi

# Cleanup if requested
if [ "$DESTROY_AFTER" = "true" ]; then
    echo ""
    echo "🧹 Cleaning up..."
    pulumi destroy --yes --stack $STACK_NAME
    pulumi stack rm $STACK_NAME --yes
fi

echo ""
echo "✅ Test complete!"