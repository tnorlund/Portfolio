#!/bin/bash
# Setup script for end-to-end tests
# This script helps set up the environment variables needed for e2e tests

set -e

echo "🔧 Setting up environment for end-to-end tests..."

# Check if Pulumi is installed
if ! command -v pulumi &> /dev/null; then
    echo "❌ Pulumi CLI not found. Please install Pulumi first."
    echo "   Visit: https://www.pulumi.com/docs/get-started/install/"
    exit 1
fi

# Default to dev stack, allow override
STACK=${1:-dev}
PULUMI_STACK="tnorlund/portfolio/$STACK"

echo "📦 Using Pulumi stack: $PULUMI_STACK"

# Select the stack
pulumi stack select "$PULUMI_STACK" 2>/dev/null || {
    echo "❌ Could not select stack $PULUMI_STACK"
    echo "   Make sure you have access to this stack and are logged in to Pulumi"
    echo "   Run: pulumi login"
    exit 1
}

# Get outputs from Pulumi
echo "🔍 Retrieving configuration from Pulumi..."

DYNAMODB_TABLE_NAME=$(pulumi stack output dynamodb_table_name 2>/dev/null || echo "")

if [ -z "$DYNAMODB_TABLE_NAME" ]; then
    echo "❌ Could not retrieve DynamoDB table name from Pulumi"
    exit 1
fi

# Export environment variables
export DYNAMODB_TABLE_NAME
export AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-us-east-1}

echo "✅ Environment variables set:"
echo "   DYNAMODB_TABLE_NAME=$DYNAMODB_TABLE_NAME"
echo "   AWS_DEFAULT_REGION=$AWS_DEFAULT_REGION"

# Check AWS credentials
echo ""
echo "🔐 Checking AWS credentials..."
if aws sts get-caller-identity &>/dev/null; then
    AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
    echo "✅ AWS credentials valid (Account: $AWS_ACCOUNT)"
else
    echo "❌ AWS credentials not found or invalid"
    echo "   Configure with: aws configure"
    exit 1
fi

echo ""
echo "🎉 Environment ready for end-to-end tests!"
echo ""
echo "Run tests with:"
echo "  pytest -m end_to_end"
echo ""
echo "Or source this script to use the environment variables:"
echo "  source setup_env.sh"
